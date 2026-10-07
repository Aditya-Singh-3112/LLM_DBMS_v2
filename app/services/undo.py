"""
Single-level undo for confirmed writes.

Before a write runs, in the same transaction, we snapshot what it can
destroy, and afterwards compare the schema's relations with what was there
before. The result is an UndoPlan (plain JSON, stored in Mongo) that
reverses the write:

  INSERT/UPDATE/DELETE/TRUNCATE  copy of each target table's rows; undo
                                 truncates the table and copies them back
  ALTER/DROP TABLE               structural copy (columns, constraints,
                                 indexes, identity, generated columns) plus
                                 rows; undo drops the current table and
                                 renames the copy into place, recreating
                                 defaults and serial sequences
  CREATE ...                     undo drops what was created
  views and indexes              definitions are captured before the write;
                                 undo recreates any that went missing

Only the latest write per database can be undone. Every confirmed write
(and every import) discards the previous snapshots, so an undo never
overwrites a later change. Undo is unavailable when a target table is too
large to copy cheaply, or when other tables reference it by foreign key
(restoring it would not restore their rows).

All functions take a connection that is already inside a transaction with
the tenant role and search_path set.
"""
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from asyncpg import Connection
from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.services.sql_validator import ParsedStatement, SqlOperationType

logger = logging.getLogger(__name__)

SNAPSHOT_PREFIX = "_undo_"
# For LIKE patterns: '_' is a wildcard, so escape it.
SNAPSHOT_LIKE = r"\_undo\_%"

_DATA_OPERATIONS = {
    SqlOperationType.INSERT,
    SqlOperationType.UPDATE,
    SqlOperationType.DELETE,
    SqlOperationType.TRUNCATE,
}
_STRUCTURAL_OPERATIONS = {SqlOperationType.ALTER, SqlOperationType.DROP}
_KIND_WORDS = {"r": "TABLE", "v": "VIEW", "m": "MATERIALIZED VIEW", "i": "INDEX"}


@dataclass
class Relation:
    oid: int
    name: str
    kind: str  # r, v, m, i
    definition: str | None  # view query or CREATE INDEX statement
    table_oid: int | None  # indexes only


@dataclass
class UndoPlan:
    drop: list[dict] = field(default_factory=list)          # {name, kind}
    rename: list[dict] = field(default_factory=list)        # {kind, current_oid, original}
    structural: list[dict] = field(default_factory=list)    # {oid, original, snapshot, defaults, serials}
    data: list[dict] = field(default_factory=list)          # {table, snapshot, columns}
    views: list[dict] = field(default_factory=list)         # {name, kind, definition}
    indexes: list[dict] = field(default_factory=list)       # {name, table, definition}

    @property
    def snapshots(self) -> list[str]:
        return [s["snapshot"] for s in self.structural] + [d["snapshot"] for d in self.data]


@dataclass
class Assessment:
    """What a write would need snapshotted, or why it can't be undone."""
    before: dict[int, Relation]
    tables: list[Relation]
    structural: bool
    unavailable_reason: str | None = None


async def list_relations(conn: Connection, schema: str) -> dict[int, Relation]:
    rows = await conn.fetch(
        """
        SELECT c.oid, c.relname, c.relkind::text AS relkind,
               CASE WHEN c.relkind IN ('v', 'm') THEN pg_get_viewdef(c.oid)
                    WHEN c.relkind = 'i' THEN pg_get_indexdef(c.oid) END AS definition,
               i.indrelid AS table_oid
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_index i ON i.indexrelid = c.oid
        WHERE n.nspname = $1
          AND c.relkind IN ('r', 'v', 'm', 'i')
          AND c.relname NOT LIKE $2
        """,
        schema,
        SNAPSHOT_LIKE,
    )
    return {
        r["oid"]: Relation(r["oid"], r["relname"], r["relkind"], r["definition"], r["table_oid"])
        for r in rows
    }


async def assess(conn: Connection, schema: str, parsed: ParsedStatement, max_bytes: int) -> Assessment:
    before = await list_relations(conn, schema)
    by_name = {r.name: r for r in before.values()}
    operation = parsed.operation
    structural = operation in _STRUCTURAL_OPERATIONS
    tables = [
        by_name[name] for name in parsed.write_targets
        if name in by_name and by_name[name].kind == "r"
    ]
    assessment = Assessment(before=before, tables=tables, structural=structural)

    if max_bytes <= 0:
        assessment.unavailable_reason = "undo is disabled"
        return assessment
    if operation in _DATA_OPERATIONS and not tables:
        assessment.unavailable_reason = "the changed table could not be determined"
        return assessment
    if not tables:
        return assessment

    oids = [t.oid for t in tables]
    referenced = await conn.fetchval(
        """
        SELECT string_agg(DISTINCT confrelid::regclass::text, ', ')
        FROM pg_constraint
        WHERE contype = 'f' AND confrelid = ANY($1::oid[]) AND conrelid <> confrelid
        """,
        oids,
    )
    if referenced:
        assessment.unavailable_reason = f"other tables reference {referenced} by foreign key"
        return assessment

    size = await conn.fetchval(
        "SELECT COALESCE(sum(pg_total_relation_size(oid)), 0) FROM unnest($1::oid[]) AS oid", oids
    )
    if size > max_bytes:
        assessment.unavailable_reason = (
            f"the affected tables are larger than the {max_bytes // (1024 * 1024)} MB undo limit"
        )
    return assessment


async def discard_snapshots(conn: Connection, schema: str) -> None:
    names = await conn.fetch(
        """
        SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = $1 AND c.relkind = 'r' AND c.relname LIKE $2
        """,
        schema,
        SNAPSHOT_LIKE,
    )
    for row in names:
        await conn.execute(f'DROP TABLE IF EXISTS {_q(row["relname"])} CASCADE')


async def take_snapshots(conn: Connection, assessment: Assessment, undo_id: str) -> UndoPlan:
    plan = UndoPlan()
    for n, table in enumerate(assessment.tables):
        snapshot = f"{SNAPSHOT_PREFIX}{undo_id}_{n}"
        columns = await _insertable_columns(conn, table.oid)
        col_list = ", ".join(_q(c) for c in columns)
        if assessment.structural:
            defaults, serials = await _column_defaults(conn, table.oid)
            await conn.execute(
                f"CREATE TABLE {_q(snapshot)} (LIKE {_q(table.name)} INCLUDING ALL EXCLUDING DEFAULTS)"
            )
            await conn.execute(
                f"INSERT INTO {_q(snapshot)} ({col_list}) OVERRIDING SYSTEM VALUE "
                f"SELECT {col_list} FROM {_q(table.name)}"
            )
            plan.structural.append({
                "oid": table.oid, "original": table.name, "snapshot": snapshot,
                "defaults": defaults, "serials": serials,
            })
        else:
            await conn.execute(f"CREATE TABLE {_q(snapshot)} AS TABLE {_q(table.name)}")
            plan.data.append({"table": table.name, "snapshot": snapshot, "columns": columns})
    return plan


async def row_change_counters(conn: Connection, schema: str) -> dict[str, int]:
    """
    Rows inserted + updated + deleted per table, from Postgres's
    per-backend transaction statistics. The absolute values can include
    unflushed counts from earlier transactions on this pooled connection,
    but stats are never flushed mid-transaction, so the difference between
    two readings in one transaction is exactly what happened in between.
    """
    rows = await conn.fetch(
        """
        SELECT relname, n_tup_ins + n_tup_upd + n_tup_del AS changed
        FROM pg_stat_xact_user_tables
        WHERE schemaname = $1 AND relname NOT LIKE $2
        """,
        schema,
        SNAPSHOT_LIKE,
    )
    return {r["relname"]: r["changed"] for r in rows}


def unsnapshotted_changes(before: dict[str, int], after: dict[str, int], plan: UndoPlan) -> list[str]:
    """
    Tables whose rows changed between the two readings but that the plan
    did not snapshot. A safety net under the SQL parsing that chose what to
    snapshot: an undo must never restore some tables and silently leave
    others changed.
    """
    covered = {d["table"] for d in plan.data} | {s["original"] for s in plan.structural}
    created = {d["name"] for d in plan.drop if d["kind"] == "r"}
    return sorted(
        name for name, count in after.items()
        if count > before.get(name, 0) and name not in covered and name not in created
    )


async def complete_plan(conn: Connection, schema: str, assessment: Assessment, plan: UndoPlan) -> UndoPlan:
    """Fill in the parts of the plan that depend on what the write did."""
    before, after = assessment.before, await list_relations(conn, schema)
    restored = {s["oid"] for s in plan.structural}

    for oid, rel in after.items():
        old = before.get(oid)
        if rel.kind == "i" and rel.table_oid in restored:
            continue  # the restored copy brings the table's own indexes back
        if old is None:
            # Indexes on new tables go away with the table.
            if rel.kind == "i" and rel.table_oid not in before:
                continue
            plan.drop.append({"name": rel.name, "kind": rel.kind})
        elif rel.kind in ("v", "m") and rel.definition != old.definition:
            # CREATE OR REPLACE VIEW: drop it; the views step recreates the old one.
            plan.drop.append({"name": rel.name, "kind": rel.kind})
        elif rel.name != old.name and oid not in restored:
            plan.rename.append({"kind": rel.kind, "current_oid": oid, "original": old.name})

    for rel in before.values():
        if rel.kind in ("v", "m"):
            plan.views.append({"name": rel.name, "kind": rel.kind, "definition": rel.definition})
        elif rel.kind == "i" and rel.table_oid not in restored and rel.table_oid in before:
            plan.indexes.append({
                "name": rel.name, "table": before[rel.table_oid].name, "definition": rel.definition,
            })
    # Drop views before the tables they might depend on.
    plan.drop.sort(key=lambda d: {"v": 0, "m": 0, "i": 1, "r": 2}[d["kind"]])
    return plan


async def apply(conn: Connection, schema: str, plan: UndoPlan) -> None:
    """Reverse the write. Raises HTTPException(409) if the snapshots are gone."""
    present = {r["relname"] for r in await conn.fetch(
        """
        SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = $1 AND c.relname LIKE $2
        """,
        schema,
        SNAPSHOT_LIKE,
    )}
    if any(s not in present for s in plan.snapshots):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This change can no longer be undone: a later change replaced its snapshot",
        )

    for item in plan.drop:
        await conn.execute(f"DROP {_KIND_WORDS[item['kind']]} IF EXISTS {_q(item['name'])} CASCADE")

    for item in plan.rename:
        current = await _name_of(conn, item["current_oid"])
        if current:
            await conn.execute(
                f"ALTER {_KIND_WORDS[item['kind']]} {_q(current)} RENAME TO {_q(item['original'])}"
            )

    for item in plan.structural:
        current = await _name_of(conn, item["oid"])
        if current:
            await conn.execute(f"DROP TABLE {_q(current)} CASCADE")
        table = _q(item["original"])
        await conn.execute(f"ALTER TABLE {_q(item['snapshot'])} RENAME TO {table}")
        for column, expression in item["defaults"].items():
            # Expressions come from pg_get_expr on the original table.
            await conn.execute(f"ALTER TABLE {table} ALTER COLUMN {_q(column)} SET DEFAULT {expression}")
        for column in item["serials"]:
            sequence = _q(f"{item['original']}_{column}_seq"[:63])
            await conn.execute(f"CREATE SEQUENCE IF NOT EXISTS {sequence} OWNED BY {table}.{_q(column)}")
            await conn.execute(
                f"SELECT setval($1::regclass, COALESCE((SELECT max({_q(column)}) FROM {table}), 0) + 1, false)",
                sequence,
            )
            literal = sequence.replace("'", "''")
            await conn.execute(
                f"ALTER TABLE {table} ALTER COLUMN {_q(column)} SET DEFAULT nextval('{literal}')"
            )

    for item in plan.data:
        table, cols = _q(item["table"]), ", ".join(_q(c) for c in item["columns"])
        await conn.execute(f"TRUNCATE {table}")
        await conn.execute(
            f"INSERT INTO {table} ({cols}) OVERRIDING SYSTEM VALUE SELECT {cols} FROM {_q(item['snapshot'])}"
        )
        await conn.execute(f"DROP TABLE {_q(item['snapshot'])}")

    await _recreate_missing(conn, schema, plan)


async def _recreate_missing(conn: Connection, schema: str, plan: UndoPlan) -> None:
    """Views (which may depend on each other) and indexes that no longer exist."""
    async def existing() -> set[str]:
        return {r.name for r in (await list_relations(conn, schema)).values()}

    pending = list(plan.views)
    while pending:
        names, failed = await existing(), []
        for view in pending:
            if view["name"] in names:
                continue
            word = "MATERIALIZED VIEW" if view["kind"] == "m" else "VIEW"
            try:
                async with conn.transaction():
                    await conn.execute(f"CREATE {word} {_q(view['name'])} AS {view['definition']}")
            except Exception:
                failed.append(view)
        if len(failed) == len(pending):
            logger.warning("Undo could not recreate views: %s", [v["name"] for v in failed])
            break
        pending = failed

    names = await existing()
    for index in plan.indexes:
        if index["name"] not in names and index["table"] in names:
            try:
                async with conn.transaction():
                    await conn.execute(index["definition"])
            except Exception:
                logger.warning("Undo could not recreate index %s", index["name"])


async def _insertable_columns(conn: Connection, table_oid: int) -> list[str]:
    rows = await conn.fetch(
        """
        SELECT attname FROM pg_attribute
        WHERE attrelid = $1 AND attnum > 0 AND NOT attisdropped AND attgenerated = ''
        ORDER BY attnum
        """,
        table_oid,
    )
    return [r["attname"] for r in rows]


async def _column_defaults(conn: Connection, table_oid: int) -> tuple[dict[str, str], list[str]]:
    """Plain defaults by column, and the columns whose default is a serial sequence."""
    rows = await conn.fetch(
        """
        SELECT a.attname, pg_get_expr(d.adbin, d.adrelid) AS expr,
               pg_get_serial_sequence(quote_ident(c.relname), a.attname) IS NOT NULL AS is_serial
        FROM pg_attrdef d
        JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum
        JOIN pg_class c ON c.oid = d.adrelid
        WHERE d.adrelid = $1 AND a.attgenerated = '' AND a.attidentity = ''
        """,
        table_oid,
    )
    defaults = {r["attname"]: r["expr"] for r in rows if not r["is_serial"]}
    serials = [r["attname"] for r in rows if r["is_serial"]]
    return defaults, serials


async def _name_of(conn: Connection, oid: int) -> str | None:
    return await conn.fetchval("SELECT relname FROM pg_class WHERE oid = $1", oid)


def _q(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


class UndoStore:
    """The latest confirmed write per database and how to reverse it."""

    def __init__(self, mongo_database: AsyncIOMotorDatabase) -> None:
        self.records = mongo_database["undo_log"]

    async def save(
        self,
        database_id: str,
        user_id: str,
        sql: str,
        plan: UndoPlan | None,
        unavailable_reason: str | None,
    ) -> None:
        await self.records.replace_one(
            {"_id": database_id},
            {
                "_id": database_id,
                "user_id": user_id,
                "sql": sql,
                "plan": asdict(plan) if plan else None,
                "unavailable_reason": unavailable_reason,
                "created_at": datetime.now(timezone.utc),
            },
            upsert=True,
        )

    async def get(self, database_id: str) -> dict | None:
        return await self.records.find_one({"_id": database_id})

    async def clear(self, database_id: str) -> None:
        await self.records.delete_one({"_id": database_id})

    @staticmethod
    def plan_of(record: dict) -> UndoPlan | None:
        return UndoPlan(**record["plan"]) if record.get("plan") else None
