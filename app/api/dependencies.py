import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings
from app.core.security import decode_access_token
from app.models.auth import UserResponse
from app.services.auth import AuthService
from app.api.auth import get_auth_service

bearer_scheme = HTTPBearer()

async def get_current_user(request: Request,
                           credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
                           auth_service: AuthService = Depends(get_auth_service)) -> UserResponse:
    try:
        payload = decode_access_token(
            credentials.credentials,
            get_settings()
        )
    except jwt.PyJWTError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from error

    return await auth_service.get_user(payload["sub"])