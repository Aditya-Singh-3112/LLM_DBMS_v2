from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel


class ToolCallSource(str, Enum):
    MCP = "mcp"
    RAG = "rag"


class ToolCallStatus(str, Enum):
    SUCCESS = "success"
    DENIED = "denied"
    ERROR = "error"
    PENDING = "pending"  # write awaiting user confirmation


class ToolCallEntry(BaseModel):
    tool_name: str
    args: dict[str, Any]
    result: Any | None = None
    status: ToolCallStatus
    duration_ms: int
    timestamp: datetime
    source: ToolCallSource