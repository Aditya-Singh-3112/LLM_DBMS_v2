from datetime import datetime, timezone

from bson import ObjectId
from fastapi import HTTPException, status
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from motor.motor_asyncio import AsyncIOMotorDatabase


class ConversationService:
    """
    Saved conversations per (user, database). A user can keep many, reopen
    any of them, and continue it; follow-up questions see the last
    HISTORY_MESSAGES messages of the conversation they belong to.
    """

    HISTORY_MESSAGES = 20
    MAX_MESSAGES = 500
    TITLE_LENGTH = 80

    def __init__(self, mongo_database: AsyncIOMotorDatabase) -> None:
        self.conversations = mongo_database["conversations"]

    async def initialize(self) -> None:
        await self.conversations.create_index([("user_id", 1), ("database_id", 1), ("updated_at", -1)])

    async def list_for(self, user_id: str, database_id: str, limit: int = 100) -> list[dict]:
        cursor = (
            self.conversations.find(
                {"user_id": user_id, "database_id": database_id},
                {"title": 1, "created_at": 1, "updated_at": 1, "message_count": {"$size": "$messages"}},
            )
            .sort("updated_at", -1)
            .limit(limit)
        )
        return [
            {
                "id": str(doc["_id"]),
                "title": doc.get("title") or "Untitled",
                "created_at": doc.get("created_at"),
                "updated_at": doc.get("updated_at"),
                "message_count": doc.get("message_count", 0),
            }
            async for doc in cursor
        ]

    async def get(self, user_id: str, database_id: str, conversation_id: str) -> dict:
        doc = await self._find(user_id, database_id, conversation_id)
        return {
            "id": str(doc["_id"]),
            "title": doc.get("title") or "Untitled",
            "created_at": doc.get("created_at"),
            "updated_at": doc.get("updated_at"),
            "messages": [
                {"role": m["role"], "content": m["content"], "sql": m.get("sql"), "ts": m.get("ts")}
                for m in doc.get("messages", [])
            ],
        }

    async def get_history(self, user_id: str, database_id: str, conversation_id: str | None) -> list[BaseMessage]:
        if conversation_id is None:
            return []
        doc = await self._find(user_id, database_id, conversation_id)
        return [
            HumanMessage(content=m["content"]) if m["role"] == "human" else AIMessage(content=m["content"])
            for m in doc.get("messages", [])[-self.HISTORY_MESSAGES:]
        ]

    async def append(
        self,
        user_id: str,
        database_id: str,
        conversation_id: str | None,
        question: str,
        answer: str,
        sql: str | None = None,
    ) -> str:
        """Add one exchange; starts a conversation when conversation_id is None. Returns its id."""
        now = datetime.now(timezone.utc)
        messages = [
            {"role": "human", "content": question, "ts": now},
            {"role": "ai", "content": answer, "sql": sql, "ts": now},
        ]
        if conversation_id is None:
            result = await self.conversations.insert_one({
                "user_id": user_id,
                "database_id": database_id,
                "title": _title(question, self.TITLE_LENGTH),
                "messages": messages,
                "created_at": now,
                "updated_at": now,
            })
            return str(result.inserted_id)

        await self._find(user_id, database_id, conversation_id)
        await self.conversations.update_one(
            {"_id": ObjectId(conversation_id)},
            {
                "$push": {"messages": {"$each": messages, "$slice": -self.MAX_MESSAGES}},
                "$set": {"updated_at": now},
            },
        )
        return conversation_id

    async def rename(self, user_id: str, database_id: str, conversation_id: str, title: str) -> None:
        await self._find(user_id, database_id, conversation_id)
        await self.conversations.update_one(
            {"_id": ObjectId(conversation_id)}, {"$set": {"title": title.strip()[: self.TITLE_LENGTH]}}
        )

    async def delete(self, user_id: str, database_id: str, conversation_id: str) -> None:
        await self._find(user_id, database_id, conversation_id)
        await self.conversations.delete_one({"_id": ObjectId(conversation_id)})

    async def clear_database(self, database_id: str) -> None:
        await self.conversations.delete_many({"database_id": database_id})

    async def delete_user(self, user_id: str) -> None:
        await self.conversations.delete_many({"user_id": user_id})

    async def _find(self, user_id: str, database_id: str, conversation_id: str) -> dict:
        doc = None
        if ObjectId.is_valid(conversation_id):
            doc = await self.conversations.find_one(
                {"_id": ObjectId(conversation_id), "user_id": user_id, "database_id": database_id}
            )
        if doc is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
        return doc


def _title(question: str, length: int) -> str:
    title = " ".join(question.split())
    return title if len(title) <= length else title[: length - 1] + "…"
