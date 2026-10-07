from langchain_core.tools import StructuredTool
from mcp.types import Tool

from app.agent.mcp_client import MCPToolClient


def to_langchain_tools(client: MCPToolClient) -> list[StructuredTool]:
    """
    One LangChain tool per tool the MCP server advertised. Names,
    descriptions and argument schemas come from tools/list, so a tool added
    to the server reaches the agent without changes here.
    """
    return [_to_langchain_tool(client, tool) for tool in client.tools]


def _to_langchain_tool(client: MCPToolClient, tool: Tool) -> StructuredTool:
    async def call(**arguments) -> str:
        return await client.call_tool(tool.name, arguments)

    return StructuredTool(
        name=tool.name,
        description=tool.description or tool.name,
        args_schema=tool.inputSchema,
        coroutine=call,
        handle_tool_error=True,
    )
