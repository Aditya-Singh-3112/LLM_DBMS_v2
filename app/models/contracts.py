from datetime import datetime
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field

class AccessLevel(str, Enum):
    OWNER = "owner"
    WRITE = "write"
    READ = "read"

class ToolCallSource(str, Enum):
    MCP = "mcp"
    RAG = "rag"

class ToolCallStatus(str, Enum):
    SUCCESS = "success"
    DENIED = "denied"
    ERROR = "error"

class ToolCall(BaseModel):
    tool_name: str
    args: dict[str, Any] = Field(default_factory = dict)
    result: Any | None = None
    status: ToolCallStatus
    duration_ms: int = Field(ge = 0)
    timestamp: datetime
    source: ToolCallSource

class AskRequest(BaseModel):
    query: str = Field(min_length=1, max_length=10_000)


class GroundedReference(BaseModel):
    source: str
    chapter: str | None = None


class QueryResult(BaseModel):
    columns: list[str]
    rows: list[list[Any]]


class AskResponse(BaseModel):
    answer: str
    sql: str | None = None
    result: QueryResult | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    grounded_on: list[GroundedReference] | None = None
    total_execution_time_ms: int = Field(ge=0)

class DatabaseCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class DatabaseResponse(BaseModel):
    id: str
    name: str
    owner_id: str
    access_level: AccessLevel
    created_at: datetime


class PermissionGrantRequest(BaseModel):
    user_id: str
    access_level: AccessLevel


class PermissionResponse(BaseModel):
    database_id: str
    user_id: str
    access_level: AccessLevel