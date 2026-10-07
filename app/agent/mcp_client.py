import logging
from contextlib import AsyncExitStack
from typing import Any

import httpx
from langchain_core.tools import ToolException
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, TextContent, Tool

from app.models.rag import TextbookChunk

logger = logging.getLogger(__name__)


class MCPUnavailableError(RuntimeError):
    """The MCP server could not be reached or refused the session."""


class MCPToolClient:
    """
    One MCP session over Streamable HTTP, on behalf of one user.

        async with MCPToolClient(url, token) as client:
            client.tools                      # discovered via tools/list
            await client.call_tool("run_sql", {...})

    Enter and exit it in the same task: the SDK transport runs an anyio task
    group that must not cross tasks.

    Besides relaying calls, it keeps the structured results the API needs to
    report back: the last run_sql result and every retrieved textbook passage.
    """

    def __init__(
        self,
        url: str,
        access_token: str,
        timeout_seconds: float = 90,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.url = url
        self.access_token = access_token
        self.timeout_seconds = timeout_seconds
        # Tests pass an ASGITransport to reach an in-process server.
        self.transport = transport

        self.tools: list[Tool] = []
        self.last_run_sql_result: dict[str, Any] | None = None
        # Dry-run details of writes awaiting confirmation, by SQL text.
        self.pending_writes: dict[str, dict[str, Any]] = {}
        self.retrieved_passages: list[TextbookChunk] = []

        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None

    async def __aenter__(self) -> "MCPToolClient":
        stack = AsyncExitStack()
        try:
            http_client = await stack.enter_async_context(
                httpx.AsyncClient(
                    headers={"Authorization": f"Bearer {self.access_token}"},
                    timeout=httpx.Timeout(self.timeout_seconds),
                    transport=self.transport,
                )
            )
            read, write, _ = await stack.enter_async_context(
                streamable_http_client(self.url, http_client=http_client)
            )
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            self.tools = (await session.list_tools()).tools
        except BaseException as e:
            # When the transport fails (refused connection, 401), its task
            # group cancels us and the real error surfaces from closing it.
            error: BaseException = e
            try:
                await stack.aclose()
            except BaseException as close_error:
                error = close_error
            if isinstance(error, Exception):
                cause = _root_cause(error)
                logger.error("Could not open an MCP session at %s: %r", self.url, cause)
                raise MCPUnavailableError(f"MCP server unavailable: {cause}") from error
            raise error

        self._stack = stack
        self._session = session
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        stack, self._stack, self._session = self._stack, None, None
        if stack is None:
            return
        try:
            await stack.__aexit__(exc_type, exc, tb)
            return
        except BaseExceptionGroup as group:
            # The transport's task group wraps an exception raised inside
            # `async with` in a group; give callers back the original.
            if exc is None or _root_cause(group) is not exc:
                raise
        raise exc

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """
        Call a tool and return its text content. A tool-level error
        (`isError`) is raised as ToolException so the agent receives it as
        an observation and can correct itself.
        """
        if self._session is None:
            raise RuntimeError("MCPToolClient is not connected")

        result = await self._session.call_tool(name, arguments)
        text = _text_of(result)
        if result.isError:
            error = (result.structuredContent or {}).get("error") or {}
            if error.get("code") == "CONFIRMATION_REQUIRED" and error.get("sql"):
                self.pending_writes[error["sql"]] = error
            raise ToolException(text or f"Tool call failed: {name}")

        structured = result.structuredContent or {}
        if name == "run_sql":
            self.last_run_sql_result = structured
        elif name == "sql_reference_lookup":
            self.retrieved_passages.extend(
                TextbookChunk(**p) for p in structured.get("passages", [])
            )
        return text


def _root_cause(error: BaseException) -> BaseException:
    while isinstance(error, BaseExceptionGroup) and error.exceptions:
        error = error.exceptions[0]
    return error


def _text_of(result: CallToolResult) -> str:
    return "\n".join(block.text for block in result.content if isinstance(block, TextContent))
