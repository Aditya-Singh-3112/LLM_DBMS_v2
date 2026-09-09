from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, status
from mcp.server import Server
from mcp.server.sse import SseServerTransport


async def setup_mcp_transport(app: FastAPI, server: Server) -> None:
    """Set up SSE transport for MCP server over HTTP."""

    @app.post("/mcp/messages")
    async def handle_mcp_message(message: dict):
        """Handle MCP protocol messages."""
        try:
            response = await server.handle_message(message)
            return response
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e),
            )

    @app.get("/mcp/sse")
    async def mcp_sse():
        """SSE endpoint for MCP streaming."""
        transport = SseServerTransport(
            "/mcp/messages",
        )
        await server.add_transport(transport)
        return transport.sse_response()