import logging
import time
from datetime import datetime, timezone
from typing import Awaitable, Callable, TypeVar

from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel

from app.models.contracts import AccessLevel
from app.models.mcp_tools import (
    ColumnInfo,
    DescribeTableRequest,
    DescribeTableResponse,
    ListSchemasRequest,
    ListSchemasResponse,
    RunSqlRequest,
    RunSqlResponse,
    SampleRowsRequest,
    SampleRowsResponse,
)
from app.models.tool_call_log import ToolCallSource, ToolCallStatus
from app.services.permission_service import PermissionService
from app.services.postgres_executor import PostgresExecutor
from app.services.sql_validator import SQLValidator, SqlOperationType
from app.services.cache_service import CacheService
from app.services.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

R = TypeVar("R", bound=BaseModel)


class MCPToolService:
    """
    The four database tools exposed to the agent.

    Every call goes through `_run_tool`, which applies the same pipeline:
    rate limit -> permission check -> cache lookup -> execute -> cache store
    -> audit log, with uniform error mapping.
    """

    def __init__(
        self,
        postgres_executor: PostgresExecutor,
        permission_service: PermissionService,
        mongo_database: AsyncIOMotorDatabase,
        cache_service: CacheService,
        rate_limiter: RateLimiter,
    ) -> None:
        self.postgres_executor = postgres_executor
        self.permission_service = permission_service
        self.mongo_database = mongo_database
        self.sql_validator = SQLValidator()
        self.tool_call_logs = mongo_database["tool_call_logs"]
        self.cache_service = cache_service
        self.rate_limiter = rate_limiter

    @staticmethod
    def schema_for(database_id: str) -> str:
        """The tenant schema is derived from the database id, never from the caller."""
        return f"tenant_{database_id.lower()}"

    async def list_schemas(
        self,
        request: ListSchemasRequest,
        user_id: str,
    ) -> ListSchemasResponse:
        async def execute(_: AccessLevel) -> ListSchemasResponse:
            schemas = await self.postgres_executor.list_schemas(
                self.schema_for(request.database_id)
            )
            return ListSchemasResponse(schemas=schemas)

        return await self._run_tool(
            tool_name="list_schemas",
            request=request,
            user_id=user_id,
            execute=execute,
            response_type=ListSchemasResponse,
            cacheable=True,
        )

    async def describe_table(
        self,
        request: DescribeTableRequest,
        user_id: str,
    ) -> DescribeTableResponse:
        async def execute(_: AccessLevel) -> DescribeTableResponse:
            columns = await self.postgres_executor.describe_table(
                schema_name=self.schema_for(request.database_id),
                table_name=request.table_name,
            )
            return DescribeTableResponse(
                table_name=request.table_name,
                columns=[
                    ColumnInfo(name=name, type=type_, nullable=nullable)
                    for name, type_, nullable in columns
                ],
            )

        return await self._run_tool(
            tool_name="describe_table",
            request=request,
            user_id=user_id,
            execute=execute,
            response_type=DescribeTableResponse,
            cacheable=True,
        )

    async def sample_rows(
        self,
        request: SampleRowsRequest,
        user_id: str,
    ) -> SampleRowsResponse:
        async def execute(_: AccessLevel) -> SampleRowsResponse:
            columns, rows = await self.postgres_executor.sample_rows(
                schema_name=self.schema_for(request.database_id),
                table_name=request.table_name,
                limit=request.limit,
            )
            return SampleRowsResponse(columns=columns, rows=rows)

        return await self._run_tool(
            tool_name="sample_rows",
            request=request,
            user_id=user_id,
            execute=execute,
            response_type=SampleRowsResponse,
            cacheable=True,
        )

    async def run_sql(
        self,
        request: RunSqlRequest,
        user_id: str,
    ) -> RunSqlResponse:
        operation: SqlOperationType | None = None

        async def execute(access_level: AccessLevel) -> RunSqlResponse:
            nonlocal operation
            allow_write = access_level in (AccessLevel.WRITE, AccessLevel.OWNER)

            operation, _ = self.sql_validator.validate_and_parse(
                request.sql,
                allow_write=allow_write,
            )

            # Every write needs an explicit user confirmation. The agent
            # never sets `confirmed`; only the /sql/confirm route does.
            if operation in SQLValidator.WRITE_OPERATIONS and not request.confirmed:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "confirmation_required",
                        "operation": operation.value,
                        "sql": request.sql,
                        "message": (
                            f"This {operation.value.upper()} statement modifies the database "
                            "and needs your confirmation before it runs."
                        ),
                    },
                )

            result = await self.postgres_executor.execute_sql(
                sql=request.sql,
                schema_name=self.schema_for(request.database_id),
            )

            # Only writes can change what the read-only tools would return.
            if operation in SQLValidator.WRITE_OPERATIONS:
                await self.cache_service.invalidate_mcp_cache(request.database_id)

            return result

        def summarize(result: RunSqlResponse) -> dict:
            return {
                "operation": operation.value if operation else None,
                "row_count": len(result.rows) if result.rows else 0,
            }

        return await self._run_tool(
            tool_name="run_sql",
            request=request,
            user_id=user_id,
            execute=execute,
            response_type=RunSqlResponse,
            cacheable=False,
            log_args={"sql": request.sql[:500]},
            log_result=summarize,
        )

    async def _run_tool(
        self,
        *,
        tool_name: str,
        request: BaseModel,
        user_id: str,
        execute: Callable[[AccessLevel], Awaitable[R]],
        response_type: type[R],
        cacheable: bool,
        log_args: dict | None = None,
        log_result: Callable[[R], dict] | None = None,
    ) -> R:
        start = time.time()
        database_id: str = getattr(request, "database_id")
        args = request.model_dump()
        log_args = log_args if log_args is not None else args

        allowed, _ = await self.rate_limiter.check_and_increment(user_id)
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded. Retry in 60 seconds.",
                headers={"Retry-After": "60"},
            )

        try:
            access_level = await self.permission_service.check_access(
                database_id=database_id,
                user_id=user_id,
                required_level=AccessLevel.READ,
            )

            if cacheable:
                cached = await self.cache_service.get_mcp_result(
                    database_id=database_id, tool_name=tool_name, args=args
                )
                if cached:
                    return response_type(**cached)

            result = await execute(access_level)

            if cacheable:
                await self.cache_service.set_mcp_result(
                    database_id=database_id,
                    tool_name=tool_name,
                    args=args,
                    result=result.model_dump(),
                )

            await self._log_tool_call(
                user_id=user_id,
                database_id=database_id,
                tool_name=tool_name,
                args=log_args,
                result=log_result(result) if log_result else result.model_dump(),
                status=ToolCallStatus.SUCCESS,
                duration_ms=self._elapsed_ms(start),
            )
            return result

        except HTTPException as e:
            await self._log_tool_call(
                user_id=user_id,
                database_id=database_id,
                tool_name=tool_name,
                args=log_args,
                result={"error": e.detail},
                status=ToolCallStatus.PENDING if e.status_code == status.HTTP_409_CONFLICT else ToolCallStatus.DENIED,
                duration_ms=self._elapsed_ms(start),
            )
            raise

        except Exception as e:
            logger.exception("Tool %s failed for database %s", tool_name, database_id)
            await self._log_tool_call(
                user_id=user_id,
                database_id=database_id,
                tool_name=tool_name,
                args=log_args,
                result={"error": str(e)},
                status=ToolCallStatus.ERROR,
                duration_ms=self._elapsed_ms(start),
            )
            # Postgres errors (bad column, syntax error, permission denied)
            # are useful to the agent for self-correction; surface the
            # message but not as a 500.
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Query failed: {str(e)}",
            )

    @staticmethod
    def _elapsed_ms(start: float) -> int:
        return int((time.time() - start) * 1000)

    async def _log_tool_call(
        self,
        user_id: str,
        database_id: str,
        tool_name: str,
        args: dict,
        result: dict | None,
        status: ToolCallStatus,
        duration_ms: int,
    ) -> None:
        try:
            await self.tool_call_logs.insert_one(
                {
                    "user_id": user_id,
                    "database_id": database_id,
                    "tool_name": tool_name,
                    "args": args,
                    "result": result,
                    "status": status.value,
                    "duration_ms": duration_ms,
                    "timestamp": datetime.now(timezone.utc),
                    "source": ToolCallSource.MCP.value,
                }
            )
        except Exception:
            logger.exception("Failed to write tool_call_logs entry")
