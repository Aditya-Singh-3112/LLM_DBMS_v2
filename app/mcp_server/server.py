from mcp.server import Server
from mcp.types import CallToolResult, TextContent, Tool

from app.mcp_server.handlers import MCPHandler


def create_mcp_server() -> Server:
    server = Server("llm-dbms")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="list_schemas",
                description="List all tables in a database schema",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "database_id": {
                            "type": "string",
                            "description": "The database ID (MongoDB ObjectId)",
                        }
                    },
                    "required": ["database_id"],
                },
            ),
            Tool(
                name="describe_table",
                description="Get column information for a table",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "database_id": {
                            "type": "string",
                            "description": "The database ID",
                        },
                        "schema_name": {
                            "type": "string",
                            "description": "The schema name (e.g., tenant_<id>)",
                        },
                        "table_name": {
                            "type": "string",
                            "description": "The table name",
                        },
                    },
                    "required": ["database_id", "schema_name", "table_name"],
                },
            ),
            Tool(
                name="sample_rows",
                description="Sample rows from a table",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "database_id": {
                            "type": "string",
                            "description": "The database ID",
                        },
                        "schema_name": {
                            "type": "string",
                            "description": "The schema name",
                        },
                        "table_name": {
                            "type": "string",
                            "description": "The table name",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Number of rows to sample (1-100)",
                            "default": 5,
                        },
                    },
                    "required": ["database_id", "schema_name", "table_name"],
                },
            ),
            Tool(
                name="run_sql",
                description="Execute a SQL query against a database",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "database_id": {
                            "type": "string",
                            "description": "The database ID",
                        },
                        "sql": {
                            "type": "string",
                            "description": "The SQL query to execute",
                        },
                    },
                    "required": ["database_id", "sql"],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> CallToolResult:
        handlers = MCPHandler()

        try:
            if name == "list_schemas":
                result = await handlers.handle_list_schemas(arguments)
            elif name == "describe_table":
                result = await handlers.handle_describe_table(arguments)
            elif name == "sample_rows":
                result = await handlers.handle_sample_rows(arguments)
            elif name == "run_sql":
                result = await handlers.handle_run_sql(arguments)
            else:
                return CallToolResult(
                    content=[TextContent(type="text", text=f"Unknown tool: {name}")],
                    isError=True,
                )

            return CallToolResult(
                content=[TextContent(type="text", text=result)],
                isError=False,
            )

        except Exception as e:
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=f"Error executing {name}: {str(e)}",
                    )
                ],
                isError=True,
            )

    return server