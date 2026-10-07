from datetime import datetime

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase


class AuditService:
    """
    Read side of `tool_call_logs`, which MCPToolService writes for every
    tool call (agent or app): who ran what against a database, and how it went.
    """

    def __init__(self, mongo_database: AsyncIOMotorDatabase) -> None:
        self.logs = mongo_database["tool_call_logs"]
        self.users = mongo_database["user"]

    async def initialize(self) -> None:
        await self.logs.create_index([("database_id", 1), ("timestamp", -1)])

    async def list_for(
        self,
        database_id: str,
        limit: int = 50,
        before: datetime | None = None,
        writes_only: bool = False,
    ) -> list[dict]:
        query: dict = {"database_id": database_id}
        if before is not None:
            query["timestamp"] = {"$lt": before}
        if writes_only:
            query["$or"] = [
                {"tool_name": "undo"},
                {"tool_name": "run_sql", "result.operation": {"$nin": [None, "select"]}},
            ]

        entries = [doc async for doc in self.logs.find(query).sort("timestamp", -1).limit(limit)]

        user_ids = {e["user_id"] for e in entries if ObjectId.is_valid(e.get("user_id", ""))}
        emails = {
            str(u["_id"]): u["email"]
            async for u in self.users.find({"_id": {"$in": [ObjectId(i) for i in user_ids]}}, {"email": 1})
        }

        return [
            {
                "id": str(e["_id"]),
                "timestamp": e["timestamp"],
                "user_id": e["user_id"],
                "user_email": emails.get(e["user_id"]),
                "tool_name": e["tool_name"],
                "sql": (e.get("args") or {}).get("sql"),
                "args": e.get("args"),
                "status": e["status"],
                "result": e.get("result"),
                "duration_ms": e.get("duration_ms"),
            }
            for e in entries
        ]
