from enum import Enum

import sqlparse
from sqlparse import tokens as T
from fastapi import HTTPException, status


class SqlOperationType(str, Enum):
    SELECT = "select"
    INSERT = "insert"
    UPDATE = "update"
    DELETE = "delete"
    CREATE = "create"


class SqlValidationError(HTTPException):
    def __init__(self, detail: str) -> None:
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail,
        )


class SQLValidator:
    """
    Allow-list validator for user SQL.

    Exactly one statement is permitted, and it must be a SELECT or (for write
    access) INSERT/UPDATE/DELETE or CREATE TABLE/INDEX/VIEW. Data-modifying
    CTEs count as writes. Every other statement type (DROP, ALTER, GRANT,
    SET, COPY, CALL, DO, ...) is rejected.

    Postgres itself enforces tenant isolation via per-tenant roles; this
    class only decides what *kind* of statement may run.
    """

    WRITE_OPERATIONS = {
        SqlOperationType.INSERT,
        SqlOperationType.UPDATE,
        SqlOperationType.DELETE,
        SqlOperationType.CREATE,
    }

    # The only object kinds CREATE may build. Modifiers that can sit between
    # CREATE and the object kind are skipped when checking.
    CREATABLE_OBJECTS = {"TABLE", "INDEX", "VIEW"}
    CREATE_MODIFIERS = {"UNIQUE", "TEMP", "TEMPORARY", "UNLOGGED", "OR", "REPLACE", "MATERIALIZED"}

    # Functions that change session state or reach outside the database.
    BLOCKED_FUNCTIONS = {
        "set_config",
        "pg_sleep",
        "pg_sleep_for",
        "pg_sleep_until",
        "pg_read_file",
        "pg_read_binary_file",
        "pg_ls_dir",
        "pg_stat_file",
        "lo_import",
        "lo_export",
        "pg_terminate_backend",
        "pg_cancel_backend",
        "pg_reload_conf",
        "dblink",
        "dblink_exec",
    }

    def validate_and_parse(
        self,
        sql: str,
        allow_write: bool = False,
    ) -> tuple[SqlOperationType, list[str]]:
        """
        Validate SQL and extract table names.

        Returns:
            Tuple of (operation_type, table_names)

        Raises:
            SqlValidationError: If SQL is invalid or unsafe
        """
        cleaned = sqlparse.format(sql, strip_comments=True).strip().rstrip(";").strip()
        if not cleaned:
            raise SqlValidationError("SQL query cannot be empty")

        statements = [s for s in sqlparse.parse(cleaned) if str(s).strip()]
        if len(statements) != 1:
            raise SqlValidationError("Exactly one SQL statement is allowed")

        statement = statements[0]
        flat = [t for t in statement.flatten() if not t.is_whitespace]

        self._check_blocked_functions(flat)

        operation = self._classify(statement, flat)

        if operation in self.WRITE_OPERATIONS and not allow_write:
            raise SqlValidationError(
                "Write operations are not allowed for this access level"
            )

        return operation, self._extract_table_names(flat)

    @classmethod
    def _classify(cls, statement, flat) -> SqlOperationType:
        top = statement.get_type().upper()

        # sqlparse reports "CREATE OR REPLACE" as a single type/token.
        if top.startswith("CREATE"):
            top = "CREATE"

        if top not in ("SELECT", "INSERT", "UPDATE", "DELETE", "CREATE"):
            raise SqlValidationError(
                f"Statement type '{top}' is not allowed; only SELECT, INSERT, UPDATE, DELETE and CREATE are permitted"
            )

        if top == "CREATE":
            cls._check_create_target(flat)
            return SqlOperationType.CREATE

        # Any DDL or other administrative keyword anywhere in the statement
        # (e.g. inside a CTE or subquery) is rejected outright. Words that
        # are legitimate inside DML (SET in UPDATE, LOCK in SELECT ... FOR
        # UPDATE) are deliberately absent; as statements they are already
        # rejected by the type check above.
        for token in flat:
            if token.ttype in T.Keyword.DDL:
                raise SqlValidationError(f"DDL keyword '{token.value.upper()}' is not allowed")
            if token.ttype in T.Keyword and token.value.upper() in (
                "TRUNCATE", "GRANT", "REVOKE", "COPY", "VACUUM", "CALL",
                "LISTEN", "NOTIFY", "REINDEX", "CLUSTER",
            ):
                raise SqlValidationError(f"Keyword '{token.value.upper()}' is not allowed")

        # Data-modifying CTEs: `WITH x AS (DELETE ... RETURNING *) SELECT ...`
        # parses as SELECT but writes. Any DML keyword promotes the whole
        # statement to a write.
        dml = {
            t.value.upper()
            for t in flat
            if t.ttype in T.Keyword.DML and t.value.upper() in ("INSERT", "UPDATE", "DELETE")
        }
        if dml:
            if "DELETE" in dml:
                return SqlOperationType.DELETE
            if "UPDATE" in dml:
                return SqlOperationType.UPDATE
            return SqlOperationType.INSERT

        return SqlOperationType.SELECT

    @classmethod
    def _check_create_target(cls, flat) -> None:
        """CREATE may only build a TABLE, INDEX or VIEW; nothing else, and no nested DDL."""
        # The leading token may be "CREATE" or "CREATE OR REPLACE"; split it
        # so modifiers are handled uniformly.
        words = [w for t in flat for w in t.value.upper().split()]
        i = 1
        while i < len(words) and words[i] in cls.CREATE_MODIFIERS:
            i += 1
        target = words[i] if i < len(words) else ""
        if target not in cls.CREATABLE_OBJECTS:
            raise SqlValidationError(
                f"CREATE {target or '?'} is not allowed; only CREATE TABLE, INDEX and VIEW are permitted"
            )

        # A second DDL keyword after the leading CREATE (e.g. DROP inside a
        # view body) is never legitimate here.
        for token in flat[1:]:
            if token.ttype in T.Keyword.DDL and not token.value.upper().startswith("CREATE"):
                raise SqlValidationError(f"DDL keyword '{token.value.upper()}' is not allowed")

    @classmethod
    def _check_blocked_functions(cls, flat) -> None:
        for i, token in enumerate(flat[:-1]):
            if token.ttype in (T.Name, T.Keyword, T.Name.Builtin) and flat[i + 1].match(T.Punctuation, "("):
                if token.value.lower() in cls.BLOCKED_FUNCTIONS:
                    raise SqlValidationError(f"Function '{token.value}' is not allowed")

    @staticmethod
    def _extract_table_names(flat) -> list[str]:
        """Best-effort table name extraction for logging; not used for authorization."""
        tables: set[str] = set()
        for i, token in enumerate(flat[:-1]):
            if token.ttype in T.Keyword and token.value.upper() in ("FROM", "INTO", "UPDATE", "JOIN"):
                nxt = flat[i + 1]
                if nxt.ttype in (T.Name, None) and not nxt.match(T.Punctuation, "("):
                    tables.add(nxt.value.strip('"'))
        return sorted(tables)
