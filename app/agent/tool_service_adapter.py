from typing import Any

from fastapi import HTTPException
from langchain_core.tools import ToolException

from app.services.mcp_tools import MCPToolService
from app.models.mcp_tools import (
    DescribeTableRequest,
    ListSchemasRequest,
    RunSqlRequest,
    SampleRowsRequest,
)


class ToolServiceAdapter:
    """
    Calls MCPToolService in-process on behalf of one user and records the
    last run_sql result so the API layer can return it verbatim.

    HTTPExceptions raised by the service (permission denied, invalid SQL,
    rate limit) are re-raised as ToolException so the agent sees them as tool
    observations and can recover, instead of aborting the whole request.
    """

    def __init__(self, service: MCPToolService, user_id: str) -> None:
        self.service = service
        self.user_id = user_id
        self.last_run_sql_result: dict | None = None

    @staticmethod
    async def _call(coro):
        try:
            return await coro
        except HTTPException as e:
            detail = e.detail
            if isinstance(detail, dict) and detail.get("code") == "confirmation_required":
                raise ToolException(
                    "CONFIRMATION_REQUIRED: the statement was NOT executed. "
                    "Tell the user exactly what it would do and that they must click "
                    "'Run anyway' to execute it. Do not retry it yourself.\n"
                    f"SQL: {detail['sql']}"
                ) from e
            raise ToolException(f"Tool call failed ({e.status_code}): {detail}") from e

    async def list_schemas(self, database_id: str) -> dict[str, Any]:
        result = await self._call(self.service.list_schemas(
            ListSchemasRequest(database_id=database_id), self.user_id
        ))
        return result.model_dump()

    async def describe_table(self, database_id: str, table_name: str) -> dict[str, Any]:
        result = await self._call(self.service.describe_table(
            DescribeTableRequest(database_id=database_id, table_name=table_name),
            self.user_id,
        ))
        return result.model_dump()

    async def sample_rows(self, database_id: str, table_name: str, limit: int = 5) -> dict[str, Any]:
        result = await self._call(self.service.sample_rows(
            SampleRowsRequest(database_id=database_id, table_name=table_name, limit=limit),
            self.user_id,
        ))
        return result.model_dump()

    async def run_sql(self, database_id: str, sql: str) -> dict[str, Any]:
        result = await self._call(self.service.run_sql(
            RunSqlRequest(database_id=database_id, sql=sql), self.user_id
        ))
        dumped = result.model_dump()
        self.last_run_sql_result = dumped
        return dumped
