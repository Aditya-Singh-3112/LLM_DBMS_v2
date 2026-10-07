import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from langchain_core.callbacks import UsageMetadataCallbackHandler

from app.agent.agent_factory import AgentFactory
from app.agent.mcp_client import MCPToolClient, MCPUnavailableError
from app.api.dependencies import get_current_user
from app.core.config import Settings, get_settings
from app.core.database import DatabaseManager
from app.core.metrics import ASK_REQUESTS, LLM_TOKENS
from app.core.security import create_access_token
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
from app.services.conversation_service import ConversationService
from app.services.database_registry import DatabaseRegistryService
from app.services.mcp_tools import build_tool_service
from app.services.usage_service import UsageService

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
        # Tests set this to reach an in-process MCP server; None means HTTP.
        "mcp_transport": getattr(request.app.state, "mcp_transport", None),
    }


@dataclass
class _AskSession:
    conversations: ConversationService
    conversation_id: str | None
    chat_history: list
    start_time: float
    usage: UsageMetadataCallbackHandler
    usage_service: UsageService

    @property
    def agent_config(self) -> dict:
        return {"callbacks": [self.usage]}


async def _prepare(database_id: str, ask_request: AskRequest, user: UserResponse, deps: dict) -> _AskSession:
    """Everything both /ask routes need before invoking the agent."""
    start_time = time.time()
    db_manager: DatabaseManager = deps["db_manager"]
    settings: Settings = deps["settings"]
    mongo_db = deps["mongo_db"]

    # Fail fast with 403/404/429 before spending an LLM call.
    await DatabaseRegistryService(mongo_database=mongo_db, database_manager=db_manager).get_access_level(
        database_id=database_id, user_id=user.id
    )
    usage_service = UsageService(mongo_db)
    await usage_service.check_daily_limit(user.id, settings.ask_daily_token_limit)
    conversations = ConversationService(mongo_db)
    # 404 for someone else's (or a mistyped) conversation, before any cost.
    chat_history = await conversations.get_history(user.id, database_id, ask_request.conversation_id)
    await AskQuota(db_manager.redis, settings.ask_daily_limit).consume(user.id)

    return _AskSession(
        conversations=conversations,
        conversation_id=ask_request.conversation_id,
        chat_history=chat_history,
        start_time=start_time,
        usage=UsageMetadataCallbackHandler(),
        usage_service=usage_service,
    )


async def _record_usage(session: _AskSession, user: UserResponse, outcome: str) -> None:
    """Count the tokens this request spent, whether or not it succeeded."""
    ASK_REQUESTS.labels(outcome).inc()
    input_tokens = sum(u.get("input_tokens", 0) for u in session.usage.usage_metadata.values())
    output_tokens = sum(u.get("output_tokens", 0) for u in session.usage.usage_metadata.values())
    LLM_TOKENS.labels("input").inc(input_tokens)
    LLM_TOKENS.labels("output").inc(output_tokens)
    try:
        await session.usage_service.record(user.id, input_tokens, output_tokens)
    except Exception:
        logger.exception("Could not record LLM usage for user %s", user.id)


@asynccontextmanager
async def _open_agent(database_id: str, user: UserResponse, deps: dict) -> AsyncIterator[tuple]:
    """
    An MCP session for `user` and an agent whose tools are that session's.
    Must be entered and exited in one task (see MCPToolClient).
    """
    settings: Settings = deps["settings"]
    # A fresh token rather than the caller's, which may expire mid-request.
    # It identifies the same, already authenticated user.
    token, _ = create_access_token(user.id, settings)
    async with MCPToolClient(
        url=settings.mcp_server_url,
        access_token=token,
        timeout_seconds=settings.ask_timeout_seconds,
        transport=deps["mcp_transport"],
    ) as mcp_client:
        agent_factory = AgentFactory(mcp_client=mcp_client, google_api_key=settings.google_api_key)
        executor = await agent_factory.create_agent_executor(database_id=database_id)
        yield executor, mcp_client


async def _finish(
    session: _AskSession,
    mcp_client: MCPToolClient,
    database_id: str,
    user: UserResponse,
    question: str,
    output,
    intermediate_steps: list,
) -> AskResponse:
    """Turn the agent's raw output into an AskResponse and persist the turn."""
    answer = _content_to_text(output)
    tool_calls = _to_tool_call_entries(intermediate_steps)
    sql = _extract_sql_from_tool_calls(tool_calls)

    conversation_id = await session.conversations.append(
        user.id, database_id, session.conversation_id, question, answer, sql
    )

    grounded_on = None
    if mcp_client.retrieved_passages:
        seen: set[tuple[str, str | None]] = set()
        grounded_on = []
        for passage in mcp_client.retrieved_passages:
            key = (passage.source, passage.chapter)
            if key in seen:
                continue
            seen.add(key)
            grounded_on.append(GroundedReference(source=passage.source, chapter=passage.chapter))

    pending_write = None
    for call in reversed(tool_calls):
        if call.status == ToolCallStatus.PENDING:
            pending_sql = call.args.get("sql", "")
            details = mcp_client.pending_writes.get(pending_sql, {})
            pending_write = PendingWrite(
                sql=pending_sql,
                operation=details.get("operation"),
                rows_affected=details.get("rows_affected"),
                undo_available=details.get("undo_available"),
                undo_unavailable_reason=details.get("undo_unavailable_reason"),
            )
            break

    query_result = None
    last = mcp_client.last_run_sql_result
    if last is not None:
        query_result = QueryResult(columns=last.get("columns") or [], rows=last.get("rows") or [])

    return AskResponse(
        answer=answer,
        sql=sql,
        result=query_result,
        tool_calls=tool_calls,
        grounded_on=grounded_on,
        pending_write=pending_write,
        conversation_id=conversation_id,
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
        async with asyncio.timeout(settings.ask_timeout_seconds):
            async with _open_agent(database_id, current_user, deps) as (executor, mcp_client):
                result = await executor.ainvoke(
                    {"input": ask_request.query, "chat_history": session.chat_history},
                    config=session.agent_config,
                )
    except TimeoutError:
        await _record_usage(session, current_user, "timeout")
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=_timeout_detail(settings))
    except Exception as e:
        status_code, detail = _classify_agent_error(e)
        await _record_usage(session, current_user, "error")
        logger.exception("Agent execution failed for database %s", database_id)
        raise HTTPException(status_code=status_code, detail=detail)

    await _record_usage(session, current_user, "success")

    return await _finish(
        session, mcp_client, database_id, current_user, ask_request.query,
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

    async def produce(queue: asyncio.Queue) -> None:
        steps: list = []
        output = ""
        try:
            async with asyncio.timeout(settings.ask_timeout_seconds):
                async with _open_agent(database_id, current_user, deps) as (executor, mcp_client):
                    async for event in executor.astream_events(
                        {"input": ask_request.query, "chat_history": session.chat_history},
                        version="v2",
                        config=session.agent_config,
                    ):
                        kind = event["event"]
                        if kind == "on_tool_start":
                            await queue.put(_sse("tool_start", {"tool": event["name"], "args": event["data"].get("input")}))
                        elif kind == "on_tool_end":
                            action = _Action(event["name"], event["data"].get("input") or {})
                            steps.append((action, event["data"].get("output")))
                            await queue.put(_sse("tool_end", _to_tool_call_entries([steps[-1]])[0].model_dump(mode="json")))
                        elif kind == "on_chat_model_stream":
                            text = _content_to_text(event["data"]["chunk"].content)
                            if text:
                                await queue.put(_sse("token", {"text": text}))
                        elif kind == "on_chain_end" and event["name"] == "AgentExecutor":
                            output = event["data"]["output"].get("output", "")

            await _record_usage(session, current_user, "success")
            response = await _finish(session, mcp_client, database_id, current_user, ask_request.query, output, steps)
            await queue.put(_sse("done", response.model_dump(mode="json")))
        except TimeoutError:
            await _record_usage(session, current_user, "timeout")
            await queue.put(_sse("error", {"detail": _timeout_detail(settings)}))
        except Exception as e:
            _, detail = _classify_agent_error(e)
            await _record_usage(session, current_user, "error")
            logger.exception("Streaming agent execution failed for database %s", database_id)
            await queue.put(_sse("error", {"detail": detail}))
        finally:
            await queue.put(None)

    async def events() -> AsyncIterator[bytes]:
        # The agent and its MCP session run in their own task: the MCP
        # transport must be entered and exited in one task, and this
        # generator may be finalized from another when the client leaves.
        queue: asyncio.Queue = asyncio.Queue()
        task = asyncio.create_task(produce(queue))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
        try:
            while (item := await queue.get()) is not None:
                yield item
        finally:
            task.cancel()

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
    # Called in-process, not over MCP: `confirmed` is intentionally not part
    # of the MCP run_sql tool, so no MCP client can execute a write.
    tool_service = build_tool_service(deps["db_manager"])
    result = await tool_service.run_sql(
        RunSqlRequest(database_id=database_id, sql=body.sql, confirmed=True),
        user_id=current_user.id,
    )
    if body.conversation_id:
        affected = f"{result.rows_affected} rows affected" if result.rows_affected is not None else "done"
        await ConversationService(deps["mongo_db"]).append(
            current_user.id, database_id, body.conversation_id,
            f"[User confirmed and ran] {body.sql}",
            f"Executed ({affected}).",
            body.sql,
        )
    return QueryResult(
        columns=result.columns or [],
        rows=result.rows or [],
        rows_affected=result.rows_affected,
        undo_available=result.undo_available,
    )


# ---------------------------------------------------------------- helpers

# Strong references to running stream producers; asyncio keeps only weak ones.
_background_tasks: set[asyncio.Task] = set()

@dataclass
class _Action:
    """Minimal stand-in for LangChain's AgentAction used by the stream path."""
    tool: str
    tool_input: dict


def _timeout_detail(settings: Settings) -> str:
    return f"The assistant did not finish within {settings.ask_timeout_seconds} seconds"


def _classify_agent_error(e: Exception) -> tuple[int, str]:
    """Map LLM provider and tool server failures to something the user can act on."""
    if isinstance(e, MCPUnavailableError):
        return status.HTTP_503_SERVICE_UNAVAILABLE, "The database tool server is unavailable. Please try again shortly."
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
