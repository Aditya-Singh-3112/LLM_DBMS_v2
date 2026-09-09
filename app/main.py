from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.ask import router as ask_router
from app.api.auth import router as auth_router
from app.api.databases import router as databases_router
from app.api.health import router as health_router
from app.core.config import get_settings
from app.core.database import DatabaseManager
from app.mcp_server.server import create_mcp_server
from app.api.export import router as export_router
from app.services.rag_service import RAGService

settings = get_settings()
database_manager = DatabaseManager(settings)

_global_db_manager = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _global_db_manager

    await database_manager.connect()
    _global_db_manager = database_manager
    app.state.database_manager = database_manager

    from app.services.auth import AuthService

    auth_service = AuthService(
        database=database_manager.mongo_database,
        settings=settings,
    )
    await auth_service.initialize()

    from app.services.database_registry import DatabaseRegistryService

    registry_service = DatabaseRegistryService(
        mongo_database=database_manager.mongo_database,
        database_manager=database_manager,
    )
    await registry_service.initialize()

    rag_service = RAGService(
        database_manager=database_manager,
        redis=database_manager.redis,
    )
    await rag_service.initialize()
    app.state.rag_service = rag_service

    mcp_server = create_mcp_server()
    app.state.mcp_server = mcp_server

    yield

    await database_manager.disconnect()


app = FastAPI(
    title=settings.app_name,
    debug=settings.debug,
    lifespan=lifespan,
)

app.include_router(health_router)
app.include_router(auth_router)
app.include_router(databases_router)
app.include_router(ask_router)
app.include_router(export_router)