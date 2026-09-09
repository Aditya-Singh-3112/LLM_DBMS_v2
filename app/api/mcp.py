from fastapi import APIRouter, Depends, Request, status

from app.api.dependencies import get_current_user
from app.core.database import DatabaseManager
from app.models.auth import UserResponse
from app.models.mcp_tools import (
    DescribeTableRequest,
    DescribeTableResponse,
    ListSchemasRequest,
    ListSchemasResponse,
    RunSqlRequest,
    RunSqlResponse,
    SampleRowsRequest,
    SampleRowsResponse,
)
from app.services.database_registry import DatabaseRegistryService
from app.services.mcp_tools import MCPToolService
from app.services.permission_service import PermissionService
from app.services.postgres_executor import PostgresExecutor
from app.services.cache_service import CacheService
from app.services.rate_limiter import RateLimiter


router = APIRouter(prefix="/mcp", tags=["mcp"])


def get_mcp_tool_service(
    request: Request,
) -> MCPToolService:
    database_manager: DatabaseManager = (
        request.app.state.database_manager
    )

    if database_manager.mongo_database is None:
        raise RuntimeError("MongoDB is not initialized")

    if database_manager.postgres_pool is None:
        raise RuntimeError("Postgres pool is not initialized")

    registry_service = DatabaseRegistryService(
        mongo_database=database_manager.mongo_database,
        database_manager=database_manager,
    )

    postgres_executor = PostgresExecutor(database_manager)

    permission_service = PermissionService(registry_service)

    cache_service = CacheService(redis=database_manager.redis)

    rate_limiter = RateLimiter(
        redis=database_manager.redis,
        calls_per_minute=60,
    )

    return MCPToolService(
        postgres_executor=postgres_executor,
        permission_service=permission_service,
        mongo_database=database_manager.mongo_database,
        cache_service=cache_service,
        rate_limiter=rate_limiter,
    )


@router.post(
    "/list_schemas",
    response_model=ListSchemasResponse,
)
async def list_schemas(
    request: ListSchemasRequest,
    current_user: UserResponse = Depends(get_current_user),
    service: MCPToolService = Depends(get_mcp_tool_service),
) -> ListSchemasResponse:
    return await service.list_schemas(request, current_user.id)


@router.post(
    "/describe_table",
    response_model=DescribeTableResponse,
)
async def describe_table(
    request: DescribeTableRequest,
    current_user: UserResponse = Depends(get_current_user),
    service: MCPToolService = Depends(get_mcp_tool_service),
) -> DescribeTableResponse:
    return await service.describe_table(request, current_user.id)


@router.post(
    "/sample_rows",
    response_model=SampleRowsResponse,
)
async def sample_rows(
    request: SampleRowsRequest,
    current_user: UserResponse = Depends(get_current_user),
    service: MCPToolService = Depends(get_mcp_tool_service),
) -> SampleRowsResponse:
    return await service.sample_rows(request, current_user.id)


@router.post(
    "/run_sql",
    response_model=RunSqlResponse,
)
async def run_sql(
    request: RunSqlRequest,
    current_user: UserResponse = Depends(get_current_user),
    service: MCPToolService = Depends(get_mcp_tool_service),
) -> RunSqlResponse:
    return await service.run_sql(request, current_user.id)