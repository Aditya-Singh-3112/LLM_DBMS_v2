"""
Tool definitions for the MCP server.

Each tool resolves the caller from the bearer token, delegates to the same
MCPToolService pipeline the rest of the app uses (rate limit -> permission
check -> cache -> execute -> audit log), and answers with a CallToolResult
that carries both LLM-readable text and `structuredContent` for programmatic
clients.

Errors are tool results with `isError: true`, not protocol errors, so an
agent sees them as observations it can recover from. Their structured
content is `{"error": {"code": ..., "message": ...}}`.
"""
import json
import logging
from dataclasses import dataclass
from typing import Annotated, Any, Awaitable, Callable

from fastapi import HTTPException, status
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel, Field, ValidationError

from app.core.config import Settings
from app.core.database import DatabaseManager
from app.mcp_server.auth import JwtTokenVerifier, current_user_id
from app.models.mcp_tools import (
    DescribeTableRequest,
    ListSchemasRequest,
    RunSqlRequest,
    SampleRowsRequest,
)
from app.services.database_registry import DatabaseRegistryService
from app.services.mcp_tools import MCPToolService, build_tool_service
from app.services.rag_service import RAGService

logger = logging.getLogger(__name__)

CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"

_ERROR_CODES = {
    status.HTTP_400_BAD_REQUEST: "INVALID_REQUEST",
    status.HTTP_401_UNAUTHORIZED: "UNAUTHENTICATED",
    status.HTTP_403_FORBIDDEN: "PERMISSION_DENIED",
    status.HTTP_404_NOT_FOUND: "NOT_FOUND",
    status.HTTP_413_CONTENT_TOO_LARGE: "STORAGE_LIMIT",
    status.HTTP_429_TOO_MANY_REQUESTS: "RATE_LIMITED",
}

DatabaseId = Annotated[str, Field(description="The database ID")]
TableName = Annotated[str, Field(description="The table name, unqualified")]


@dataclass
class ServerState:
    """Filled in by the app's lifespan; tools read it per call."""
    database_manager: DatabaseManager | None = None
    rag_service: RAGService | None = None

    def tool_service(self) -> MCPToolService:
        return build_tool_service(self._require_database_manager())

    def registry(self) -> DatabaseRegistryService:
        database_manager = self._require_database_manager()
        return DatabaseRegistryService(
            mongo_database=database_manager.mongo_database,
            database_manager=database_manager,
        )

    def _require_database_manager(self) -> DatabaseManager:
        if self.database_manager is None:
            raise RuntimeError("MCP server is not initialized")
        return self.database_manager


def build_mcp_server(settings: Settings, state: ServerState) -> FastMCP:
    mcp = FastMCP(
        name="llm-dbms",
        instructions=(
            "Tools for querying the caller's Postgres databases. Call list_databases "
            "to find a database id, then list_schemas / describe_table / sample_rows "
            "to learn its structure before run_sql. Writes are never executed through "
            "MCP: run_sql answers CONFIRMATION_REQUIRED and the user runs them from the app."
        ),
        token_verifier=JwtTokenVerifier(settings),
        auth=AuthSettings(
            issuer_url=settings.mcp_issuer_url,
            resource_server_url=None,
        ),
        # Stateless + plain JSON responses: every POST carries its own bearer
        # token and is answered in full, so any replica can serve any request
        # and there is no server-side session to expire or pin.
        stateless_http=True,
        json_response=True,
        # DNS-rebinding protection guards unauthenticated local servers; every
        # request here needs a valid bearer token, and the server is reached by
        # service name inside docker, which the localhost allow-list rejects.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    @mcp.tool(description="List the databases you can access, with your access level on each.")
    async def list_databases() -> CallToolResult:
        async def call() -> CallToolResult:
            databases = await state.registry().list_for_user(current_user_id())
            items = [
                {"id": d.id, "name": d.name, "access_level": d.access_level.value}
                for d in databases
            ]
            if not items:
                text = "You have no databases."
            else:
                text = "Databases:\n" + "\n".join(
                    f"- {d['name']} (id {d['id']}, {d['access_level']} access)" for d in items
                )
            return _ok(text, {"databases": items})

        return await _guard("list_databases", call)

    @mcp.tool(description="List all tables in a database.")
    async def list_schemas(database_id: DatabaseId) -> CallToolResult:
        async def call() -> CallToolResult:
            result = await state.tool_service().list_schemas(
                ListSchemasRequest(database_id=database_id), current_user_id()
            )
            text = (
                f"Tables in database: {', '.join(result.schemas)}"
                if result.schemas
                else "The database has no tables yet."
            )
            return _ok(text, result)

        return await _guard("list_schemas", call)

    @mcp.tool(description="Get column names, types and nullability for a table.")
    async def describe_table(database_id: DatabaseId, table_name: TableName) -> CallToolResult:
        async def call() -> CallToolResult:
            result = await state.tool_service().describe_table(
                DescribeTableRequest(database_id=database_id, table_name=table_name),
                current_user_id(),
            )
            columns = ", ".join(
                f"{c.name} ({c.type}{', nullable' if c.nullable else ''})" for c in result.columns
            )
            return _ok(f"Columns in {table_name}: {columns}", result)

        return await _guard("describe_table", call)

    @mcp.tool(description="Sample rows from a table to see what its data looks like.")
    async def sample_rows(
        database_id: DatabaseId,
        table_name: TableName,
        limit: Annotated[int, Field(ge=1, le=100, description="Number of rows to sample")] = 5,
    ) -> CallToolResult:
        async def call() -> CallToolResult:
            result = await state.tool_service().sample_rows(
                SampleRowsRequest(database_id=database_id, table_name=table_name, limit=limit),
                current_user_id(),
            )
            text = (
                f"Sample {len(result.rows)} rows from {table_name}:\n"
                f"Columns: {', '.join(result.columns)}\n"
                f"Rows: {_rows_text(result)}"
            )
            return _ok(text, result)

        return await _guard("sample_rows", call)

    @mcp.tool(
        description=(
            "Execute one SQL statement: SELECT, INSERT, UPDATE, DELETE, CREATE TABLE/INDEX/VIEW, "
            "and for database owners ALTER TABLE, DROP TABLE/VIEW/INDEX and TRUNCATE. Reference "
            "tables by bare name. Writes are not executed: they are dry-run, then return "
            "CONFIRMATION_REQUIRED and the user must confirm them in the app."
        )
    )
    async def run_sql(
        database_id: DatabaseId,
        sql: Annotated[str, Field(description="The SQL statement to execute")],
    ) -> CallToolResult:
        # `confirmed` is deliberately not a parameter: no MCP client, agent or
        # otherwise, can execute a write. Only the REST /sql/confirm route can.
        async def call() -> CallToolResult:
            result = await state.tool_service().run_sql(
                RunSqlRequest(database_id=database_id, sql=sql), current_user_id()
            )
            if not result.columns:
                return _ok("Query executed successfully with no rows returned", result)
            rows = result.rows or []
            text = (
                f"Query result ({len(rows)} rows):\n"
                f"Columns: {', '.join(result.columns)}\n"
                f"Rows: {_rows_text(result)}"
            )
            return _ok(text, result)

        return await _guard("run_sql", call)

    @mcp.tool(
        description=(
            "Look up SQL and database concepts in the reference textbook. Use it before "
            "writing SQL with joins, subqueries or window functions, or when unsure "
            "about normalization or SQL semantics."
        )
    )
    async def sql_reference_lookup(
        query: Annotated[str, Field(description="The SQL semantic question to look up")],
        k: Annotated[int, Field(ge=1, le=10, description="Number of passages to retrieve")] = 4,
    ) -> CallToolResult:
        async def call() -> CallToolResult:
            if state.rag_service is None:
                return _ok("No relevant passages found in the textbook.", {"passages": []})
            try:
                result = await state.rag_service.retrieve(query, k=k)
            except Exception as e:
                return _error("RETRIEVAL_FAILED", f"Tool call failed (RETRIEVAL_FAILED): {e}")
            if not result.passages:
                return _ok("No relevant passages found in the textbook.", {"passages": []})
            text = f"Retrieved {len(result.passages)} passages:\n" + "\n---\n".join(
                f"[{p.source}] {p.chapter or 'N/A'}\n{p.content}" for p in result.passages
            )
            return _ok(text, {"passages": [p.model_dump(mode="json") for p in result.passages]})

        return await _guard("sql_reference_lookup", call)

    return mcp


async def _guard(tool_name: str, call: Callable[[], Awaitable[CallToolResult]]) -> CallToolResult:
    """Translate service exceptions into MCP tool error results."""
    try:
        return await call()
    except HTTPException as e:
        detail = e.detail
        if isinstance(detail, dict) and detail.get("code") == "confirmation_required":
            effects = []
            if detail.get("rows_affected") is not None:
                effects.append(f"A dry run shows it would affect {detail['rows_affected']} rows.")
            if detail.get("undo_available"):
                effects.append("It can be undone afterwards.")
            elif detail.get("undo_unavailable_reason"):
                effects.append(f"It cannot be undone ({detail['undo_unavailable_reason']}).")
            return _error(
                CONFIRMATION_REQUIRED,
                f"{CONFIRMATION_REQUIRED}: the statement was NOT executed. "
                "Tell the user exactly what it would do and that they must click "
                "'Run anyway' to execute it. Do not retry it yourself.\n"
                + (" ".join(effects) + "\n" if effects else "")
                + f"SQL: {detail['sql']}",
                operation=detail.get("operation"),
                sql=detail["sql"],
                rows_affected=detail.get("rows_affected"),
                undo_available=detail.get("undo_available"),
                undo_unavailable_reason=detail.get("undo_unavailable_reason"),
            )
        code = _ERROR_CODES.get(e.status_code, "ERROR")
        return _error(code, f"Tool call failed ({code}): {detail}")
    except ValidationError as e:
        message = "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors())
        return _error("INVALID_REQUEST", f"Tool call failed (INVALID_REQUEST): {message}")
    except PermissionError as e:
        return _error("UNAUTHENTICATED", f"Tool call failed (UNAUTHENTICATED): {e}")
    except Exception:
        logger.exception("MCP tool %s failed", tool_name)
        return _error("ERROR", "Tool call failed (ERROR): internal error")


def _ok(text: str, data: BaseModel | dict[str, Any]) -> CallToolResult:
    structured = data.model_dump(mode="json") if isinstance(data, BaseModel) else data
    return CallToolResult(content=[TextContent(type="text", text=text)], structuredContent=structured)


def _error(code: str, message: str, **extra: Any) -> CallToolResult:
    # `message` is written for the model; `code` is for programs.
    return CallToolResult(
        content=[TextContent(type="text", text=message)],
        structuredContent={"error": {"code": code, "message": message, **extra}},
        isError=True,
    )


def _rows_text(result: BaseModel) -> str:
    """Rows as JSON, so dates and decimals read the same as in structuredContent."""
    return json.dumps(result.model_dump(mode="json").get("rows") or [])
