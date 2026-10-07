import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, Query, UploadFile, status

from app.api.dependencies import get_current_user
from pydantic import BaseModel, Field

from app.api.export import cap_select, file_response, get_tool_service
from app.models.auth import UserResponse
from app.models.contracts import AccessLevel
from app.models.export import ExportFormat
from app.models.mcp_tools import (
    IDENTIFIER_PATTERN,
    BrowseRowsRequest,
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
# Most rows the SQL editor shows for one SELECT.
SQL_EDITOR_MAX_ROWS = 1000


class SqlRequest(BaseModel):
    sql: str = Field(min_length=1, max_length=50_000)


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


@router.get("/{database_id}/tables/{table_name}")
async def describe_table(
    database_id: str,
    table_name: str = Path(pattern=IDENTIFIER_PATTERN),
    current_user: UserResponse = Depends(get_current_user),
    tool_service: MCPToolService = Depends(get_tool_service),
) -> dict:
    """Columns of a table: name, type, nullability, primary key."""
    result = await tool_service.describe_table(
        DescribeTableRequest(database_id=database_id, table_name=table_name),
        user_id=current_user.id,
    )
    return result.model_dump()


@router.get("/{database_id}/tables/{table_name}/rows")
async def browse_rows(
    database_id: str,
    table_name: str = Path(pattern=IDENTIFIER_PATTERN),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
    order_by: str | None = Query(default=None, max_length=63),
    descending: bool = Query(default=False),
    current_user: UserResponse = Depends(get_current_user),
    tool_service: MCPToolService = Depends(get_tool_service),
) -> dict:
    """One page of a table's rows, optionally sorted by a column, with the total count."""
    result = await tool_service.browse_rows(
        BrowseRowsRequest(
            database_id=database_id, table_name=table_name, offset=offset,
            limit=limit, order_by=order_by, descending=descending,
        ),
        user_id=current_user.id,
    )
    return {**result.model_dump(mode="json"), "offset": offset, "limit": limit}


@router.post("/{database_id}/sql")
async def run_sql(
    database_id: str,
    body: SqlRequest,
    current_user: UserResponse = Depends(get_current_user),
    tool_service: MCPToolService = Depends(get_tool_service),
) -> dict:
    """
    Run SQL typed by the user. SELECTs return at most SQL_EDITOR_MAX_ROWS
    rows. Writes are dry-run and answered with 409 confirmation_required
    (rows affected, undo availability); POST /sql/confirm executes them.
    """
    result = await tool_service.run_sql(
        RunSqlRequest(database_id=database_id, sql=cap_select(body.sql, SQL_EDITOR_MAX_ROWS + 1)),
        user_id=current_user.id,
    )
    rows = result.rows or []
    return {
        "columns": result.columns or [],
        "rows": rows[:SQL_EDITOR_MAX_ROWS],
        "truncated": len(rows) > SQL_EDITOR_MAX_ROWS,
    }


@router.get("/{database_id}/usage")
async def storage_usage(
    database_id: str,
    current_user: UserResponse = Depends(get_current_user),
    tool_service: MCPToolService = Depends(get_tool_service),
) -> dict:
    """Storage used by this database and its limit (null when unlimited)."""
    await tool_service.permission_service.check_access(database_id=database_id, user_id=current_user.id)
    size = await tool_service.postgres_executor.database_size(tool_service.schema_for(database_id))
    return {"size_bytes": size, "limit_bytes": tool_service.max_database_bytes or None}


@router.get("/{database_id}/undo")
async def undo_status(
    database_id: str,
    current_user: UserResponse = Depends(get_current_user),
    tool_service: MCPToolService = Depends(get_tool_service),
) -> dict:
    """The latest confirmed change and whether it can be undone."""
    await tool_service.permission_service.check_access(database_id=database_id, user_id=current_user.id)
    record = await tool_service.undo_store.get(database_id)
    if record is None:
        return {"available": False, "sql": None}
    return {
        "available": record.get("plan") is not None,
        "sql": record["sql"],
        "created_at": record["created_at"],
        "by_you": record["user_id"] == current_user.id,
        "unavailable_reason": record.get("unavailable_reason"),
    }


@router.post("/{database_id}/undo")
async def undo_last_change(
    database_id: str,
    current_user: UserResponse = Depends(get_current_user),
    tool_service: MCPToolService = Depends(get_tool_service),
) -> dict:
    """Reverse the latest confirmed change (write access needed)."""
    return await tool_service.undo_last_write(database_id, current_user.id)


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
            max_database_bytes=tool_service.max_database_bytes,
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
    await tool_service.discard_undo(database_id)
    return response


def _jsonable(v):
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    return str(v)
