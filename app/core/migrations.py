"""
Versioned data migrations, applied in order at API startup.

Each migration runs once; applied ids are recorded in Mongo's
`schema_migrations`. A lock document keeps two API replicas from running
them concurrently. Index creation stays in the services' idempotent
`initialize()` methods; migrations are for changes that are not idempotent
or that must remove something (old indexes, reshaped documents).

To add one: write `async def _name(mongo, database_manager)` and append
(id, function) to MIGRATIONS. Never edit or reorder applied migrations.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from app.core.database import DatabaseManager

logger = logging.getLogger(__name__)

LOCK_ID = "__lock__"
LOCK_TIMEOUT = timedelta(minutes=10)
LOCK_WAIT_SECONDS = 120

Migration = Callable[[AsyncIOMotorDatabase, DatabaseManager], Awaitable[None]]


async def _conversations_many_per_database(mongo: AsyncIOMotorDatabase, _: DatabaseManager) -> None:
    """
    Conversations used to be one per (user, database), enforced by a unique
    index, and expired after 7 idle days. Now there are many, kept until
    deleted, each with a title.
    """
    conversations = mongo["conversations"]
    for name, spec in (await conversations.index_information()).items():
        if name != "_id_" and (spec.get("unique") or "expireAfterSeconds" in spec):
            await conversations.drop_index(name)

    async for doc in conversations.find({"title": {"$exists": False}}, {"messages": {"$slice": 1}}):
        first = next((m["content"] for m in doc.get("messages", []) if m.get("role") == "human"), "")
        title = " ".join(first.split())[:80] or "Untitled"
        await conversations.update_one({"_id": doc["_id"]}, {"$set": {"title": title}})


async def _users_email_verified(mongo: AsyncIOMotorDatabase, _: DatabaseManager) -> None:
    """Accounts created before verification existed have not proven their address."""
    await mongo["user"].update_many({"email_verified": {"$exists": False}}, {"$set": {"email_verified": False}})


MIGRATIONS: list[tuple[str, Migration]] = [
    ("20261007_conversations_many_per_database", _conversations_many_per_database),
    ("20261007_users_email_verified", _users_email_verified),
]


async def run_migrations(database_manager: DatabaseManager) -> list[str]:
    """Apply pending migrations; returns the ids applied by this call."""
    mongo = database_manager.mongo_database
    records = mongo["schema_migrations"]

    await _acquire_lock(records)
    try:
        applied = {doc["_id"] async for doc in records.find({"_id": {"$ne": LOCK_ID}}, {"_id": 1})}
        ran = []
        for migration_id, migrate in MIGRATIONS:
            if migration_id in applied:
                continue
            logger.info("Applying migration %s", migration_id)
            await migrate(mongo, database_manager)
            await records.insert_one({"_id": migration_id, "applied_at": datetime.now(timezone.utc)})
            ran.append(migration_id)
        return ran
    finally:
        await records.delete_one({"_id": LOCK_ID})


async def _acquire_lock(records) -> None:
    deadline = asyncio.get_running_loop().time() + LOCK_WAIT_SECONDS
    while True:
        now = datetime.now(timezone.utc)
        try:
            await records.insert_one({"_id": LOCK_ID, "expires_at": now + LOCK_TIMEOUT})
            return
        except DuplicateKeyError:
            # A replica that died mid-migration leaves a stale lock behind.
            await records.delete_one({"_id": LOCK_ID, "expires_at": {"$lt": now}})
        if asyncio.get_running_loop().time() > deadline:
            raise RuntimeError("Timed out waiting for another process to finish migrations")
        await asyncio.sleep(1)
