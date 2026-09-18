from datetime import datetime, timezone

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from motor.motor_asyncio import AsyncIOMotorDatabase


class ConversationService:
    """
    Per (user, database) chat history so follow-up questions can refer to
    earlier ones. Kept short: only the last MAX_MESSAGES messages are stored
    and idle conversations expire after TTL_DAYS.
    """

    MAX_MESSAGES = 20
    TTL_DAYS = 7

    def __init__(self, mongo_database: AsyncIOMotorDatabase) -> None:
        self.conversations = mongo_database["conversations"]

    async def initialize(self) -> None:
        await self.conversations.create_index([("user_id", 1), ("database_id", 1)], unique=True)
        await self.conversations.create_index("updated_at", expireAfterSeconds=self.TTL_DAYS * 86400)

    async def get_history(self, user_id: str, database_id: str) -> list[BaseMessage]:
        doc = await self.conversations.find_one({"user_id": user_id, "database_id": database_id})
        if not doc:
            return []
        return [
            HumanMessage(content=m["content"]) if m["role"] == "human" else AIMessage(content=m["content"])
            for m in doc.get("messages", [])
        ]

    async def append(self, user_id: str, database_id: str, question: str, answer: str) -> None:
        now = datetime.now(timezone.utc)
        await self.conversations.update_one(
            {"user_id": user_id, "database_id": database_id},
            {
                "$push": {
                    "messages": {
                        "$each": [
                            {"role": "human", "content": question, "ts": now},
                            {"role": "ai", "content": answer, "ts": now},
                        ],
                        "$slice": -self.MAX_MESSAGES,
                    }
                },
                "$set": {"updated_at": now},
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )

    async def clear(self, user_id: str, database_id: str) -> None:
        await self.conversations.delete_one({"user_id": user_id, "database_id": database_id})

    async def clear_database(self, database_id: str) -> None:
        await self.conversations.delete_many({"database_id": database_id})
