from fastapi import HTTPException, status

from app.core.database import DatabaseManager
from app.models.mcp_tools import RunSqlResponse


class PostgresExecutor:
    """
    Runs tenant-scoped SQL.

    Every user-facing statement executes inside a transaction with
    `SET LOCAL ROLE <tenant>` and `SET LOCAL search_path`, so both settings
    are confined to that transaction and cannot leak to other users sharing
    the pooled connection.
    """

    STATEMENT_TIMEOUT = "30s"

    def __init__(self, database_manager: DatabaseManager) -> None:
        self.database_manager = database_manager

    async def execute_sql(
        self,
        sql: str,
        schema_name: str,
    ) -> RunSqlResponse:
        """
        Execute one SQL statement as the tenant role in the tenant schema.

        Returns columns and rows, or (None, None) when the statement produced
        no result rows.
        """
        self.database_manager._validate_schema_name(schema_name)

        async with self.database_manager.postgres_connection() as connection:
            async with connection.transaction():
                await self._enter_tenant(connection, schema_name)
                rows = await connection.fetch(sql)

        if not rows:
            return RunSqlResponse(columns=None, rows=None)

        columns = list(rows[0].keys())
        rows_list = [list(row.values()) for row in rows]

        return RunSqlResponse(columns=columns, rows=rows_list)

    async def list_schemas(
        self,
        schema_name: str,
    ) -> list[str]:
        """
        List all tables in the tenant schema.
        """
        async with self.database_manager.postgres_connection() as connection:
            rows = await connection.fetch(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = $1
                ORDER BY table_name
                """,
                schema_name,
            )

        return [row["table_name"] for row in rows]

    async def describe_table(
        self,
        schema_name: str,
        table_name: str,
    ) -> list[tuple[str, str, bool]]:
        """
        Get column information for a table.

        Returns list of (column_name, type, nullable)
        """
        async with self.database_manager.postgres_connection() as connection:
            rows = await connection.fetch(
                """
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema = $1 AND table_name = $2
                ORDER BY ordinal_position
                """,
                schema_name,
                table_name,
            )

        if not rows:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Table '{table_name}' not found",
            )

        return [
            (row["column_name"], row["data_type"], row["is_nullable"] == "YES")
            for row in rows
        ]

    async def sample_rows(
        self,
        schema_name: str,
        table_name: str,
        limit: int = 5,
    ) -> tuple[list[str], list[list]]:
        """
        Sample rows from a table.

        Returns (column_names, rows)
        """
        self.database_manager._validate_schema_name(schema_name)

        async with self.database_manager.postgres_connection() as connection:
            async with connection.transaction():
                exists = await connection.fetchval(
                    """
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = $1 AND table_name = $2
                    """,
                    schema_name,
                    table_name,
                )
                if not exists:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Table '{table_name}' not found",
                    )

                await self._enter_tenant(connection, schema_name)

                # table_name is validated against IDENTIFIER_PATTERN by the
                # request model and confirmed to exist above, so quoting it
                # here is safe.
                rows = await connection.fetch(
                    f'SELECT * FROM "{table_name}" LIMIT $1',
                    limit,
                )

        if not rows:
            return [], []

        columns = list(rows[0].keys())
        rows_list = [list(row.values()) for row in rows]

        return columns, rows_list

    async def import_rows(
        self,
        schema_name: str,
        table_name: str,
        columns: list[tuple[str, str]],
        rows: list[list],
        mode: str,
    ) -> int:
        """
        Bulk-load rows into a tenant table.

        mode: "create"  - table must not exist
              "append"  - table must exist; columns are matched by name
              "replace" - drop and recreate

        Column names/types come from ImportService (validated identifiers and
        a fixed allow-list of types), so interpolating them is safe.
        """
        self.database_manager._validate_schema_name(schema_name)
        if mode not in ("create", "append", "replace"):
            raise ValueError("mode must be create, append or replace")

        col_defs = ", ".join(f'"{name}" {type_}' for name, type_ in columns)
        col_names = [name for name, _ in columns]

        async with self.database_manager.postgres_connection() as connection:
            async with connection.transaction():
                await self._enter_tenant(connection, schema_name)
                await connection.execute("SET LOCAL statement_timeout = '10min'")

                exists = await connection.fetchval(
                    "SELECT 1 FROM information_schema.tables WHERE table_schema = $1 AND table_name = $2",
                    schema_name, table_name,
                )

                if mode == "create" and exists:
                    raise HTTPException(status.HTTP_409_CONFLICT, f"Table '{table_name}' already exists")
                if mode == "append" and not exists:
                    raise HTTPException(status.HTTP_404_NOT_FOUND, f"Table '{table_name}' not found")

                if mode == "replace" and exists:
                    await connection.execute(f'DROP TABLE "{table_name}"')
                    exists = False

                if not exists:
                    await connection.execute(f'CREATE TABLE "{table_name}" ({col_defs})')

                if rows:
                    await connection.copy_records_to_table(
                        table_name, records=rows, columns=col_names, schema_name=schema_name
                    )

        return len(rows)

    async def _enter_tenant(self, connection, schema_name: str) -> None:
        # schema_name already matched tenant_[a-f0-9]{24}, so it is safe to
        # interpolate. SET LOCAL cannot take bind parameters.
        await connection.execute(f'SET LOCAL ROLE "{schema_name}"')
        await connection.execute(f'SET LOCAL search_path TO "{schema_name}"')
        await connection.execute(
            f"SET LOCAL statement_timeout = '{self.STATEMENT_TIMEOUT}'"
        )
