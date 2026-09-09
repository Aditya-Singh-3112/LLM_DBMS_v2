from app.core.database import DatabaseManager
from app.models.mcp_tools import RunSqlResponse


class PostgresExecutor:
    def __init__(self, database_manager: DatabaseManager) -> None:
        self.database_manager = database_manager

    async def execute_sql(
        self,
        sql: str,
        schema_name: str,
        user_id: str,
    ) -> RunSqlResponse:
        """
        Execute SQL in the tenant schema with RLS context.
        
        Args:
            sql: The SQL query
            schema_name: The tenant schema (e.g., tenant_<id>)
            user_id: The current user's ID (set as RLS context)
            
        Returns:
            Query results (columns and rows) or None if no rows returned
        """
        async with self.database_manager.postgres_connection() as connection:
            await connection.execute(
                f"SET search_path TO {schema_name}, public;"
            )
            await connection.execute(
                f"SELECT set_config('app.current_user_id', $1, false);",
                user_id,
            )

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
        async with self.database_manager.postgres_connection() as connection:
            await connection.execute(
                f"SET search_path TO {schema_name}, public;"
            )

            rows = await connection.fetch(
                f'SELECT * FROM "{table_name}" LIMIT $1',
                limit,
            )

        if not rows:
            return [], []

        columns = list(rows[0].keys())
        rows_list = [list(row.values()) for row in rows]

        return columns, rows_list