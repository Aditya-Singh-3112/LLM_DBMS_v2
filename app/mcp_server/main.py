"""
MCP server process.

    uvicorn app.mcp_server.main:app --port 8001

Serves Streamable HTTP at /mcp (bearer JWT required) and an unauthenticated
/health. The API's agent connects here as an MCP client; so can any other
MCP client that holds a valid access token.
"""
import asyncio
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from app.core.config import get_settings
from app.core.database import DatabaseManager
from app.core.metrics import MetricsMiddleware, metrics_endpoint
from app.core.observability import configure_logging
from app.mcp_server.server import ServerState, build_mcp_server
from app.services.rag_service import RAGService

settings = get_settings()
configure_logging(json_logs=settings.app_env != "development", debug=settings.debug)

state = ServerState()
mcp = build_mcp_server(settings, state)


@asynccontextmanager
async def lifespan(app: Starlette):
    database_manager = DatabaseManager(settings)
    await database_manager.connect()
    rag_service = RAGService(database_manager=database_manager, redis=database_manager.redis)
    await rag_service.initialize()
    state.database_manager = database_manager
    state.rag_service = rag_service

    # Mounted sub-apps don't get their own lifespan, so start the MCP
    # session manager here.
    async with mcp.session_manager.run():
        yield

    state.database_manager = None
    state.rag_service = None
    await database_manager.disconnect()


async def health(request: Request) -> JSONResponse:
    """503 until started, and whenever Postgres or Mongo stops answering."""
    database_manager = state.database_manager
    if database_manager is None:
        return JSONResponse({"status": "starting"}, status_code=503)

    async def postgres() -> None:
        async with database_manager.postgres_connection() as connection:
            await connection.fetchval("SELECT 1")

    report = {}
    for name, check in (("postgres", postgres), ("mongo", lambda: database_manager.mongo_client.admin.command("ping"))):
        try:
            await asyncio.wait_for(check(), 2)
            report[name] = "ok"
        except Exception:
            report[name] = "unavailable"
    healthy = all(v == "ok" for v in report.values())
    return JSONResponse({"status": "ok" if healthy else "unavailable", **report}, status_code=200 if healthy else 503)


routes = [Route("/health", health)]
middleware = []
if settings.metrics_enabled:
    routes.append(Route("/metrics", metrics_endpoint))
    middleware.append(Middleware(MetricsMiddleware, known_paths=frozenset({"/mcp", "/health"})))

app = Starlette(
    routes=[*routes, Mount("/", app=mcp.streamable_http_app())],
    middleware=middleware,
    lifespan=lifespan,
)
