import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, Query, UploadFile, status

from app.api.dependencies import get_current_user
from app.api.export import file_response, get_tool_service
from app.models.auth import UserResponse
from app.models.contracts import AccessLevel
from app.models.export import ExportFormat
from app.models.mcp_tools import (
    IDENTIFIER_PATTERN,
    DescribeTableRequest,
    ListSchemasRequest,
    RunSqlRequest,
    RunSqlResponse,
)
from app.services.export_service import ExportService
from app.services.import_service import ImportError_, default_table_name, parse_upload
from app.services.mcp_tools import MCPToolService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/databases", tags=["tables"])

PREVIEW_ROWS = 5


@router.get("/{database_id}/tables")
async def list_tables(
    database_id: str,
    current_user: UserResponse = Depends(get_current_user),
    tool_service: MCPToolService = Depends(get_tool_service),
) -> dict:
    """List the tables in a database."""
    result = await tool_service.list_schemas(
        ListSchemasRequest(database_id=database_id),
        user_id=current_user.id,
    )
    return {"tables": result.schemas}


@router.get("/{database_id}/tables/{table_name}/export")
async def export_table(
    database_id: str,
    table_name: str = Path(pattern=IDENTIFIER_PATTERN),
    format: ExportFormat = Query(default=ExportFormat.CSV),
    current_user: UserResponse = Depends(get_current_user),
    tool_service: MCPToolService = Depends(get_tool_service),
):
    """
    Download a whole table as CSV or XLSX.

    An empty table still yields a file containing just the header row.
    """
    # table_name matched IDENTIFIER_PATTERN, so quoting it is safe; the
    # tenant role and search_path confine it to the caller's schema.
    result = await tool_service.run_sql(
        RunSqlRequest(database_id=database_id, sql=f'SELECT * FROM "{table_name}"'),
        user_id=current_user.id,
    )

    if not result.columns:
        described = await tool_service.describe_table(
            DescribeTableRequest(database_id=database_id, table_name=table_name),
            user_id=current_user.id,
        )
        result = RunSqlResponse(columns=[c.name for c in described.columns], rows=[])

    try:
        exported_bytes = await ExportService().export(result=result, format=format.value)
    except Exception:
        logger.exception("Table export failed for %s.%s", database_id, table_name)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Export failed",
        )

    return file_response(exported_bytes, format, f"{table_name}.{format.value}")


@router.post("/{database_id}/tables/import")
async def import_table(
    database_id: str,
    file: UploadFile = File(...),
    table_name: str | None = Form(default=None, pattern=IDENTIFIER_PATTERN),
    mode: str = Form(default="create", pattern=r"^(create|append|replace)$"),
    dry_run: bool = Form(default=False),
    current_user: UserResponse = Depends(get_current_user),
    tool_service: MCPToolService = Depends(get_tool_service),
) -> dict:
    """
    Upload a CSV/XLSX file as a table.

    With dry_run=true, returns the inferred schema and a preview without
    writing anything.
    """
    await tool_service.permission_service.check_access(
        database_id=database_id,
        user_id=current_user.id,
        required_level=AccessLevel.WRITE,
    )

    filename = file.filename or "upload.csv"
    content = await file.read()

    try:
        plan = parse_upload(filename, content)
    except ImportError_ as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    target = table_name or default_table_name(filename)
    columns = [{"name": c.name, "type": c.type, "source_header": c.source_header} for c in plan.columns]

    response = {
        "table_name": target,
        "mode": mode,
        "columns": columns,
        "row_count": plan.row_count,
        "skipped_rows": plan.skipped_rows,
        "preview": [[_jsonable(v) for v in row] for row in plan.rows[:PREVIEW_ROWS]],
        "dry_run": dry_run,
    }
    if dry_run:
        return response

    schema_name = tool_service.schema_for(database_id)
    try:
        await tool_service.postgres_executor.import_rows(
            schema_name=schema_name,
            table_name=target,
            columns=[(c.name, c.type) for c in plan.columns],
            rows=plan.rows,
            mode=mode,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Import into %s.%s failed", database_id, target)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Import failed: {e}",
        )

    await tool_service.cache_service.invalidate_mcp_cache(database_id)
    return response


def _jsonable(v):
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    return str(v)
