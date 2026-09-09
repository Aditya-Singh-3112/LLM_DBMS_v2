import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.dependencies import get_current_user
from app.core.database import DatabaseManager
from app.models.auth import UserResponse
from app.models.contracts import AskRequest, AskResponse
from app.models.tool_call_log import ToolCallSource, ToolCallStatus
from app.services.database_registry import DatabaseRegistryService
from app.services.rag_service import RAGService
from app.mcp_server.client import MCPClientWrapper
from app.agent.agent_factory import AgentFactory


router = APIRouter(prefix="/databases", tags=["ask"])


def get_ask_dependencies(request: Request):
    """Dependency for ask endpoint."""
    db_manager: DatabaseManager = request.app.state.database_manager

    if db_manager.mongo_database is None:
        raise RuntimeError("MongoDB is not initialized")

    if db_manager.postgres_pool is None:
        raise RuntimeError("Postgres pool is not initialized")

    from app.core.config import get_settings

    settings = get_settings()

    return {
        "db_manager": db_manager,
        "settings": settings,
        "mongo_db": db_manager.mongo_database,
    }


@router.post("/{database_id}/ask", response_model=AskResponse)
async def ask_database(database_id: str, ask_request: AskRequest, current_user: UserResponse = Depends(get_current_user), deps=Depends(get_ask_dependencies),) -> AskResponse:
    """
    Ask a natural language question about a database.
    
    The agent will:
    1. Inspect the database schema
    2. Look up relevant SQL concepts if needed
    3. Generate and execute SQL
    4. Return results and explain the query
    """
    start_time = time.time()

    db_manager = deps["db_manager"]
    settings = deps["settings"]
    mongo_db = deps["mongo_db"]

    registry_service = DatabaseRegistryService(
        mongo_database=mongo_db,
        database_manager=db_manager,
    )

    try:
        access_level = await registry_service.get_access_level(
            database_id=database_id,
            user_id=current_user.id,
        )
    except HTTPException as e:
        if e.status_code == status.HTTP_403_FORBIDDEN:
            raise
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Database not found",
        )

    rag_service = RAGService(
        database_manager=db_manager,
        redis=db_manager.redis,
    )

    await rag_service.initialize()

    mcp_client = MCPClientWrapper(
        session=db_manager.mcp_session,
        user_id=current_user.id,
    )

    agent_factory = AgentFactory(
        mcp_client=mcp_client,
        rag_service=rag_service,
        google_api_key=settings.google_api_key,
    )

    executor = await agent_factory.create_agent_executor(
        database_id=database_id,
        max_iterations=6,
    )

    try:
        result = await executor.ainvoke(
            {
                "input": ask_request.query,
            }
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Agent execution failed: {str(e)}",
        )

    answer = result.get("output", "")
    intermediate_steps = result.get("intermediate_steps", [])

    tool_calls = await _process_intermediate_steps(
        intermediate_steps,
        current_user.id,
        database_id,
        mongo_db,
    )

    grounded_on = _extract_grounded_passages(tool_calls, intermediate_steps)

    total_execution_time_ms = int((time.time() - start_time) * 1000)

    return AskResponse(
        answer=answer,
        sql=_extract_sql_from_tool_calls(tool_calls),
        result=None,
        tool_calls=tool_calls,
        grounded_on=grounded_on,
        total_execution_time_ms=total_execution_time_ms,
    )


async def _process_intermediate_steps(
    intermediate_steps: list,
    user_id: str,
    database_id: str,
    mongo_db,
) -> list:
    """Convert intermediate steps to tool_call_logs."""
    from app.models.tool_call_log import ToolCallEntry

    tool_calls = []

    for step in intermediate_steps:
        action, observation = step

        tool_call_entry = ToolCallEntry(
            tool_name=action.tool,
            args=action.tool_input,
            result=observation,
            status=ToolCallStatus.SUCCESS,
            duration_ms=0,
            timestamp=datetime.now(timezone.utc),
            source=(
                ToolCallSource.RAG
                if action.tool == "sql_reference_lookup"
                else ToolCallSource.MCP
            ),
        )

        tool_calls.append(tool_call_entry)

        await mongo_db["tool_call_logs"].insert_one(
            {
                "user_id": user_id,
                "database_id": database_id,
                **tool_call_entry.model_dump(),
            }
        )

    return tool_calls


def _extract_sql_from_tool_calls(tool_calls: list) -> str | None:
    """Extract the SQL query if run_sql was called."""
    for call in tool_calls:
        if call.tool_name == "run_sql":
            return call.args.get("sql")
    return None


def _extract_grounded_passages(tool_calls: list, intermediate_steps: list) -> list | None:
    """Extract passages cited in sql_reference_lookup calls."""
    from app.models.contracts import GroundedReference

    grounded = []

    for call in tool_calls:
        if call.tool_name == "sql_reference_lookup":
            observation = call.result

            if isinstance(observation, str) and "source" in observation:
                grounded.append(
                    GroundedReference(
                        source="Database Textbook",
                        chapter=None,
                    )
                )

    return grounded if grounded else None