import re
from enum import Enum

import sqlparse
from fastapi import HTTPException, status


class SqlOperationType(str, Enum):
    SELECT = "select"
    INSERT = "insert"
    UPDATE = "update"
    DELETE = "delete"
    CREATE = "create"
    DROP = "drop"
    ALTER = "alter"
    UNKNOWN = "unknown"


class SqlValidationError(HTTPException):
    def __init__(self, detail: str) -> None:
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail,
        )


class SQLValidator:
    DANGEROUS_KEYWORDS = {
        "DROP",
        "TRUNCATE",
        "GRANT",
        "REVOKE",
        "ALTER ROLE",
        "ALTER USER",
    }

    ALLOWED_READ_KEYWORDS = {"SELECT", "WITH"}
    ALLOWED_WRITE_KEYWORDS = {
        "INSERT",
        "UPDATE",
        "DELETE",
        "WITH",
    }

    def __init__(self) -> None:
        pass

    def validate_and_parse(
        self,
        sql: str,
        allow_write: bool = False,
    ) -> tuple[SqlOperationType, list[str]]:
        """
        Validate SQL and extract table names.
        
        Args:
            sql: The SQL query string
            allow_write: Whether to allow INSERT/UPDATE/DELETE
            
        Returns:
            Tuple of (operation_type, table_names)
            
        Raises:
            SqlValidationError: If SQL is invalid or unsafe
        """
        sql = sql.strip()
        if not sql:
            raise SqlValidationError("SQL query cannot be empty")

        parsed = sqlparse.parse(sql)
        if not parsed:
            raise SqlValidationError("Failed to parse SQL")

        statement = parsed[0]

        self._check_dangerous_keywords(statement.tokens)

        operation = self._extract_operation_type(statement)

        if operation in (
            SqlOperationType.CREATE,
            SqlOperationType.DROP,
            SqlOperationType.ALTER,
        ):
            raise SqlValidationError("DDL operations are not allowed")

        if not allow_write and operation in (
            SqlOperationType.INSERT,
            SqlOperationType.UPDATE,
            SqlOperationType.DELETE,
        ):
            raise SqlValidationError(
                "Write operations are not allowed for this access level"
            )

        tables = self._extract_table_names(statement)

        return operation, tables

    @staticmethod
    def _check_dangerous_keywords(tokens) -> None:
        for token in tokens:
            if token.ttype is None and hasattr(token, "tokens"):
                SQLValidator._check_dangerous_keywords(token.tokens)

            upper_value = str(token).upper().strip()

            for keyword in SQLValidator.DANGEROUS_KEYWORDS:
                if keyword in upper_value:
                    raise SqlValidationError(
                        f"SQL contains forbidden keyword: {keyword}"
                    )

    @staticmethod
    def _extract_operation_type(statement) -> SqlOperationType:
        first_token = None

        for token in statement.tokens:
            if token.ttype is not None and token.is_whitespace:
                continue

            first_token = str(token).upper().strip()
            break

        if first_token is None:
            return SqlOperationType.UNKNOWN

        if first_token.startswith("SELECT"):
            return SqlOperationType.SELECT

        if first_token.startswith("WITH"):
            return SqlOperationType.SELECT

        if first_token.startswith("INSERT"):
            return SqlOperationType.INSERT

        if first_token.startswith("UPDATE"):
            return SqlOperationType.UPDATE

        if first_token.startswith("DELETE"):
            return SqlOperationType.DELETE

        if first_token.startswith("CREATE"):
            return SqlOperationType.CREATE

        if first_token.startswith("DROP"):
            return SqlOperationType.DROP

        if first_token.startswith("ALTER"):
            return SqlOperationType.ALTER

        return SqlOperationType.UNKNOWN

    @staticmethod
    def _extract_table_names(statement) -> list[str]:
        tables = set()

        tokens = list(statement.flatten())

        i = 0
        while i < len(tokens):
            token = tokens[i]

            upper_val = str(token).upper().strip()

            if upper_val in ("FROM", "INTO", "UPDATE", "JOIN", "INNER JOIN", "LEFT JOIN", "RIGHT JOIN", "FULL JOIN"):
                i += 1

                while i < len(tokens) and tokens[i].is_whitespace:
                    i += 1

                if i < len(tokens):
                    table_name = str(tokens[i]).strip()

                    if table_name and not table_name.upper() in (
                        "WHERE",
                        "GROUP",
                        "ORDER",
                        "LIMIT",
                        "OFFSET",
                        "AND",
                        "OR",
                    ):
                        tables.add(table_name.split()[0])

            i += 1

        return list(tables)