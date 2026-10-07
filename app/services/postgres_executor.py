from dataclasses import dataclass

from fastapi import HTTPException, status

from app.core.database import DatabaseManager
from app.models.mcp_tools import RunSqlResponse
from app.services import undo
from app.services.sql_validator import ParsedStatement, SqlOperationType


@dataclass
class WritePreview:
    rows_affected: int | None
    undo_available: bool
    undo_unavailable_reason: str | None


@dataclass
class WriteOutcome:
    result: RunSqlResponse
    rows_affected: int | None
    plan: undo.UndoPlan | None
    undo_unavailable_reason: str | None


def storage_limit_error(limit_bytes: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        detail=(
            f"This change would take the database over its {limit_bytes // (1024 * 1024)} MB "
            "storage limit, so it was rolled back. Delete data or drop tables to make room."
        ),
    )


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

    async def preview_write(
        self,
        sql: str,
        schema_name: str,
        parsed: ParsedStatement,
        max_undo_bytes: int,
    ) -> WritePreview:
        """
        Run a write in a transaction that is always rolled back, to report
        how many rows it would affect and whether it could be undone. SQL
        errors surface here, before the user is asked to confirm.
        """
        self.database_manager._validate_schema_name(schema_name)

        async with self.database_manager.postgres_connection() as connection:
            transaction = connection.transaction()
            await transaction.start()
            try:
                await self._enter_tenant(connection, schema_name)
                assessment = await undo.assess(connection, schema_name, parsed, max_undo_bytes)
                rows_affected = await self._run_counting(connection, sql, parsed, assessment)
            finally:
                await transaction.rollback()

        return WritePreview(
            rows_affected=rows_affected,
            undo_available=assessment.unavailable_reason is None,
            undo_unavailable_reason=assessment.unavailable_reason,
        )

    async def execute_write(
        self,
        sql: str,
        schema_name: str,
        parsed: ParsedStatement,
        undo_id: str,
        max_undo_bytes: int,
        max_database_bytes: int,
    ) -> WriteOutcome:
        """
        Execute a confirmed write. In the same transaction: discard the
        previous undo snapshots, snapshot what this write can destroy, run
        it, and roll everything back if the database grew past its limit.
        """
        self.database_manager._validate_schema_name(schema_name)

        try:
            return await self._execute_write(
                sql, schema_name, parsed, undo_id, max_undo_bytes, max_database_bytes
            )
        except HTTPException as e:
            if e.status_code == status.HTTP_413_CONTENT_TOO_LARGE:
                await self._reclaim(schema_name, parsed.write_targets)
            raise

    async def _execute_write(
        self,
        sql: str,
        schema_name: str,
        parsed: ParsedStatement,
        undo_id: str,
        max_undo_bytes: int,
        max_database_bytes: int,
    ) -> WriteOutcome:
        async with self.database_manager.postgres_connection() as connection:
            async with connection.transaction():
                await self._enter_tenant(connection, schema_name)
                size_before = await self._schema_size(connection, schema_name)

                await undo.discard_snapshots(connection, schema_name)
                assessment = await undo.assess(connection, schema_name, parsed, max_undo_bytes)
                plan = None
                if assessment.unavailable_reason is None:
                    plan = await undo.take_snapshots(connection, assessment, undo_id)

                discarded = await self._count_discarded(connection, parsed, assessment)
                counters_before = await undo.row_change_counters(connection, schema_name)
                statement = await connection.prepare(sql)
                rows = await statement.fetch()
                counters_after = await undo.row_change_counters(connection, schema_name)
                rows_affected = discarded if discarded is not None else _rows_affected(statement.get_statusmsg())

                unavailable_reason = assessment.unavailable_reason
                if plan is not None:
                    plan = await undo.complete_plan(connection, schema_name, assessment, plan)
                    missed = undo.unsnapshotted_changes(counters_before, counters_after, plan)
                    if missed:
                        await undo.discard_snapshots(connection, schema_name)
                        plan = None
                        unavailable_reason = f"it also changed {', '.join(missed)}"

                if max_database_bytes > 0:
                    size_after = await self._schema_size(connection, schema_name)
                    # Only refuse growth: deletes must work even over the limit.
                    if size_after > max_database_bytes and size_after > size_before:
                        raise storage_limit_error(max_database_bytes)

        result = (
            RunSqlResponse(columns=list(rows[0].keys()), rows=[list(r.values()) for r in rows])
            if rows else RunSqlResponse(columns=None, rows=None)
        )
        result.rows_affected = rows_affected
        result.undo_available = plan is not None
        return WriteOutcome(result, rows_affected, plan, unavailable_reason)

    async def _reclaim(self, schema_name: str, tables: list[str]) -> None:
        """
        A rolled-back write leaves its pages in the table files until VACUUM,
        so the database would still measure over its limit. Give them back.
        The app role is a member of the tenant role, which owns the tables.
        """
        async with self.database_manager.postgres_connection() as connection:
            for table in tables:
                try:
                    await connection.execute(f"VACUUM {undo._q(schema_name)}.{undo._q(table)}")
                except Exception:
                    pass  # the table may not exist (it was created by the rolled-back write)

    async def apply_undo(self, schema_name: str, plan: undo.UndoPlan) -> None:
        self.database_manager._validate_schema_name(schema_name)
        async with self.database_manager.postgres_connection() as connection:
            async with connection.transaction():
                await self._enter_tenant(connection, schema_name)
                await undo.apply(connection, schema_name, plan)
                await undo.discard_snapshots(connection, schema_name)

    async def discard_undo(self, schema_name: str) -> None:
        self.database_manager._validate_schema_name(schema_name)
        async with self.database_manager.postgres_connection() as connection:
            async with connection.transaction():
                await self._enter_tenant(connection, schema_name)
                await undo.discard_snapshots(connection, schema_name)

    async def database_size(self, schema_name: str) -> int:
        """Bytes used by the tenant's tables, indexes and TOAST (undo snapshots excluded)."""
        self.database_manager._validate_schema_name(schema_name)
        async with self.database_manager.postgres_connection() as connection:
            return await self._schema_size(connection, schema_name)

    @staticmethod
    async def _schema_size(connection, schema_name: str) -> int:
        return await connection.fetchval(
            """
            SELECT COALESCE(sum(pg_total_relation_size(c.oid)), 0)::bigint
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = $1 AND c.relkind IN ('r', 'm') AND c.relname NOT LIKE $2
            """,
            schema_name,
            undo.SNAPSHOT_LIKE,
        )

    async def _run_counting(self, connection, sql: str, parsed: ParsedStatement, assessment) -> int | None:
        """Execute `sql` and return how many rows it changes, when that is knowable."""
        discarded = await self._count_discarded(connection, parsed, assessment)
        statement = await connection.prepare(sql)
        await statement.fetch()
        return discarded if discarded is not None else _rows_affected(statement.get_statusmsg())

    @staticmethod
    async def _count_discarded(connection, parsed: ParsedStatement, assessment) -> int | None:
        """For TRUNCATE/DROP TABLE: the rows the target tables hold now. None otherwise."""
        if parsed.operation not in (SqlOperationType.TRUNCATE, SqlOperationType.DROP) or not assessment.tables:
            return None
        total = 0
        for table in assessment.tables:
            total += await connection.fetchval(f"SELECT count(*) FROM {undo._q(table.name)}")
        return total

    async def list_schemas(
        self,
        schema_name: str,
    ) -> list[str]:
        """
        List all tables and views in the tenant schema.
        """
        async with self.database_manager.postgres_connection() as connection:
            rows = await connection.fetch(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = $1 AND table_name NOT LIKE $2
                ORDER BY table_name
                """,
                schema_name,
                undo.SNAPSHOT_LIKE,
            )

        return [row["table_name"] for row in rows]

    async def describe_table(
        self,
        schema_name: str,
        table_name: str,
    ) -> list[tuple[str, str, bool, bool]]:
        """
        Get column information for a table.

        Returns list of (column_name, type, nullable, primary_key)
        """
        async with self.database_manager.postgres_connection() as connection:
            rows = await connection.fetch(
                """
                SELECT c.column_name, c.data_type, c.is_nullable,
                       EXISTS (
                           SELECT 1
                           FROM information_schema.table_constraints tc
                           JOIN information_schema.key_column_usage k
                             ON k.constraint_name = tc.constraint_name
                            AND k.table_schema = tc.table_schema
                            AND k.table_name = tc.table_name
                           WHERE tc.constraint_type = 'PRIMARY KEY'
                             AND tc.table_schema = c.table_schema
                             AND tc.table_name = c.table_name
                             AND k.column_name = c.column_name
                       ) AS primary_key
                FROM information_schema.columns c
                WHERE c.table_schema = $1 AND c.table_name = $2
                ORDER BY c.ordinal_position
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
            (row["column_name"], row["data_type"], row["is_nullable"] == "YES", row["primary_key"])
            for row in rows
        ]

    async def browse_rows(
        self,
        schema_name: str,
        table_name: str,
        offset: int,
        limit: int,
        order_by: str | None,
        descending: bool,
    ) -> tuple[list[str], list[list], int]:
        """
        One page of a table, optionally sorted by one of its columns.

        Returns (column_names, rows, total_row_count).
        """
        columns = [c[0] for c in await self.describe_table(schema_name, table_name)]
        if order_by is not None and order_by not in columns:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown column '{order_by}'",
            )

        table = undo._q(table_name)
        order = f" ORDER BY {undo._q(order_by)} {'DESC' if descending else 'ASC'} NULLS LAST" if order_by else ""
        async with self.database_manager.postgres_connection() as connection:
            async with connection.transaction():
                await self._enter_tenant(connection, schema_name)
                total = await connection.fetchval(f"SELECT count(*) FROM {table}")
                rows = await connection.fetch(
                    f"SELECT * FROM {table}{order} LIMIT $1 OFFSET $2", limit, offset
                )

        return columns, [list(r.values()) for r in rows], total

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
        max_database_bytes: int = 0,
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

        try:
            return await self._import_rows(schema_name, table_name, col_defs, col_names, rows, mode, max_database_bytes)
        except HTTPException as e:
            if e.status_code == status.HTTP_413_CONTENT_TOO_LARGE:
                await self._reclaim(schema_name, [table_name])
            raise

    async def _import_rows(self, schema_name, table_name, col_defs, col_names, rows, mode, max_database_bytes) -> int:
        async with self.database_manager.postgres_connection() as connection:
            async with connection.transaction():
                await self._enter_tenant(connection, schema_name)
                await connection.execute("SET LOCAL statement_timeout = '10min'")
                size_before = await self._schema_size(connection, schema_name)
                # An import is a change undo knows nothing about; restoring an
                # earlier snapshot afterwards would silently discard it.
                await undo.discard_snapshots(connection, schema_name)

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

                if max_database_bytes > 0:
                    size_after = await self._schema_size(connection, schema_name)
                    if size_after > max_database_bytes and size_after > size_before:
                        raise storage_limit_error(max_database_bytes)

        return len(rows)

    async def _enter_tenant(self, connection, schema_name: str) -> None:
        # schema_name already matched tenant_[a-f0-9]{24}, so it is safe to
        # interpolate. SET LOCAL cannot take bind parameters.
        await connection.execute(f'SET LOCAL ROLE "{schema_name}"')
        await connection.execute(f'SET LOCAL search_path TO "{schema_name}"')
        await connection.execute(
            f"SET LOCAL statement_timeout = '{self.STATEMENT_TIMEOUT}'"
        )


def _rows_affected(status_message: str | None) -> int | None:
    """'INSERT 0 3' -> 3, 'UPDATE 2' -> 2; None for tags without a row count we trust."""
    if not status_message:
        return None
    parts = status_message.split()
    if parts[0] in ("INSERT", "UPDATE", "DELETE", "MERGE") and parts[-1].isdigit():
        return int(parts[-1])
    return None
