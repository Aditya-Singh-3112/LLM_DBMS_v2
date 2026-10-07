from datetime import datetime, timezone

from bson import ObjectId
from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase


class SavedQueryService:
    """Named SQL a user keeps per database. Private to the user who saved it."""

    MAX_PER_DATABASE = 200

    def __init__(self, mongo_database: AsyncIOMotorDatabase) -> None:
        self.saved = mongo_database["saved_queries"]

    async def initialize(self) -> None:
        await self.saved.create_index([("user_id", 1), ("database_id", 1), ("name", 1)])

    async def list_for(self, user_id: str, database_id: str) -> list[dict]:
        cursor = self.saved.find({"user_id": user_id, "database_id": database_id}).sort("name", 1)
        return [_out(doc) async for doc in cursor]

    async def create(self, user_id: str, database_id: str, name: str, sql: str) -> dict:
        if await self.saved.count_documents({"user_id": user_id, "database_id": database_id}) >= self.MAX_PER_DATABASE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"You can keep at most {self.MAX_PER_DATABASE} saved queries per database",
            )
        doc = {
            "user_id": user_id,
            "database_id": database_id,
            "name": name.strip(),
            "sql": sql.strip(),
            "created_at": datetime.now(timezone.utc),
        }
        doc["_id"] = (await self.saved.insert_one(doc)).inserted_id
        return _out(doc)

    async def delete(self, user_id: str, database_id: str, query_id: str) -> None:
        result = None
        if ObjectId.is_valid(query_id):
            result = await self.saved.delete_one(
                {"_id": ObjectId(query_id), "user_id": user_id, "database_id": database_id}
            )
        if result is None or result.deleted_count == 0:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Saved query not found")

    async def clear_database(self, database_id: str) -> None:
        await self.saved.delete_many({"database_id": database_id})

    async def delete_user(self, user_id: str) -> None:
        await self.saved.delete_many({"user_id": user_id})


def _out(doc: dict) -> dict:
    return {"id": str(doc["_id"]), "name": doc["name"], "sql": doc["sql"], "created_at": doc["created_at"]}
