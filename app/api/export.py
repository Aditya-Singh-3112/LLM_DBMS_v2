import logging

import re

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.dependencies import get_current_user
from app.core.database import DatabaseManager
from app.models.auth import UserResponse
from app.models.export import ExportFormat, ExportRequest
from app.models.mcp_tools import RunSqlRequest
from app.services.database_registry import DatabaseRegistryService
from app.services.export_service import ExportService
from app.services.mcp_tools import MCPToolService
from app.services.permission_service import PermissionService
from app.services.postgres_executor import PostgresExecutor
from app.services.cache_service import CacheService
from app.services.rate_limiter import RateLimiter
from app.services.sql_validator import SqlOperationType, SQLValidator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/databases", tags=["export"])

PREVIEW_ROWS = 100


class ExportQueryRequest(BaseModel):
    sql: str = Field(min_length=1, max_length=50_000)
    format: ExportFormat
    filename: str | None = Field(default=None, max_length=255)


def get_tool_service(request: Request) -> MCPToolService:
    """Build the tool service for one request."""
    db_manager: DatabaseManager = request.app.state.database_manager

    if db_manager.mongo_database is None:
        raise RuntimeError("MongoDB is not initialized")

    if db_manager.postgres_pool is None:
        raise RuntimeError("Postgres pool is not initialized")

    registry_service = DatabaseRegistryService(
        mongo_database=db_manager.mongo_database,
        database_manager=db_manager,
    )

    return MCPToolService(
        postgres_executor=PostgresExecutor(db_manager),
        permission_service=PermissionService(registry_service),
        mongo_database=db_manager.mongo_database,
        cache_service=CacheService(redis=db_manager.redis),
        rate_limiter=RateLimiter(redis=db_manager.redis, calls_per_minute=60),
    )


@router.post("/{database_id}/export")
async def export_query(
    database_id: str,
    export_request: ExportQueryRequest,
    current_user: UserResponse = Depends(get_current_user),
    tool_service: MCPToolService = Depends(get_tool_service),
):
    """
    Execute a SQL query and export results to CSV or XLSX.
    """
    # run_sql performs the permission check and validation itself.
    result = await tool_service.run_sql(
        RunSqlRequest(database_id=database_id, sql=export_request.sql),
        user_id=current_user.id,
    )

    if not result.columns or not result.rows:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Query returned no results to export",
        )

    try:
        exported_bytes = await ExportService().export(
            result=result,
            format=export_request.format.value,
        )
    except Exception:
        logger.exception("Export failed for database %s", database_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Export failed",
        )

    filename = (
        export_request.filename
        or ExportRequest(
            database_id=database_id,
            sql=export_request.sql,
            format=export_request.format,
        ).filename
    )

    return file_response(exported_bytes, export_request.format, filename)


def file_response(data: bytes, format: ExportFormat, filename: str) -> StreamingResponse:
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", filename)
    return StreamingResponse(
        iter([data]),
        media_type=ExportService.get_content_type(format.value),
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )


@router.post("/{database_id}/export/preview")
async def export_preview(
    database_id: str,
    export_request: ExportQueryRequest,
    current_user: UserResponse = Depends(get_current_user),
    tool_service: MCPToolService = Depends(get_tool_service),
):
    """
    Preview the first rows of a query without exporting.
    """
    # Wrap SELECTs rather than appending LIMIT, so queries that already end
    # in ';' or LIMIT remain valid. Anything else (or anything the validator
    # rejects) is passed through unchanged so run_sql reports the real error.
    sql = export_request.sql
    try:
        operation, _ = SQLValidator().validate_and_parse(sql, allow_write=True)
    except Exception:
        operation = None
    if operation is SqlOperationType.SELECT:
        inner = sql.strip().rstrip(";")
        sql = f"SELECT * FROM ({inner}) AS _preview LIMIT {PREVIEW_ROWS}"

    result = await tool_service.run_sql(
        RunSqlRequest(database_id=database_id, sql=sql),
        user_id=current_user.id,
    )

    rows = result.rows or []
    return {
        "columns": result.columns or [],
        "rows": rows[:10],
        "total_rows": len(rows),
        "format": export_request.format.value,
    }
