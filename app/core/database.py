import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator
import re
import asyncpg
from asyncpg.pool import PoolConnectionProxy
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from redis.asyncio import Redis

from app.core.config import Settings

logger = logging.getLogger(__name__)

class DatabaseManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.mongo_client: AsyncIOMotorClient | None = None
        self.mongo_database: AsyncIOMotorDatabase | None = None
        self.redis: Redis | None = None
        self.postgres_pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        # tz_aware so datetimes read back from Mongo compare cleanly with
        # datetime.now(timezone.utc) (e.g. refresh-token expiry checks).
        self.mongo_client = AsyncIOMotorClient(self.settings.mongo_uri, tz_aware=True)
        self.mongo_database = self.mongo_client[self.settings.mongo_database]

        self.redis = Redis.from_url(
            self.settings.redis_uri,
            decode_responses = True
        )

        # statement_cache_size=0: identical SQL text runs under different
        # tenant roles / search_paths on the same pooled connection, so a
        # cached plan from one tenant would be invalid (or wrong) for another.
        self.postgres_pool = await asyncpg.create_pool(
            dsn = self.settings.postgres_uri,
            min_size = self.settings.postgres_min_pool_size,
            max_size = self.settings.postgres_max_pool_size,
            statement_cache_size = 0,
        )

        await self.initialize_postgres()

    async def disconnect(self) -> None:
        if self.postgres_pool is not None:
            await self.postgres_pool.close()

        if self.redis is not None:
            await self.redis.aclose()

        if self.mongo_client is not None:
            self.mongo_client.close()

    async def initialize_postgres(self) -> None:
        # pgvector is needed by langchain_postgres (which manages its own
        # langchain_pg_* tables for the RAG collection).
        async with self.postgres_connection() as connection:
            await connection.execute("CREATE EXTENSION IF NOT EXISTS vector;")

            is_super = await connection.fetchval(
                "SELECT rolsuper FROM pg_roles WHERE rolname = current_user"
            )
            if is_super and self.settings.app_env != "development":
                logger.warning(
                    "Postgres login role is a SUPERUSER. Tenant isolation relies on "
                    "SET ROLE; run scripts/bootstrap_db.sql and connect as llm_dbms_app."
                )

    async def create_tenant_schema(self, schema_name: str) -> None:
        """
        Create a schema owned by a dedicated NOLOGIN role of the same name.

        User SQL later runs under `SET LOCAL ROLE <schema_name>`, so Postgres
        itself denies access to any other tenant's schema. The app's login
        role is granted membership so it can switch into the tenant role.
        """
        self._validate_schema_name(schema_name)

        async with self.postgres_connection() as connection:
            async with connection.transaction():
                app_role = await connection.fetchval("SELECT current_user")

                exists = await connection.fetchval(
                    "SELECT 1 FROM pg_roles WHERE rolname = $1", schema_name
                )
                if not exists:
                    await connection.execute(f'CREATE ROLE "{schema_name}" NOLOGIN')

                await connection.execute(
                    f'GRANT "{schema_name}" TO "{app_role}"'
                )
                await connection.execute(
                    f'CREATE SCHEMA IF NOT EXISTS "{schema_name}" AUTHORIZATION "{schema_name}"'
                )

    async def delete_tenant_schema(self, schema_name: str) -> None:
        self._validate_schema_name(schema_name)

        async with self.postgres_connection() as connection:
            async with connection.transaction():
                await connection.execute(
                    f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'
                )
                await connection.execute(
                    f'DROP ROLE IF EXISTS "{schema_name}"'
                )

    @staticmethod
    def _validate_schema_name(schema_name: str) -> None:
        if not re.fullmatch(r"tenant_[a-f0-9]{24}", schema_name):
            raise ValueError("Invalid tenant schema name")

    @asynccontextmanager
    async def postgres_connection(self) -> AsyncIterator[PoolConnectionProxy]:
        if self.postgres_pool is None:
            raise RuntimeError("Postgres pool is not initialized")

        async with self.postgres_pool.acquire() as connection:
            yield connection
