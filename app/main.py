from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.account import router as account_router
from app.api.ask import router as ask_router
from app.api.auth import router as auth_router
from app.api.databases import router as databases_router
from app.api.export import router as export_router
from app.api.health import router as health_router
from app.api.history import router as history_router
from app.api.tables import router as tables_router
from app.core.config import get_settings
from app.core.database import DatabaseManager
from app.core.metrics import MetricsMiddleware, metrics_endpoint
from app.core.migrations import run_migrations
from app.core.observability import RequestIdMiddleware, configure_logging, install_error_handlers
from app.services.audit_service import AuditService
from app.services.auth import AuthService
from app.services.conversation_service import ConversationService
from app.services.database_registry import DatabaseRegistryService
from app.services.saved_query_service import SavedQueryService
from app.services.usage_service import UsageService

settings = get_settings()
configure_logging(json_logs=settings.app_env != "development", debug=settings.debug)
database_manager = DatabaseManager(settings)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await database_manager.connect()
    app.state.database_manager = database_manager

    await run_migrations(database_manager)

    await AuthService(
        database=database_manager.mongo_database,
        settings=settings,
    ).initialize()

    await DatabaseRegistryService(
        mongo_database=database_manager.mongo_database,
        database_manager=database_manager,
    ).initialize()

    for service in (ConversationService, SavedQueryService, AuditService, UsageService):
        await service(database_manager.mongo_database).initialize()

    # The agent's tools, including textbook retrieval, live in the MCP
    # server process (app/mcp_server); this API reaches them as a client.
    yield

    await database_manager.disconnect()


# Never pass debug=True here: Starlette would then answer unhandled errors
# with an HTML traceback instead of our JSON handler. DEBUG only affects
# log verbosity.
app = FastAPI(
    title=settings.app_name,
    lifespan=lifespan,
)

install_error_handlers(app)
app.add_middleware(RequestIdMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)

if settings.metrics_enabled:
    app.add_middleware(MetricsMiddleware)
    app.add_route("/metrics", metrics_endpoint, include_in_schema=False)

app.include_router(health_router)
app.include_router(auth_router)
app.include_router(account_router)
app.include_router(history_router)
app.include_router(databases_router)
app.include_router(ask_router)
app.include_router(export_router)
app.include_router(tables_router)
