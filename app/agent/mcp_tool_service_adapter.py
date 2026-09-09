from typing import Any

from app.services.mcp_tools import MCPToolService
from app.models.mcp_tools import (
    DescribeTableRequest,
    ListSchemasRequest,
    RunSqlRequest,
    SampleRowsRequest,
)


class MCPToolServiceAdapter:
    """
    Same interface as MCPClientWrapper (list_schemas, describe_table, sample_rows,
    run_sql), but calls MCPToolService in-process instead of going through an
    MCP ClientSession/transport.
    """

    def __init__(self, service: MCPToolService, user_id: str) -> None:
        self.service = service
        self.user_id = user_id
        self.last_run_sql_result: dict | None = None

    async def list_schemas(self, database_id: str) -> dict[str, Any]:
        result = await self.service.list_schemas(
            ListSchemasRequest(database_id=database_id), self.user_id
        )
        return result.model_dump()

    async def describe_table(self, database_id: str, schema_name: str, table_name: str) -> dict[str, Any]:
        result = await self.service.describe_table(
            DescribeTableRequest(database_id=database_id, schema_name=schema_name, table_name=table_name),
            self.user_id,
        )
        return result.model_dump()

    async def sample_rows(self, database_id: str, schema_name: str, table_name: str, limit: int = 5) -> dict[str, Any]:
        result = await self.service.sample_rows(
            SampleRowsRequest(database_id=database_id, schema_name=schema_name, table_name=table_name, limit=limit),
            self.user_id,
        )
        return result.model_dump()

    async def run_sql(self, database_id: str, sql: str) -> dict[str, Any]:
        result = await self.service.run_sql(
            RunSqlRequest(database_id=database_id, sql=sql), self.user_id
        )
        dumped = result.model_dump()
        self.last_run_sql_result = dumped
        return dumped
