import json
from typing import Any

from mcp.client.session import ClientSession
from mcp.types import CallToolRequest


class MCPClientWrapper:
    """Wrapper around MCP ClientSession for easy tool calling."""

    def __init__(self, session: ClientSession, user_id: str) -> None:
        self.session = session
        self.user_id = user_id

    async def list_schemas(self, database_id: str) -> dict[str, Any]:
        """Call list_schemas tool."""
        result = await self.session.call_tool(
            CallToolRequest(
                name="list_schemas",
                arguments={
                    "database_id": database_id,
                    "user_id": self.user_id,
                },
            )
        )

        if result.isError:
            raise RuntimeError(f"Tool error: {result.content[0].text}")

        return json.loads(result.content[0].text)

    async def describe_table(self, database_id: str, schema_name: str, table_name: str) -> dict[str, Any]:
        """Call describe_table tool."""
        result = await self.session.call_tool(
            CallToolRequest(
                name="describe_table",
                arguments={
                    "database_id": database_id,
                    "schema_name": schema_name,
                    "table_name": table_name,
                    "user_id": self.user_id,
                },
            )
        )

        if result.isError:
            raise RuntimeError(f"Tool error: {result.content[0].text}")

        return json.loads(result.content[0].text)

    async def sample_rows(self, database_id: str, schema_name: str, table_name: str, limit: int = 5) -> dict[str, Any]:
        """Call sample_rows tool."""
        result = await self.session.call_tool(
            CallToolRequest(
                name="sample_rows",
                arguments={
                    "database_id": database_id,
                    "schema_name": schema_name,
                    "table_name": table_name,
                    "limit": limit,
                    "user_id": self.user_id,
                },
            )
        )

        if result.isError:
            raise RuntimeError(f"Tool error: {result.content[0].text}")

        return json.loads(result.content[0].text)

    async def run_sql(self, database_id: str, sql: str) -> dict[str, Any]:
        """Call run_sql tool."""
        result = await self.session.call_tool(
            CallToolRequest(
                name="run_sql",
                arguments={
                    "database_id": database_id,
                    "sql": sql,
                    "user_id": self.user_id,
                },
            )
        )

        if result.isError:
            raise RuntimeError(f"Tool error: {result.content[0].text}")

        return json.loads(result.content[0].text)