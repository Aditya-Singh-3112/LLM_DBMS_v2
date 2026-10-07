"""Conversations, saved queries and the activity log of a database."""
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import BaseModel, Field

from app.api.dependencies import get_current_user
from app.core.database import DatabaseManager
from app.models.auth import UserResponse
from app.models.contracts import AccessLevel
from app.services.audit_service import AuditService
from app.services.conversation_service import ConversationService
from app.services.database_registry import DatabaseRegistryService
from app.services.permission_service import PermissionService
from app.services.saved_query_service import SavedQueryService

router = APIRouter(prefix="/databases", tags=["history"])


class RenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class SavedQueryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    sql: str = Field(min_length=1, max_length=50_000)


def _mongo(request: Request):
    return request.app.state.database_manager.mongo_database


async def _require_access(
    request: Request, database_id: str, user: UserResponse, level: AccessLevel = AccessLevel.READ
) -> None:
    database_manager: DatabaseManager = request.app.state.database_manager
    registry = DatabaseRegistryService(mongo_database=database_manager.mongo_database, database_manager=database_manager)
    await PermissionService(registry).check_access(database_id=database_id, user_id=user.id, required_level=level)


# ---------------------------------------------------------- conversations

@router.get("/{database_id}/conversations")
async def list_conversations(
    database_id: str, request: Request, current_user: UserResponse = Depends(get_current_user)
) -> list[dict]:
    await _require_access(request, database_id, current_user)
    return await ConversationService(_mongo(request)).list_for(current_user.id, database_id)


@router.get("/{database_id}/conversations/{conversation_id}")
async def get_conversation(
    database_id: str, conversation_id: str, request: Request,
    current_user: UserResponse = Depends(get_current_user),
) -> dict:
    await _require_access(request, database_id, current_user)
    return await ConversationService(_mongo(request)).get(current_user.id, database_id, conversation_id)


@router.patch("/{database_id}/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def rename_conversation(
    database_id: str, conversation_id: str, body: RenameRequest, request: Request,
    current_user: UserResponse = Depends(get_current_user),
) -> Response:
    await ConversationService(_mongo(request)).rename(current_user.id, database_id, conversation_id, body.title)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{database_id}/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    database_id: str, conversation_id: str, request: Request,
    current_user: UserResponse = Depends(get_current_user),
) -> Response:
    await ConversationService(_mongo(request)).delete(current_user.id, database_id, conversation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------- saved queries

@router.get("/{database_id}/saved-queries")
async def list_saved_queries(
    database_id: str, request: Request, current_user: UserResponse = Depends(get_current_user)
) -> list[dict]:
    await _require_access(request, database_id, current_user)
    return await SavedQueryService(_mongo(request)).list_for(current_user.id, database_id)


@router.post("/{database_id}/saved-queries", status_code=status.HTTP_201_CREATED)
async def save_query(
    database_id: str, body: SavedQueryCreate, request: Request,
    current_user: UserResponse = Depends(get_current_user),
) -> dict:
    await _require_access(request, database_id, current_user)
    return await SavedQueryService(_mongo(request)).create(current_user.id, database_id, body.name, body.sql)


@router.delete("/{database_id}/saved-queries/{query_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_saved_query(
    database_id: str, query_id: str, request: Request,
    current_user: UserResponse = Depends(get_current_user),
) -> Response:
    await SavedQueryService(_mongo(request)).delete(current_user.id, database_id, query_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------- activity log

@router.get("/{database_id}/activity")
async def activity_log(
    database_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    before: datetime | None = Query(default=None, description="Page backwards from this timestamp"),
    writes_only: bool = Query(default=False),
    current_user: UserResponse = Depends(get_current_user),
) -> list[dict]:
    """Every tool call against this database, newest first. Owners only."""
    await _require_access(request, database_id, current_user, AccessLevel.OWNER)
    return await AuditService(_mongo(request)).list_for(database_id, limit=limit, before=before, writes_only=writes_only)
