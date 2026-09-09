from contextlib import asynccontextmanager
from typing import AsyncIterator
import re
from typing import Final
import asyncpg
from asyncpg.pool import PoolConnectionProxy
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from redis.asyncio import Redis

from app.core.config import Settings

class DatabaseManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.mongo_client: AsyncIOMotorClient | None = None
        self.mongo_database: AsyncIOMotorDatabase | None = None
        self.redis: Redis | None = None
        self.postgres_pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        self.mongo_client = AsyncIOMotorClient(self.settings.mongo_uri)
        self.mongo_database = self.mongo_client[self.settings.mongo_database]

        self.redis = Redis.from_url(
            self.settings.redis_uri,
            decode_response = True
        )

        self.postgres_pool = await asyncpg.create_pool(
            dsn = self.settings.postgres_uri,
            min_size = self.settings.postgres_min_pool_size,
            max_size = self.settings.postgres_max_pool_size
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
        if self.postgres_pool is None:
            raise RuntimeError("Postgres pool is not initialized")

        async with self.postgres_pool.acquire() as conneciton:
            await conneciton.execute(
                """
                CREATE EXTENSION IF NOT EXISTS vector;
                CREATE SCHEMA IF NOT EXISTS rag;

                CREATE TABLE IF NOT EXISTS rag.textbook_chunks (
                    id SERIAL PRIMARY KEY,
                    source TEXT NOT NULL,
                    chapter TEXT,
                    page_start INT,
                    page_end INT,
                    content TEXT NOT NULL,
                    embedding VECTOR(768),
                    created_at TIMESTAMPTZ DEFAULT now()
                );
                """
            )

    async def create_tenant_schema(self, schema_name: str) -> None:
        self._validate_schema_name(schema_name)

        async with self.postgres_connection() as connection:
            await connection.execute(
                f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"'
            )

    async def delete_tenant_schema(self, schema_name: str) -> None:
        self._validate_schema_name(schema_name)

        async with self.postgres_connection() as connection:
            await connection.execute(
                f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'
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

database_manager: DatabaseManager | None = None

def get_database_manager() -> DatabaseManager:
    if database_manager is None:
        raise RuntimeError("Database manager has not been initialized")

    return database_manager