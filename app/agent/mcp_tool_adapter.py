from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.agent.tool_service_adapter import ToolServiceAdapter


class ListSchemasInput(BaseModel):
    database_id: str = Field(description="The database ID")


class DescribeTableInput(BaseModel):
    database_id: str = Field(description="The database ID")
    table_name: str = Field(description="The table name")


class SampleRowsInput(BaseModel):
    database_id: str = Field(description="The database ID")
    table_name: str = Field(description="The table name")
    limit: int = Field(default=5, ge=1, le=100, description="Number of rows to sample")


class RunSqlInput(BaseModel):
    database_id: str = Field(description="The database ID")
    sql: str = Field(description="The SQL query to execute")


class MCPToolAdapter:
    """
    Exposes the database tool service to LangChain as StructuredTools.
    """

    def __init__(self, tool_client: ToolServiceAdapter) -> None:
        self.tool_client = tool_client

    def create_tools(self) -> list[StructuredTool]:
        """Create all database tools as LangChain StructuredTools."""
        return [
            self._create_list_schemas_tool(),
            self._create_describe_table_tool(),
            self._create_sample_rows_tool(),
            self._create_run_sql_tool(),
        ]

    def _create_list_schemas_tool(self) -> StructuredTool:
        async def list_schemas(database_id: str) -> str:
            result = await self.tool_client.list_schemas(database_id)
            schemas = result.get("schemas", [])
            if not schemas:
                return "The database has no tables yet."
            return f"Tables in database: {', '.join(schemas)}"

        return StructuredTool(
            name="list_schemas",
            description="List all tables in a database",
            coroutine=list_schemas,
            args_schema=ListSchemasInput,
            handle_tool_error=True,
        )

    def _create_describe_table_tool(self) -> StructuredTool:
        async def describe_table(database_id: str, table_name: str) -> str:
            result = await self.tool_client.describe_table(
                database_id=database_id,
                table_name=table_name,
            )

            columns = result.get("columns", [])
            col_str = ", ".join(
                [
                    f"{c['name']} ({c['type']}{', nullable' if c['nullable'] else ''})"
                    for c in columns
                ]
            )
            return f"Columns in {table_name}: {col_str}"

        return StructuredTool(
            name="describe_table",
            description="Get column information for a table",
            coroutine=describe_table,
            args_schema=DescribeTableInput,
            handle_tool_error=True,
        )

    def _create_sample_rows_tool(self) -> StructuredTool:
        async def sample_rows(database_id: str, table_name: str, limit: int = 5) -> str:
            result = await self.tool_client.sample_rows(
                database_id=database_id,
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
            handle_tool_error=True,
        )

    def _create_run_sql_tool(self) -> StructuredTool:
        async def run_sql(database_id: str, sql: str) -> str:
            result = await self.tool_client.run_sql(
                database_id=database_id,
                sql=sql,
            )

            columns = result.get("columns") or []
            rows = result.get("rows") or []

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
            handle_tool_error=True,
        )
