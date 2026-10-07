from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase


class UsageService:
    """LLM token usage per user per UTC day, and the optional daily token cap."""

    def __init__(self, mongo_database: AsyncIOMotorDatabase) -> None:
        self.usage = mongo_database["llm_usage"]

    async def initialize(self) -> None:
        await self.usage.create_index([("user_id", 1), ("day", -1)], unique=True)

    async def record(self, user_id: str, input_tokens: int, output_tokens: int) -> None:
        await self.usage.update_one(
            {"user_id": user_id, "day": _today()},
            {"$inc": {"input_tokens": input_tokens, "output_tokens": output_tokens, "requests": 1}},
            upsert=True,
        )

    async def check_daily_limit(self, user_id: str, limit: int) -> None:
        if limit <= 0:
            return
        doc = await self.usage.find_one({"user_id": user_id, "day": _today()}) or {}
        used = doc.get("input_tokens", 0) + doc.get("output_tokens", 0)
        if used >= limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Daily limit of {limit:,} model tokens reached. Resets at midnight UTC.",
            )

    async def recent(self, user_id: str, days: int = 30) -> list[dict]:
        since = (datetime.now(timezone.utc) - timedelta(days=days - 1)).strftime("%Y-%m-%d")
        cursor = self.usage.find({"user_id": user_id, "day": {"$gte": since}}).sort("day", -1)
        return [
            {
                "day": doc["day"],
                "input_tokens": doc.get("input_tokens", 0),
                "output_tokens": doc.get("output_tokens", 0),
                "requests": doc.get("requests", 0),
            }
            async for doc in cursor
        ]

    async def delete_user(self, user_id: str) -> None:
        await self.usage.delete_many({"user_id": user_id})


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")
