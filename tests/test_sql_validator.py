import pytest

from app.services.sql_validator import SQLValidator, SqlValidationError, SqlOperationType


@pytest.fixture
def validator() -> SQLValidator:
    return SQLValidator()


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM users WHERE revoked_at IS NULL",
        "SELECT u.id, COUNT(*) FROM users u JOIN orders o ON o.user_id = u.id GROUP BY u.id",
        "WITH t AS (SELECT 1 AS x) SELECT * FROM t;",
        "SELECT * FROM t WHERE name = 'dropbox'",
        "SELECT * FROM t WHERE granted",
        "-- leading comment\nSELECT 1",
    ],
)
def test_read_only_selects_are_allowed(validator, sql):
    operation, _ = validator.validate_and_parse(sql, allow_write=False)
    assert operation is SqlOperationType.SELECT


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO t VALUES (1)",
        "-- note\nINSERT INTO t VALUES (1)",
        "/* c */ DELETE FROM t",
        "UPDATE t SET a = 1",
        "WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d",
        "CREATE TABLE t (id int)",
    ],
)
def test_writes_rejected_for_read_access(validator, sql):
    with pytest.raises(SqlValidationError) as exc:
        validator.validate_and_parse(sql, allow_write=False)
    assert "Write operations" in exc.value.detail


@pytest.mark.parametrize(
    "sql, expected",
    [
        ("INSERT INTO t VALUES (1)", SqlOperationType.INSERT),
        ("UPDATE t SET a = 1", SqlOperationType.UPDATE),
        ("DELETE FROM t", SqlOperationType.DELETE),
        ("WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d", SqlOperationType.DELETE),
        ("CREATE TABLE t (id serial PRIMARY KEY, name text)", SqlOperationType.CREATE),
        ("CREATE TABLE IF NOT EXISTS t (id int)", SqlOperationType.CREATE),
        ("CREATE UNIQUE INDEX t_name_idx ON t (name)", SqlOperationType.CREATE),
        ("CREATE OR REPLACE VIEW v AS SELECT * FROM t", SqlOperationType.CREATE),
    ],
)
def test_writes_allowed_for_write_access(validator, sql, expected):
    operation, _ = validator.validate_and_parse(sql, allow_write=True)
    assert operation is expected


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1; DROP SCHEMA public CASCADE",
        "CREATE SCHEMA other",
        "CREATE ROLE hacker",
        "CREATE EXTENSION dblink",
        "CREATE FUNCTION f() RETURNS int AS $$ SELECT 1 $$ LANGUAGE sql",
        "CREATE TABLE t (id int); DROP TABLE t",
        "DROP TABLE t",
        "ALTER TABLE t ADD COLUMN x int",
        "TRUNCATE t",
        "GRANT ALL ON t TO public",
        "COPY t TO '/tmp/x'",
        "CALL some_proc()",
        "DO $$ BEGIN END $$",
        "SET ROLE postgres",
        "EXPLAIN ANALYZE SELECT 1",
        "SELECT pg_sleep(30)",
        "SELECT set_config('role', 'postgres', false)",
        "",
        "   ;  ",
    ],
)
def test_everything_else_rejected_even_with_write_access(validator, sql):
    with pytest.raises(SqlValidationError):
        validator.validate_and_parse(sql, allow_write=True)


def test_table_names_extracted(validator):
    _, tables = validator.validate_and_parse(
        "SELECT * FROM customers c JOIN orders o ON o.customer_id = c.id"
    )
    assert tables == ["customers", "orders"]


@pytest.mark.parametrize(
    "sql, expected",
    [
        ("ALTER TABLE t ADD COLUMN x int", SqlOperationType.ALTER),
        ("ALTER TABLE IF EXISTS t DROP COLUMN x", SqlOperationType.ALTER),
        ("ALTER TABLE t RENAME TO u", SqlOperationType.ALTER),
        ("DROP TABLE IF EXISTS a, b CASCADE", SqlOperationType.DROP),
        ("DROP VIEW v", SqlOperationType.DROP),
        ("DROP MATERIALIZED VIEW m", SqlOperationType.DROP),
        ("DROP INDEX i", SqlOperationType.DROP),
        ("TRUNCATE TABLE a, b RESTART IDENTITY", SqlOperationType.TRUNCATE),
    ],
)
def test_schema_changes_need_owner_access(validator, sql, expected):
    with pytest.raises(SqlValidationError, match="only allowed for the database owner"):
        validator.validate_and_parse(sql, allow_write=True)
    operation, _ = validator.validate_and_parse(sql, allow_write=True, allow_schema_changes=True)
    assert operation is expected


@pytest.mark.parametrize(
    "sql",
    [
        "ALTER TABLE t OWNER TO postgres",
        "ALTER TABLE t SET SCHEMA public",
        "ALTER TABLE t SET TABLESPACE pg_default",
        "ALTER ROLE r SUPERUSER",
        "ALTER SCHEMA s RENAME TO x",
        "DROP SCHEMA s",
        "DROP ROLE r",
        "DROP FUNCTION f",
        "DROP EXTENSION vector",
        "ALTER TABLE t ADD COLUMN x int; DROP TABLE t",
    ],
)
def test_dangerous_ddl_rejected_even_for_owners(validator, sql):
    with pytest.raises(SqlValidationError):
        validator.validate_and_parse(sql, allow_write=True, allow_schema_changes=True)


@pytest.mark.parametrize(
    "sql, targets",
    [
        ("INSERT INTO s.t (a) VALUES (1)", ["t"]),
        ('UPDATE "Foo" SET a = 1', ["Foo"]),
        ("UPDATE Orders o SET x = 1 FROM items WHERE o.id = items.id", ["orders"]),
        ("DELETE FROM t WHERE a = 1", ["t"]),
        ("WITH d AS (DELETE FROM t RETURNING *) INSERT INTO archive SELECT * FROM d", ["t", "archive"]),
        ("INSERT INTO t SELECT * FROM s", ["t"]),
        ("TRUNCATE a, b", ["a", "b"]),
        ('DROP TABLE IF EXISTS a, "B"', ["a", "B"]),
        ("ALTER TABLE ONLY t ADD COLUMN x int", ["t"]),
        ("CREATE TABLE n (id int)", []),
        ("SELECT * FROM t", []),
    ],
)
def test_write_targets(validator, sql, targets):
    parsed = validator.parse(sql, allow_write=True, allow_schema_changes=True)
    assert parsed.write_targets == targets
