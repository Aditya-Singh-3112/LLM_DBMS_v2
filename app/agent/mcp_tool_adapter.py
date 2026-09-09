from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.mcp_server.client import MCPClientWrapper


class ListSchemasInput(BaseModel):
    database_id: str = Field(description="The database ID")


class DescribeTableInput(BaseModel):
    database_id: str = Field(description="The database ID")
    schema_name: str = Field(description="The schema name (e.g., tenant_<id>)")
    table_name: str = Field(description="The table name")


class SampleRowsInput(BaseModel):
    database_id: str = Field(description="The database ID")
    schema_name: str = Field(description="The schema name")
    table_name: str = Field(description="The table name")
    limit: int = Field(default=5, ge=1, le=100, description="Number of rows to sample")


class RunSqlInput(BaseModel):
    database_id: str = Field(description="The database ID")
    sql: str = Field(description="The SQL query to execute")


class MCPToolAdapter:
    """
    Adapts MCP tool schemas to LangChain StructuredTools.
    """

    def __init__(self, mcp_client: MCPClientWrapper) -> None:
        self.mcp_client = mcp_client

    def create_tools(self) -> list[StructuredTool]:
        """Create all MCP tools as LangChain StructuredTools."""
        return [
            self._create_list_schemas_tool(),
            self._create_describe_table_tool(),
            self._create_sample_rows_tool(),
            self._create_run_sql_tool(),
        ]

    def _create_list_schemas_tool(self) -> StructuredTool:
        async def list_schemas(database_id: str) -> str:
            result = await self.mcp_client.list_schemas(database_id)
            schemas = result.get("schemas", [])
            return f"Tables in database: {', '.join(schemas)}"

        return StructuredTool(
            name="list_schemas",
            description="List all tables in a database",
            coroutine=list_schemas, 
            args_schema=ListSchemasInput,
        )

    def _create_describe_table_tool(self) -> StructuredTool:
        async def describe_table(database_id: str, schema_name: str, table_name: str) -> str:
            result = await self.mcp_client.describe_table(
                database_id=database_id,
                schema_name=schema_name,
                table_name=table_name,
            )

            columns = result.get("columns", [])
            col_str = ", ".join(
                [f"{c['name']} ({c['type']})" for c in columns]
            )
            return f"Columns in {table_name}: {col_str}"

        return StructuredTool(
            name="describe_table",
            description="Get column information for a table",
            coroutine=describe_table,
            args_schema=DescribeTableInput,
        )

    def _create_sample_rows_tool(self) -> StructuredTool:
        async def sample_rows(database_id: str, schema_name: str, table_name: str, limit: int = 5) -> str:
            result = await self.mcp_client.sample_rows(
                database_id=database_id,
                schema_name=schema_name,
                table_name=table_name,
                limit=limit,
            )

            columns = result.get("columns", [])
            rows = result.get("rows", [])
            return (
                f"Sample {len(rows)} rows from {table_name}:\n"
                f"Columns: {', '.join(columns)}\n"
                f"Rows: {rows}"
            )

        return StructuredTool(
            name="sample_rows",
            description="Sample rows from a table",
            coroutine=sample_rows,
            args_schema=SampleRowsInput,
        )

    def _create_run_sql_tool(self) -> StructuredTool:
        async def run_sql(database_id: str, sql: str) -> str:
            result = await self.mcp_client.run_sql(
                database_id=database_id,
                sql=sql,
            )

            columns = result.get("columns", [])
            rows = result.get("rows", [])

            if not columns:
                return "Query executed successfully with no rows returned"

            return (
                f"Query result ({len(rows)} rows):\n"
                f"Columns: {', '.join(columns)}\n"
                f"Rows: {rows}"
            )

        return StructuredTool(
            name="run_sql",
            description="Execute a SQL query against a database",
            coroutine=run_sql,
            args_schema=RunSqlInput,
        )