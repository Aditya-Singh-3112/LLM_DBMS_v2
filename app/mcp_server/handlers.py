import json
import time
from typing import Any

from fastapi import HTTPException, status

from app.models.contracts import AccessLevel
from app.services.mcp_tools import MCPToolService
from app.services.permission_service import PermissionService
from app.services.postgres_executor import PostgresExecutor
from app.services.database_registry import DatabaseRegistryService
from app.models.tool_call_log import ToolCallStatus, ToolCallSource
from app.core.database import get_database_manager

class MCPHandler:
    """
    Handlers for MCP tool calls. These are called by the MCP server
    """
    async def _get_service(self) -> MCPToolService:
        """
        Get the MCP tool service with all the dependencies.
        """
        db_manager = get_database_manager()

        if db_manager.mongo_database is None:
            raise RuntimeError("MongoDB is not initialized")

        if db_manager.postgres_pool is None:
            raise RuntimeError("Postgres pool si not initialized")

        registry_service = DatabaseRegistryService(
            mongo_database = db_manager.mongo_database,
            database_manager = db_manager
        )

        postgres_executor = PostgresExecutor(db_manager)
        permission_service = PermissionService(registry_service)

        return MCPToolService(
            postgres_executor = postgres_executor,
            permission_service = permission_service,
            mongo_database = db_manager.mongo_database
        )

    async def handle_list_schemas(self, arguments: dict) -> str:
        """
        Handle list schemas tool call
        """
        database_id = arguments.get("database_id")
        user_id = arguments.get("user_id")  # Passed by agent

        if not database_id or not user_id:
            raise ValueError("database_id and user_id are required")

        service = await self._get_service()

        from app.models.mcp_tools import ListSchemasRequest

        request = ListSchemasRequest(database_id=database_id)
        response = await service.list_schemas(request, user_id)

        return json.dumps(response.model_dump())

    async def handle_describe_table(self, arguments: dict) -> str:
        """Handle describe_table tool call."""
        database_id = arguments.get("database_id")
        schema_name = arguments.get("schema_name")
        table_name = arguments.get("table_name")
        user_id = arguments.get("user_id")  # Passed by agent

        if not all([database_id, schema_name, table_name, user_id]):
            raise ValueError("All required fields must be provided")

        service = await self._get_service()

        from app.models.mcp_tools import DescribeTableRequest

        request = DescribeTableRequest(
            database_id=database_id,
            schema_name=schema_name,
            table_name=table_name,
        )
        response = await service.describe_table(request, user_id)

        return json.dumps(response.model_dump())

    async def handle_sample_rows(self, arguments: dict) -> str:
        """Handle sample_rows tool call."""
        database_id = arguments.get("database_id")
        schema_name = arguments.get("schema_name")
        table_name = arguments.get("table_name")
        limit = arguments.get("limit", 5)
        user_id = arguments.get("user_id")  # Passed by agent

        if not all([database_id, schema_name, table_name, user_id]):
            raise ValueError("All required fields must be provided")

        service = await self._get_service()

        from app.models.mcp_tools import SampleRowsRequest

        request = SampleRowsRequest(
            database_id=database_id,
            schema_name=schema_name,
            table_name=table_name,
            limit=limit,
        )
        response = await service.sample_rows(request, user_id)

        return json.dumps(response.model_dump())

    async def handle_run_sql(self, arguments: dict) -> str:
        """Handle run_sql tool call."""
        database_id = arguments.get("database_id")
        sql = arguments.get("sql")
        user_id = arguments.get("user_id")  # Passed by agent

        if not all([database_id, sql, user_id]):
            raise ValueError("All required fields must be provided")

        service = await self._get_service()

        from app.models.mcp_tools import RunSqlRequest

        request = RunSqlRequest(database_id=database_id, sql=sql)
        response = await service.run_sql(request, user_id)

        return json.dumps(response.model_dump())