from fastapi import APIRouter, Depends, Request, status
from app.services.auth import AuthService
from app.core.config import get_settings
from app.models.auth import (
    MessageResponse,
    RefreshRequest,
    TokenResponse,
    UserCreateRequest,
    UserLoginRequest,
    UserResponse,
)

router = APIRouter(prefix = "/auth", tags = ["auth"])

def get_auth_service(request: Request) -> AuthService:
    database_manager = request.app.state.database_manager

    if database_manager.mongo_database is None:
        raise RuntimeError("MongoDB is not initialized")

    return AuthService(
        database = database_manager.mongo_database,
        settings = get_settings()
    )

@router.post("/register", response_model = UserResponse, status_code = status.HTTP_201_CREATED)
async def register(request: UserCreateRequest, 
                   auth_service: AuthService = Depends(get_auth_service)) -> UserResponse:
    return await auth_service.register(request)

@router.post("/login", response_model = TokenResponse)
async def login(request: UserLoginRequest,
                auth_service: AuthService = Depends(get_auth_service)
) -> TokenResponse:
    return await auth_service.login(request)

@router.post("/refresh", response_model = TokenResponse)
async def refresh(request: RefreshRequest,
                  auth_service: AuthService = Depends(get_auth_service)
                  ) -> TokenResponse:
    return await auth_service.refresh(request)

@router.post("/logout", response_model = MessageResponse)
async def logout(request: RefreshRequest,
                 auth_service: AuthService = Depends(get_auth_service)) -> MessageResponse:
    await auth_service.revoke_refresh_token(request)
    return MessageResponse(message = "Successfully logged out")