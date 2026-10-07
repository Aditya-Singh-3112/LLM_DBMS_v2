from dataclasses import dataclass
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
    ALTER = "alter"
    DROP = "drop"
    TRUNCATE = "truncate"


class SqlValidationError(HTTPException):
    def __init__(self, detail: str) -> None:
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail,
        )


@dataclass
class ParsedStatement:
    operation: SqlOperationType
    # Every table name mentioned after FROM/INTO/UPDATE/JOIN (for logging).
    tables: list[str]
    # The relations the statement modifies, in statement order.
    write_targets: list[str]


class SQLValidator:
    """
    Allow-list validator for user SQL.

    Exactly one statement is permitted:
      - read access:  SELECT
      - write access: + INSERT/UPDATE/DELETE, CREATE TABLE/INDEX/VIEW
      - owners:       + ALTER TABLE, DROP TABLE/VIEW/INDEX, TRUNCATE
    Data-modifying CTEs count as writes. Every other statement type (GRANT,
    SET, COPY, CALL, DO, CREATE SCHEMA/FUNCTION, ...) is rejected.

    Postgres itself enforces tenant isolation via per-tenant roles; this
    class only decides what *kind* of statement may run.
    """

    # Statements that change the schema or discard whole tables.
    SCHEMA_CHANGE_OPERATIONS = {
        SqlOperationType.ALTER,
        SqlOperationType.DROP,
        SqlOperationType.TRUNCATE,
    }

    WRITE_OPERATIONS = {
        SqlOperationType.INSERT,
        SqlOperationType.UPDATE,
        SqlOperationType.DELETE,
        SqlOperationType.CREATE,
    } | SCHEMA_CHANGE_OPERATIONS

    # The only object kinds CREATE may build. Modifiers that can sit between
    # CREATE and the object kind are skipped when checking.
    CREATABLE_OBJECTS = {"TABLE", "INDEX", "VIEW"}
    CREATE_MODIFIERS = {"UNIQUE", "TEMP", "TEMPORARY", "UNLOGGED", "OR", "REPLACE", "MATERIALIZED"}

    DROPPABLE_OBJECTS = {"TABLE", "VIEW", "INDEX", "MATERIALIZED VIEW"}

    # ALTER TABLE clauses that would move a table out of the tenant's
    # ownership or storage. Postgres would refuse most of them for the
    # tenant role anyway; reject them up front with a clear message.
    BLOCKED_ALTER_KEYWORDS = {"OWNER", "SCHEMA", "TABLESPACE"}

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
        allow_schema_changes: bool = False,
    ) -> tuple[SqlOperationType, list[str]]:
        """
        Validate SQL and extract table names.

        Returns:
            Tuple of (operation_type, table_names)

        Raises:
            SqlValidationError: If SQL is invalid or unsafe
        """
        parsed = self.parse(sql, allow_write=allow_write, allow_schema_changes=allow_schema_changes)
        return parsed.operation, parsed.tables

    def parse(
        self,
        sql: str,
        allow_write: bool = False,
        allow_schema_changes: bool = False,
    ) -> ParsedStatement:
        """Validate SQL; see validate_and_parse. Also returns the write targets."""
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
        if operation in self.SCHEMA_CHANGE_OPERATIONS and not allow_schema_changes:
            raise SqlValidationError(
                f"{operation.value.upper()} statements are only allowed for the database owner"
            )

        return ParsedStatement(
            operation=operation,
            tables=self._extract_table_names(flat),
            write_targets=self._extract_write_targets(flat),
        )

    @classmethod
    def _classify(cls, statement, flat) -> SqlOperationType:
        top = statement.get_type().upper()

        # sqlparse reports "CREATE OR REPLACE" as a single type/token.
        if top.startswith("CREATE"):
            top = "CREATE"

        if top not in ("SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP", "TRUNCATE"):
            raise SqlValidationError(
                f"Statement type '{top}' is not allowed; only SELECT, INSERT, UPDATE, DELETE, "
                "CREATE, ALTER TABLE, DROP and TRUNCATE are permitted"
            )

        if top == "CREATE":
            cls._check_create_target(flat)
            return SqlOperationType.CREATE
        if top == "ALTER":
            cls._check_alter(flat)
            return SqlOperationType.ALTER
        if top == "DROP":
            cls._check_drop(flat)
            return SqlOperationType.DROP
        if top == "TRUNCATE":
            cls._check_no_other_ddl(flat)
            return SqlOperationType.TRUNCATE

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
    def _check_alter(cls, flat) -> None:
        """Only ALTER TABLE, and none of the clauses that change ownership or location."""
        words = [w for t in flat for w in t.value.upper().split()]
        if len(words) < 2 or words[1] != "TABLE":
            raise SqlValidationError("Only ALTER TABLE is allowed")
        for token in flat[1:]:
            value = token.value.upper()
            if token.ttype in T.Keyword and value in cls.BLOCKED_ALTER_KEYWORDS:
                raise SqlValidationError(f"ALTER TABLE ... {value} is not allowed")
            # DROP/ALTER COLUMN and DROP CONSTRAINT are ordinary ALTER TABLE clauses.
            if token.ttype in T.Keyword.DDL and value not in ("DROP", "ALTER"):
                raise SqlValidationError(f"DDL keyword '{value}' is not allowed here")

    @classmethod
    def _check_drop(cls, flat) -> None:
        """DROP TABLE / VIEW / MATERIALIZED VIEW / INDEX only."""
        target = flat[1].value.upper() if len(flat) > 1 else ""
        if target == "MATERIALIZED" and len(flat) > 2:
            target = f"MATERIALIZED {flat[2].value.upper()}"
        if target not in cls.DROPPABLE_OBJECTS:
            raise SqlValidationError(
                f"DROP {target or '?'} is not allowed; only DROP TABLE, VIEW and INDEX are permitted"
            )
        cls._check_no_other_ddl(flat)

    @staticmethod
    def _check_no_other_ddl(flat) -> None:
        for token in flat[1:]:
            if token.ttype in T.Keyword.DDL:
                raise SqlValidationError(f"DDL keyword '{token.value.upper()}' is not allowed here")

    @classmethod
    def _check_blocked_functions(cls, flat) -> None:
        for i, token in enumerate(flat[:-1]):
            if token.ttype in (T.Name, T.Keyword, T.Name.Builtin) and flat[i + 1].match(T.Punctuation, "("):
                if token.value.lower() in cls.BLOCKED_FUNCTIONS:
                    raise SqlValidationError(f"Function '{token.value}' is not allowed")

    @classmethod
    def _extract_write_targets(cls, flat) -> list[str]:
        """
        Relations a statement modifies: INSERT INTO x, UPDATE x, DELETE FROM x
        (also inside CTEs), TRUNCATE x, y, ALTER TABLE x, DROP ... x, y.
        Used to decide what to snapshot for undo, never for authorization.
        """
        targets: list[str] = []

        def add(name: str | None) -> None:
            if name and name not in targets:
                targets.append(name)

        words = [t.value.upper() for t in flat]
        lead = words[0] if words else ""

        if lead in ("TRUNCATE", "DROP", "ALTER"):
            i = 1
            skip = {"TABLE", "VIEW", "INDEX", "MATERIALIZED", "IF EXISTS", "IF", "EXISTS", "ONLY", "CONCURRENTLY"}
            while i < len(flat) and words[i] in skip:
                i += 1
            while i < len(flat):
                name, i = cls._read_name(flat, i)
                add(name)
                if lead == "ALTER" or i >= len(flat) or not flat[i].match(T.Punctuation, ","):
                    break
                i += 1
            return targets

        for i, token in enumerate(flat[:-1]):
            value = words[i]
            if token.ttype in T.Keyword.DML and value == "UPDATE":
                add(cls._read_name(flat, i + 1)[0])
            elif token.ttype in T.Keyword and value in ("INTO", "FROM") and i > 0 and words[i - 1] in (
                "INSERT", "DELETE"
            ):
                add(cls._read_name(flat, i + 1)[0])
        return targets

    @staticmethod
    def _read_name(flat, i: int) -> tuple[str | None, int]:
        """
        Read a possibly schema-qualified identifier starting at flat[i].
        Returns (unqualified name as Postgres stores it, index after it).
        """
        parts: list[str] = []
        if i < len(flat) and flat[i].value.upper() == "ONLY":
            i += 1
        while i < len(flat):
            token = flat[i]
            # In a name position, sqlparse's keywords ("archive", "data",
            # "name") are just table names.
            if token.ttype in (T.Name, T.Literal.String.Symbol) or (
                token.ttype in T.Keyword and token.ttype not in (T.Keyword.DML, T.Keyword.DDL)
                and token.value.upper() not in ("SET", "WHERE", "VALUES", "SELECT", "DEFAULT", "AS", "USING")
            ):
                value = token.value
                parts.append(value[1:-1].replace('""', '"') if value.startswith('"') else value.lower())
                i += 1
                if i < len(flat) and flat[i].match(T.Punctuation, "."):
                    i += 1
                    continue
            break
        return (parts[-1] if parts else None), i

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
