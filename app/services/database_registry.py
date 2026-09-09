from datetime import datetime, timezone

from bson import ObjectId
from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.database import DatabaseManager
from app.models.contracts import (
    AccessLevel,
    DatabaseCreateRequest,
    DatabaseResponse,
)


class DatabaseRegistryService:
    def __init__(
        self,
        mongo_database: AsyncIOMotorDatabase,
        database_manager: DatabaseManager,
    ) -> None:
        self.mongo_database = mongo_database
        self.database_manager = database_manager
        self.databases = mongo_database["databases"]
        self.permissions = mongo_database["permissions"]

    async def initialize(self) -> None:
        await self.databases.create_index(
            [("owner_id", 1), ("created_at", -1)]
        )
        await self.permissions.create_index(
            [("database_id", 1), ("user_id", 1)],
            unique=True,
        )

    async def create(
        self,
        request: DatabaseCreateRequest,
        owner_id: str,
    ) -> DatabaseResponse:
        now = datetime.now(timezone.utc)

        document = {
            "name": request.name,
            "owner_id": owner_id,
            "created_at": now,
        }

        result = await self.databases.insert_one(document)
        database_id = str(result.inserted_id)
        schema_name = self._schema_name(database_id)

        try:
            await self.database_manager.create_tenant_schema(schema_name)
        except Exception:
            await self.databases.delete_one({"_id": result.inserted_id})
            raise

        return DatabaseResponse(
            id=database_id,
            name=request.name,
            owner_id=owner_id,
            access_level=AccessLevel.OWNER,
            created_at=now,
        )

    async def list_for_user(self, user_id: str) -> list[DatabaseResponse]:
        owned = self.databases.find({"owner_id": user_id})
        shared = self.permissions.find({"user_id": user_id})

        databases: dict[str, DatabaseResponse] = {}

        async for document in owned:
            database_id = str(document["_id"])
            databases[database_id] = DatabaseResponse(
                id=database_id,
                name=document["name"],
                owner_id=document["owner_id"],
                access_level=AccessLevel.OWNER,
                created_at=document["created_at"],
            )

        async for permission in shared:
            database = await self.databases.find_one(
                {"_id": ObjectId(permission["database_id"])}
            )

            if database is None:
                continue

            database_id = str(database["_id"])
            databases[database_id] = DatabaseResponse(
                id=database_id,
                name=database["name"],
                owner_id=database["owner_id"],
                access_level=AccessLevel(permission["access_level"]),
                created_at=database["created_at"],
            )

        return list(databases.values())

    async def delete(self, database_id: str, owner_id: str) -> None:
        database = await self._get_database(database_id)

        if database["owner_id"] != owner_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the owner can delete this database",
            )

        await self.database_manager.delete_tenant_schema(
            self._schema_name(database_id)
        )

        await self.permissions.delete_many(
            {"database_id": database_id}
        )
        await self.databases.delete_one(
            {"_id": ObjectId(database_id)}
        )

    async def get_access_level(
        self,
        database_id: str,
        user_id: str,
    ) -> AccessLevel:
        database = await self._get_database(database_id)

        if database["owner_id"] == user_id:
            return AccessLevel.OWNER

        permission = await self.permissions.find_one(
            {
                "database_id": database_id,
                "user_id": user_id,
            }
        )

        if permission is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have access to this database",
            )

        return AccessLevel(permission["access_level"])

    async def share(
        self,
        database_id: str,
        owner_id: str,
        user_id: str,
        access_level: AccessLevel,
    ) -> None:
        database = await self._get_database(database_id)

        if database["owner_id"] != owner_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the owner can share this database",
            )

        if user_id == owner_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The owner already has full access",
            )

        await self.permissions.update_one(
            {
                "database_id": database_id,
                "user_id": user_id,
            },
            {
                "$set": {
                    "access_level": access_level.value,
                    "updated_at": datetime.now(timezone.utc),
                },
                "$setOnInsert": {
                    "database_id": database_id,
                    "user_id": user_id,
                    "created_at": datetime.now(timezone.utc),
                },
            },
            upsert=True,
        )

    async def _get_database(self, database_id: str) -> dict:
        if not ObjectId.is_valid(database_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Database not found",
            )

        database = await self.databases.find_one(
            {"_id": ObjectId(database_id)}
        )

        if database is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Database not found",
            )

        return database

    @staticmethod
    def _schema_name(database_id: str) -> str:
        if not ObjectId.is_valid(database_id):
            raise ValueError("Invalid database identifier")

        return f"tenant_{database_id.lower()}"