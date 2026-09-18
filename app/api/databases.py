from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.api.dependencies import get_current_user
from app.core.database import DatabaseManager
from app.models.auth import UserResponse
from app.models.contracts import (
    AccessLevel,
    DatabaseCreateRequest,
    DatabaseResponse,
    PermissionGrantRequest,
    PermissionResponse,
)
from app.services.database_registry import DatabaseRegistryService


router = APIRouter(prefix="/databases", tags=["databases"])


def get_registry_service(
    request: Request,
) -> DatabaseRegistryService:
    database_manager: DatabaseManager = (
        request.app.state.database_manager
    )

    if database_manager.mongo_database is None:
        raise RuntimeError("MongoDB is not initialized")

    return DatabaseRegistryService(
        mongo_database=database_manager.mongo_database,
        database_manager=database_manager,
    )


@router.post(
    "",
    response_model=DatabaseResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_database(
    request: DatabaseCreateRequest,
    current_user: UserResponse = Depends(get_current_user),
    service: DatabaseRegistryService = Depends(get_registry_service),
) -> DatabaseResponse:
    return await service.create(request, current_user.id)


@router.get("", response_model=list[DatabaseResponse])
async def list_databases(
    current_user: UserResponse = Depends(get_current_user),
    service: DatabaseRegistryService = Depends(get_registry_service),
) -> list[DatabaseResponse]:
    return await service.list_for_user(current_user.id)


@router.delete(
    "/{database_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_database(
    database_id: str,
    current_user: UserResponse = Depends(get_current_user),
    service: DatabaseRegistryService = Depends(get_registry_service),
) -> Response:
    await service.delete(database_id, current_user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{database_id}/share",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def share_database(
    database_id: str,
    request: PermissionGrantRequest,
    current_user: UserResponse = Depends(get_current_user),
    service: DatabaseRegistryService = Depends(get_registry_service),
) -> Response:
    if request.access_level == AccessLevel.OWNER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OWNER cannot be granted",
        )

    user_id = request.user_id or await service.resolve_user_id(request.email)

    await service.share(
        database_id=database_id,
        owner_id=current_user.id,
        user_id=user_id,
        access_level=request.access_level,
    )

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{database_id}/permissions", response_model=list[PermissionResponse])
async def list_permissions(
    database_id: str,
    current_user: UserResponse = Depends(get_current_user),
    service: DatabaseRegistryService = Depends(get_registry_service),
) -> list[PermissionResponse]:
    return await service.list_permissions(database_id, current_user.id)


@router.delete(
    "/{database_id}/permissions/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_permission(
    database_id: str,
    user_id: str,
    current_user: UserResponse = Depends(get_current_user),
    service: DatabaseRegistryService = Depends(get_registry_service),
) -> Response:
    await service.revoke(database_id, current_user.id, user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)