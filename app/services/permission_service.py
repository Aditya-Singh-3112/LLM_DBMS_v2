from fastapi import HTTPException, status

from app.models.contracts import AccessLevel
from app.services.database_registry import DatabaseRegistryService


class PermissionService:
    def __init__(self, registry_service: DatabaseRegistryService) -> None:
        self.registry_service = registry_service

    async def check_access(
        self,
        database_id: str,
        user_id: str,
        required_level: AccessLevel = AccessLevel.READ,
    ) -> AccessLevel:
        """
        Check if user has required access to database.
        
        Returns the actual access level if allowed.
        Raises HTTPException if not allowed.
        """
        actual_level = await self.registry_service.get_access_level(
            database_id=database_id,
            user_id=user_id,
        )

        level_hierarchy = {
            AccessLevel.READ: 1,
            AccessLevel.WRITE: 2,
            AccessLevel.OWNER: 3,
        }

        if level_hierarchy[actual_level] < level_hierarchy[required_level]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"You have {actual_level} access, but {required_level} is required",
            )

        return actual_level