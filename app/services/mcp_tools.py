import time
from datetime import datetime, timezone

from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

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
from app.models.tool_call_log import ToolCallEntry, ToolCallSource, ToolCallStatus
from app.services.permission_service import PermissionService
from app.services.postgres_executor import PostgresExecutor
from app.services.sql_validator import SQLValidator
from app.services.cache_service import CacheService
from app.services.rate_limiter import RateLimiter


class MCPToolService:
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

    async def list_schemas(
        self,
        request: ListSchemasRequest,
        user_id: str,
    ) -> ListSchemasResponse:
        start = time.time()

        allowed, remaining = await self.rate_limiter.check_and_increment(
            user_id
        )
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded. Retry in 60 seconds.",
                headers={"Retry-After": "60"},
            )

        try:
            await self.permission_service.check_access(
                database_id=request.database_id,
                user_id=user_id,
                required_level=AccessLevel.READ,
            )

            cached = await self.cache_service.get_mcp_result(
                database_id=request.database_id,
                tool_name="list_schemas",
                args=request.model_dump(),
            )

            if cached:
                return ListSchemasResponse(**cached)

            schema_name = f"tenant_{request.database_id.lower()}"
            schemas = await self.postgres_executor.list_schemas(schema_name)

            result = ListSchemasResponse(schemas=schemas)

            await self.cache_service.set_mcp_result(
                database_id=request.database_id,
                tool_name="list_schemas",
                args=request.model_dump(),
                result=result.model_dump(),
            )

            duration_ms = int((time.time() - start) * 1000)

            await self._log_tool_call(
                user_id=user_id,
                database_id=request.database_id,
                tool_name="list_schemas",
                args=request.model_dump(),
                result=result.model_dump(),
                status=ToolCallStatus.SUCCESS,
                duration_ms=duration_ms,
                source=ToolCallSource.MCP,
            )

            return result

        except HTTPException as e:
            duration_ms = int((time.time() - start) * 1000)

            await self._log_tool_call(
                user_id=user_id,
                database_id=request.database_id,
                tool_name="list_schemas",
                args=request.model_dump(),
                result={"error": e.detail},
                status=ToolCallStatus.DENIED,
                duration_ms=duration_ms,
                source=ToolCallSource.MCP,
            )

            raise

        except Exception as e:
            duration_ms = int((time.time() - start) * 1000)

            await self._log_tool_call(
                user_id=user_id,
                database_id=request.database_id,
                tool_name="list_schemas",
                args=request.model_dump(),
                result={"error": str(e)},
                status=ToolCallStatus.ERROR,
                duration_ms=duration_ms,
                source=ToolCallSource.MCP,
            )

            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Internal server error",
            )

    async def describe_table(
        self,
        request: DescribeTableRequest,
        user_id: str,
    ) -> DescribeTableResponse:
        start = time.time()

        allowed, remaining = await self.rate_limiter.check_and_increment(
            user_id
        )
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded",
                headers={"Retry-After": "60"},
            )

        try:
            await self.permission_service.check_access(
                database_id=request.database_id,
                user_id=user_id,
                required_level=AccessLevel.READ,
            )

            cached = await self.cache_service.get_mcp_result(
                database_id=request.database_id,
                tool_name="describe_table",
                args=request.model_dump(),
            )

            if cached:
                return DescribeTableResponse(**cached)

            columns = await self.postgres_executor.describe_table(
                schema_name=request.schema_name,
                table_name=request.table_name,
            )

            column_infos = [
                ColumnInfo(name=name, type=type_, nullable=nullable)
                for name, type_, nullable in columns
            ]

            result = DescribeTableResponse(
                table_name=request.table_name,
                columns=column_infos,
            )

            await self.cache_service.set_mcp_result(
                database_id=request.database_id,
                tool_name="describe_table",
                args=request.model_dump(),
                result=result.model_dump(),
            )

            duration_ms = int((time.time() - start) * 1000)

            await self._log_tool_call(
                user_id=user_id,
                database_id=request.database_id,
                tool_name="describe_table",
                args=request.model_dump(),
                result=result.model_dump(),
                status=ToolCallStatus.SUCCESS,
                duration_ms=duration_ms,
                source=ToolCallSource.MCP,
            )

            return result

        except HTTPException as e:
            duration_ms = int((time.time() - start) * 1000)

            await self._log_tool_call(
                user_id=user_id,
                database_id=request.database_id,
                tool_name="describe_table",
                args=request.model_dump(),
                result={"error": e.detail},
                status=ToolCallStatus.DENIED,
                duration_ms=duration_ms,
                source=ToolCallSource.MCP,
            )

            raise

        except Exception as e:
            duration_ms = int((time.time() - start) * 1000)

            await self._log_tool_call(
                user_id=user_id,
                database_id=request.database_id,
                tool_name="describe_table",
                args=request.model_dump(),
                result={"error": str(e)},
                status=ToolCallStatus.ERROR,
                duration_ms=duration_ms,
                source=ToolCallSource.MCP,
            )

            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Internal server error",
            )

    async def sample_rows(
        self,
        request: SampleRowsRequest,
        user_id: str,
    ) -> SampleRowsResponse:
        start = time.time()

        allowed, remaining = await self.rate_limiter.check_and_increment(
            user_id
        )
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded",
                headers={"Retry-After": "60"},
            )

        try:
            await self.permission_service.check_access(
                database_id=request.database_id,
                user_id=user_id,
                required_level=AccessLevel.READ,
            )

            cached = await self.cache_service.get_mcp_result(
                database_id=request.database_id,
                tool_name="sample_rows",
                args=request.model_dump(),
            )

            if cached:
                return SampleRowsResponse(**cached)

            columns, rows = await self.postgres_executor.sample_rows(
                schema_name=request.schema_name,
                table_name=request.table_name,
                limit=request.limit,
            )

            result = SampleRowsResponse(columns=columns, rows=rows)

            await self.cache_service.set_mcp_result(
                database_id=request.database_id,
                tool_name="sample_rows",
                args=request.model_dump(),
                result=result.model_dump(),
            )

            duration_ms = int((time.time() - start) * 1000)

            await self._log_tool_call(
                user_id=user_id,
                database_id=request.database_id,
                tool_name="sample_rows",
                args=request.model_dump(),
                result=result.model_dump(),
                status=ToolCallStatus.SUCCESS,
                duration_ms=duration_ms,
                source=ToolCallSource.MCP,
            )

            return result

        except HTTPException as e:
            duration_ms = int((time.time() - start) * 1000)

            await self._log_tool_call(
                user_id=user_id,
                database_id=request.database_id,
                tool_name="sample_rows",
                args=request.model_dump(),
                result={"error": e.detail},
                status=ToolCallStatus.DENIED,
                duration_ms=duration_ms,
                source=ToolCallSource.MCP,
            )

            raise

        except Exception as e:
            duration_ms = int((time.time() - start) * 1000)

            await self._log_tool_call(
                user_id=user_id,
                database_id=request.database_id,
                tool_name="sample_rows",
                args=request.model_dump(),
                result={"error": str(e)},
                status=ToolCallStatus.ERROR,
                duration_ms=duration_ms,
                source=ToolCallSource.MCP,
            )

            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Internal server error",
            )

    async def run_sql(
        self,
        request: RunSqlRequest,
        user_id: str,
    ) -> RunSqlResponse:
        start = time.time()

        allowed, remaining = await self.rate_limiter.check_and_increment(
            user_id
        )
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded",
                headers={"Retry-After": "60"},
            )

        try:
            access_level = await self.permission_service.check_access(
                database_id=request.database_id,
                user_id=user_id,
                required_level=AccessLevel.READ,
            )

            allow_write = access_level in (
                AccessLevel.WRITE,
                AccessLevel.OWNER,
            )

            operation, _ = self.sql_validator.validate_and_parse(
                request.sql,
                allow_write=allow_write,
            )

            schema_name = f"tenant_{request.database_id.lower()}"

            result = await self.postgres_executor.execute_sql(
                sql=request.sql,
                schema_name=schema_name,
                user_id=user_id,
            )

            duration_ms = int((time.time() - start) * 1000)

            await self._log_tool_call(
                user_id=user_id,
                database_id=request.database_id,
                tool_name="run_sql",
                args={"sql": request.sql[:500]},
                result={
                    "operation": operation.value,
                    "row_count": len(result.rows) if result.rows else 0,
                },
                status=ToolCallStatus.SUCCESS,
                duration_ms=duration_ms,
                source=ToolCallSource.MCP,
            )

            await self.cache_service.invalidate_mcp_cache(
                request.database_id
            )

            return result

        except HTTPException as e:
            duration_ms = int((time.time() - start) * 1000)

            await self._log_tool_call(
                user_id=user_id,
                database_id=request.database_id,
                tool_name="run_sql",
                args={"sql": request.sql[:500]},
                result={"error": e.detail},
                status=ToolCallStatus.DENIED,
                duration_ms=duration_ms,
                source=ToolCallSource.MCP,
            )

            raise

        except Exception as e:
            duration_ms = int((time.time() - start) * 1000)

            await self._log_tool_call(
                user_id=user_id,
                database_id=request.database_id,
                tool_name="run_sql",
                args={"sql": request.sql[:500]},
                result={"error": str(e)},
                status=ToolCallStatus.ERROR,
                duration_ms=duration_ms,
                source=ToolCallSource.MCP,
            )

            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Internal server error",
            )

    async def _log_tool_call(
        self,
        user_id: str,
        database_id: str,
        tool_name: str,
        args: dict,
        result: dict | None,
        status: ToolCallStatus,
        duration_ms: int,
        source: ToolCallSource,
    ) -> None:
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
                "source": source.value,
            }
        )