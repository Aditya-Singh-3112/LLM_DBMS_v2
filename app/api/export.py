from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from app.api.dependencies import get_current_user
from app.core.database import DatabaseManager
from app.models.auth import UserResponse
from app.models.export import ExportFormat, ExportRequest
from app.models.mcp_tools import RunSqlRequest, RunSqlResponse
from app.services.database_registry import DatabaseRegistryService
from app.services.export_service import ExportService
from app.services.mcp_tools import MCPToolService
from app.services.permission_service import PermissionService
from app.services.postgres_executor import PostgresExecutor
from app.services.cache_service import CacheService
from app.services.rate_limiter import RateLimiter
from pydantic import BaseModel, Field


router = APIRouter(prefix="/databases", tags=["export"])


class ExportQueryRequest(BaseModel):
    sql: str = Field(min_length=1, max_length=50_000)
    format: ExportFormat
    filename: str | None = Field(default=None, max_length=255)


def get_export_dependencies(request: Request):
    """Dependencies for export endpoint."""
    db_manager: DatabaseManager = request.app.state.database_manager

    if db_manager.mongo_database is None:
        raise RuntimeError("MongoDB is not initialized")

    if db_manager.postgres_pool is None:
        raise RuntimeError("Postgres pool is not initialized")

    return {
        "db_manager": db_manager,
        "mongo_db": db_manager.mongo_database,
    }


async def _get_mcp_tool_service(
    db_manager: DatabaseManager,
    mongo_db,
):
    """Get MCP tool service with all dependencies."""
    registry_service = DatabaseRegistryService(
        mongo_database=mongo_db,
        database_manager=db_manager,
    )

    postgres_executor = PostgresExecutor(db_manager)
    permission_service = PermissionService(registry_service)
    cache_service = CacheService(redis=db_manager.redis)
    rate_limiter = RateLimiter(redis=db_manager.redis, calls_per_minute=60)

    return MCPToolService(
        postgres_executor=postgres_executor,
        permission_service=permission_service,
        mongo_database=mongo_db,
        cache_service=cache_service,
        rate_limiter=rate_limiter,
    )


@router.post("/{database_id}/export")
async def export_query(
    database_id: str,
    export_request: ExportQueryRequest,
    current_user: UserResponse = Depends(get_current_user),
    deps=Depends(get_export_dependencies),
):
    """
    Execute a SQL query and export results to CSV or XLSX.

    Query results are streamed directly to the client without loading
    into memory, suitable for large result sets.
    """
    db_manager = deps["db_manager"]
    mongo_db = deps["mongo_db"]

    registry_service = DatabaseRegistryService(
        mongo_database=mongo_db,
        database_manager=db_manager,
    )

    try:
        access_level = await registry_service.get_access_level(
            database_id=database_id,
            user_id=current_user.id,
        )
    except HTTPException as e:
        if e.status_code == status.HTTP_403_FORBIDDEN:
            raise
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Database not found",
        )

    mcp_service = await _get_mcp_tool_service(db_manager, mongo_db)

    run_sql_request = RunSqlRequest(
        database_id=database_id,
        sql=export_request.sql,
    )

    try:
        result = await mcp_service.run_sql(
            run_sql_request,
            user_id=current_user.id,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Query execution failed: {str(e)}",
        )

    if not result.columns or not result.rows:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Query returned no results to export",
        )

    export_service = ExportService()

    try:
        exported_bytes = await export_service.export(
            result=result,
            format=export_request.format.value,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Export failed: {str(e)}",
        )

    filename = (
        export_request.filename
        or ExportRequest(
            database_id=database_id,
            sql=export_request.sql,
            format=export_request.format,
        ).filename
    )

    content_type = ExportService.get_content_type(
        export_request.format.value
    )

    return StreamingResponse(
        iter([exported_bytes]),
        media_type=content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        },
    )


@router.post("/{database_id}/export/preview")
async def export_preview(
    database_id: str,
    export_request: ExportQueryRequest,
    current_user: UserResponse = Depends(get_current_user),
    deps=Depends(get_export_dependencies),
):
    """
    Preview first N rows of a query without exporting.
    Useful for validating the query before full export.
    """
    db_manager = deps["db_manager"]
    mongo_db = deps["mongo_db"]

    registry_service = DatabaseRegistryService(
        mongo_database=mongo_db,
        database_manager=db_manager,
    )

    try:
        await registry_service.get_access_level(
            database_id=database_id,
            user_id=current_user.id,
        )
    except HTTPException as e:
        if e.status_code == status.HTTP_403_FORBIDDEN:
            raise
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Database not found",
        )

    mcp_service = await _get_mcp_tool_service(db_manager, mongo_db)

    limit_sql = f"{export_request.sql} LIMIT 100"

    run_sql_request = RunSqlRequest(
        database_id=database_id,
        sql=limit_sql,
    )

    try:
        result = await mcp_service.run_sql(
            run_sql_request,
            user_id=current_user.id,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Query execution failed: {str(e)}",
        )

    return {
        "columns": result.columns,
        "rows": result.rows[:10] if result.rows else [],
        "total_rows": len(result.rows) if result.rows else 0,
        "format": export_request.format.value,
    }