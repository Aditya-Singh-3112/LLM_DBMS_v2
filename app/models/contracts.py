from datetime import datetime
from enum import Enum
from typing import Any
from pydantic import BaseModel, EmailStr, Field, model_validator

from app.models.tool_call_log import ToolCallEntry

class AccessLevel(str, Enum):
    OWNER = "owner"
    WRITE = "write"
    READ = "read"

class AskRequest(BaseModel):
    query: str = Field(min_length=1, max_length=10_000)
    # Start a fresh conversation before answering this question.
    reset: bool = False


class GroundedReference(BaseModel):
    source: str
    chapter: str | None = None


class QueryResult(BaseModel):
    columns: list[str]
    rows: list[list[Any]]


class PendingWrite(BaseModel):
    """A write statement the agent proposed but did not execute."""
    sql: str


class SqlConfirmRequest(BaseModel):
    sql: str = Field(min_length=1, max_length=50_000)


class AskResponse(BaseModel):
    answer: str
    sql: str | None = None
    result: QueryResult | None = None
    tool_calls: list[ToolCallEntry] = Field(default_factory=list)
    grounded_on: list[GroundedReference] | None = None
    pending_write: PendingWrite | None = None
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
    """Grant by email (preferred) or by user id."""
    email: EmailStr | None = None
    user_id: str | None = None
    access_level: AccessLevel

    @model_validator(mode="after")
    def _one_target(self) -> "PermissionGrantRequest":
        if bool(self.email) == bool(self.user_id):
            raise ValueError("Provide exactly one of email or user_id")
        return self


class PermissionResponse(BaseModel):
    database_id: str
    user_id: str
    email: str | None = None
    access_level: AccessLevel
