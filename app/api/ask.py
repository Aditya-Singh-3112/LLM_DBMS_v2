import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from app.agent.agent_factory import AgentFactory
from app.agent.tool_service_adapter import ToolServiceAdapter
from app.api.dependencies import get_current_user
from app.core.config import Settings, get_settings
from app.core.database import DatabaseManager
from app.models.auth import UserResponse
from app.models.contracts import (
    AskRequest,
    AskResponse,
    GroundedReference,
    PendingWrite,
    QueryResult,
    SqlConfirmRequest,
)
from app.models.mcp_tools import RunSqlRequest
from app.models.tool_call_log import ToolCallEntry, ToolCallSource, ToolCallStatus
from app.services.ask_quota import AskQuota
from app.services.cache_service import CacheService
from app.services.conversation_service import ConversationService
from app.services.database_registry import DatabaseRegistryService
from app.services.mcp_tools import MCPToolService
from app.services.permission_service import PermissionService
from app.services.postgres_executor import PostgresExecutor
from app.services.rag_service import RAGService
from app.services.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/databases", tags=["ask"])


def get_ask_dependencies(request: Request) -> dict:
    """Dependency for ask endpoints."""
    db_manager: DatabaseManager = request.app.state.database_manager

    if db_manager.mongo_database is None:
        raise RuntimeError("MongoDB is not initialized")

    if db_manager.postgres_pool is None:
        raise RuntimeError("Postgres pool is not initialized")

    return {
        "db_manager": db_manager,
        "settings": get_settings(),
        "mongo_db": db_manager.mongo_database,
        "rag_service": request.app.state.rag_service,
    }


@dataclass
class _AskSession:
    executor: object
    tool_client: ToolServiceAdapter
    agent_factory: AgentFactory
    conversations: ConversationService
    chat_history: list
    start_time: float


async def _prepare(database_id: str, ask_request: AskRequest, user: UserResponse, deps: dict) -> _AskSession:
    """Everything both /ask routes need before invoking the agent."""
    start_time = time.time()
    db_manager: DatabaseManager = deps["db_manager"]
    settings: Settings = deps["settings"]
    mongo_db = deps["mongo_db"]
    rag_service: RAGService = deps["rag_service"]

    # Fail fast with 403/404/429 before spending an LLM call.
    await DatabaseRegistryService(mongo_database=mongo_db, database_manager=db_manager).get_access_level(
        database_id=database_id, user_id=user.id
    )
    await AskQuota(db_manager.redis, settings.ask_daily_limit).consume(user.id)

    tool_client = ToolServiceAdapter(service=_build_tool_service(deps), user_id=user.id)
    agent_factory = AgentFactory(
        tool_client=tool_client,
        rag_service=rag_service,
        google_api_key=settings.google_api_key,
    )
    executor = await agent_factory.create_agent_executor(database_id=database_id)

    conversations = ConversationService(mongo_db)
    if ask_request.reset:
        await conversations.clear(user.id, database_id)
    chat_history = await conversations.get_history(user.id, database_id)

    return _AskSession(executor, tool_client, agent_factory, conversations, chat_history, start_time)


async def _finish(
    session: _AskSession,
    database_id: str,
    user: UserResponse,
    question: str,
    output,
    intermediate_steps: list,
) -> AskResponse:
    """Turn the agent's raw output into an AskResponse and persist the turn."""
    answer = _content_to_text(output)
    tool_calls = _to_tool_call_entries(intermediate_steps)

    await session.conversations.append(user.id, database_id, question, answer)

    grounded_on = None
    rag_tool = session.agent_factory.rag_tool
    if rag_tool and rag_tool.retrieved_passages:
        seen: set[tuple[str, str | None]] = set()
        grounded_on = []
        for passage in rag_tool.retrieved_passages:
            key = (passage.source, passage.chapter)
            if key in seen:
                continue
            seen.add(key)
            grounded_on.append(GroundedReference(source=passage.source, chapter=passage.chapter))

    pending_write = None
    for call in reversed(tool_calls):
        if call.status == ToolCallStatus.PENDING:
            pending_write = PendingWrite(sql=call.args.get("sql", ""))
            break

    query_result = None
    last = session.tool_client.last_run_sql_result
    if last is not None:
        query_result = QueryResult(columns=last.get("columns") or [], rows=last.get("rows") or [])

    return AskResponse(
        answer=answer,
        sql=_extract_sql_from_tool_calls(tool_calls),
        result=query_result,
        tool_calls=tool_calls,
        grounded_on=grounded_on,
        pending_write=pending_write,
        total_execution_time_ms=int((time.time() - session.start_time) * 1000),
    )


@router.post("/{database_id}/ask", response_model=AskResponse)
async def ask_database(
    database_id: str,
    ask_request: AskRequest,
    current_user: UserResponse = Depends(get_current_user),
    deps: dict = Depends(get_ask_dependencies),
) -> AskResponse:
    """
    Ask a natural language question about a database and wait for the
    complete answer. See /ask/stream for incremental delivery.
    """
    session = await _prepare(database_id, ask_request, current_user, deps)
    settings: Settings = deps["settings"]

    try:
        result = await asyncio.wait_for(
            session.executor.ainvoke({"input": ask_request.query, "chat_history": session.chat_history}),
            timeout=settings.ask_timeout_seconds,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"The assistant did not finish within {settings.ask_timeout_seconds} seconds",
        )
    except Exception as e:
        status_code, detail = _classify_agent_error(e)
        logger.exception("Agent execution failed for database %s", database_id)
        raise HTTPException(status_code=status_code, detail=detail)

    return await _finish(
        session, database_id, current_user, ask_request.query,
        result.get("output", ""), result.get("intermediate_steps", []),
    )


@router.post("/{database_id}/ask/stream")
async def ask_database_stream(
    database_id: str,
    ask_request: AskRequest,
    current_user: UserResponse = Depends(get_current_user),
    deps: dict = Depends(get_ask_dependencies),
) -> StreamingResponse:
    """
    Same as /ask, delivered as server-sent events:

      event: tool_start  data: {"tool": ..., "args": {...}}
      event: tool_end    data: ToolCallEntry
      event: token       data: {"text": "..."}
      event: done        data: AskResponse
      event: error       data: {"detail": "..."}
    """
    session = await _prepare(database_id, ask_request, current_user, deps)
    settings: Settings = deps["settings"]

    async def events() -> AsyncIterator[bytes]:
        steps: list = []
        output = ""
        try:
            async with asyncio.timeout(settings.ask_timeout_seconds):
                async for event in session.executor.astream_events(
                    {"input": ask_request.query, "chat_history": session.chat_history},
                    version="v2",
                ):
                    kind = event["event"]
                    if kind == "on_tool_start":
                        yield _sse("tool_start", {"tool": event["name"], "args": event["data"].get("input")})
                    elif kind == "on_tool_end":
                        action = _Action(event["name"], event["data"].get("input") or {})
                        steps.append((action, event["data"].get("output")))
                        yield _sse("tool_end", _to_tool_call_entries([steps[-1]])[0].model_dump(mode="json"))
                    elif kind == "on_chat_model_stream":
                        text = _content_to_text(event["data"]["chunk"].content)
                        if text:
                            yield _sse("token", {"text": text})
                    elif kind == "on_chain_end" and event["name"] == "AgentExecutor":
                        output = event["data"]["output"].get("output", "")
        except TimeoutError:
            yield _sse("error", {"detail": f"The assistant did not finish within {settings.ask_timeout_seconds} seconds"})
            return
        except Exception as e:
            _, detail = _classify_agent_error(e)
            logger.exception("Streaming agent execution failed for database %s", database_id)
            yield _sse("error", {"detail": detail})
            return

        response = await _finish(session, database_id, current_user, ask_request.query, output, steps)
        yield _sse("done", response.model_dump(mode="json"))

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{database_id}/sql/confirm", response_model=QueryResult)
async def confirm_sql(
    database_id: str,
    body: SqlConfirmRequest,
    current_user: UserResponse = Depends(get_current_user),
    deps: dict = Depends(get_ask_dependencies),
) -> QueryResult:
    """
    Execute a write statement the agent proposed, after the user confirmed
    it in the UI. Runs through the same validation and permission checks.
    """
    tool_service = _build_tool_service(deps)
    result = await tool_service.run_sql(
        RunSqlRequest(database_id=database_id, sql=body.sql, confirmed=True),
        user_id=current_user.id,
    )
    await ConversationService(deps["mongo_db"]).append(
        current_user.id, database_id,
        f"[User confirmed and ran] {body.sql}",
        f"Executed. {len(result.rows or [])} rows returned.",
    )
    return QueryResult(columns=result.columns or [], rows=result.rows or [])


@router.post("/{database_id}/conversation/clear", status_code=status.HTTP_204_NO_CONTENT)
async def clear_conversation(
    database_id: str,
    current_user: UserResponse = Depends(get_current_user),
    deps: dict = Depends(get_ask_dependencies),
) -> None:
    """Forget the chat history for this user and database."""
    await ConversationService(deps["mongo_db"]).clear(current_user.id, database_id)


# ---------------------------------------------------------------- helpers

@dataclass
class _Action:
    """Minimal stand-in for LangChain's AgentAction used by the stream path."""
    tool: str
    tool_input: dict


def _classify_agent_error(e: Exception) -> tuple[int, str]:
    """Map LLM provider failures to something the user can act on."""
    text = str(e)
    if "RESOURCE_EXHAUSTED" in text or "429" in text[:40]:
        return (
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "The language model is rate-limited right now. Please wait a minute and try again.",
        )
    if "API key" in text or "PERMISSION_DENIED" in text or "UNAUTHENTICATED" in text:
        return status.HTTP_503_SERVICE_UNAVAILABLE, "The language model rejected the server's API key."
    return status.HTTP_500_INTERNAL_SERVER_ERROR, "Agent execution failed"


def _sse(event: str, data) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n".encode()


def _build_tool_service(deps: dict) -> MCPToolService:
    db_manager: DatabaseManager = deps["db_manager"]
    mongo_db = deps["mongo_db"]
    registry_service = DatabaseRegistryService(mongo_database=mongo_db, database_manager=db_manager)
    return MCPToolService(
        postgres_executor=PostgresExecutor(db_manager),
        permission_service=PermissionService(registry_service),
        mongo_database=mongo_db,
        cache_service=CacheService(redis=db_manager.redis),
        rate_limiter=RateLimiter(redis=db_manager.redis, calls_per_minute=60),
    )


def _content_to_text(content) -> str:
    """
    Flatten an LLM message body to plain text.

    Gemini returns the final answer as a list of content blocks
    (`[{"type": "text", "text": ...}, ...]`) rather than a string.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts)
    if content is None:
        return ""
    return str(content)


def _to_tool_call_entries(intermediate_steps: list) -> list[ToolCallEntry]:
    """
    Convert LangChain intermediate steps to response entries.

    Persistent logging happens inside MCPToolService, so nothing is written
    to Mongo here.
    """
    tool_calls: list[ToolCallEntry] = []

    for action, observation in intermediate_steps:
        text = _content_to_text(getattr(observation, "content", observation))
        if text.startswith("CONFIRMATION_REQUIRED"):
            call_status = ToolCallStatus.PENDING
        elif text.startswith("Tool call failed"):
            call_status = ToolCallStatus.ERROR
        else:
            call_status = ToolCallStatus.SUCCESS

        tool_calls.append(
            ToolCallEntry(
                tool_name=action.tool,
                args=action.tool_input,
                result=text,
                status=call_status,
                duration_ms=0,
                timestamp=datetime.now(timezone.utc),
                source=(
                    ToolCallSource.RAG
                    if action.tool == "sql_reference_lookup"
                    else ToolCallSource.MCP
                ),
            )
        )

    return tool_calls


def _extract_sql_from_tool_calls(tool_calls: list[ToolCallEntry]) -> str | None:
    """Return the SQL of the last successful run_sql call, if any."""
    for call in reversed(tool_calls):
        if call.tool_name == "run_sql" and call.status == ToolCallStatus.SUCCESS:
            return call.args.get("sql")
    return None
