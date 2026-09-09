from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import jwt

from app.core.security import decode_access_token
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
bearer_scheme = HTTPBearer()

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

@router.get("/me", response_model=UserResponse)
async def get_me(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    auth_service: AuthService = Depends(get_auth_service),
) -> UserResponse:
    try:
        payload = decode_access_token(credentials.credentials, get_settings())
    except jwt.PyJWTError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from error

    return await auth_service.get_user(payload["sub"])