from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import jwt

from app.core.security import decode_access_token
from app.services.auth import AuthService
from app.core.config import Settings, get_settings
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

REFRESH_COOKIE = "refresh_token"
# Sent only via a preflighted request, so a plain cross-site form cannot
# trigger a refresh with the victim's cookie.
CSRF_HEADER_VALUE = "XMLHttpRequest"


def _set_refresh_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=token,
        max_age=settings.refresh_token_expire_days * 86400,
        path="/auth",
        domain=settings.cookie_domain,
        secure=settings.cookie_secure,
        httponly=True,
        samesite=settings.cookie_samesite,
    )


def _clear_refresh_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=REFRESH_COOKIE,
        path="/auth",
        domain=settings.cookie_domain,
        secure=settings.cookie_secure,
        httponly=True,
        samesite=settings.cookie_samesite,
    )


def _require_csrf_header(x_requested_with: str | None) -> None:
    if x_requested_with != CSRF_HEADER_VALUE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Missing X-Requested-With: {CSRF_HEADER_VALUE} header",
        )

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
                response: Response,
                auth_service: AuthService = Depends(get_auth_service)
) -> TokenResponse:
    tokens = await auth_service.login(request)
    _set_refresh_cookie(response, tokens.refresh_token, get_settings())
    return tokens

@router.post("/refresh", response_model = TokenResponse)
async def refresh(response: Response,
                  refresh_cookie: str | None = Cookie(default=None, alias=REFRESH_COOKIE),
                  x_requested_with: str | None = Header(default=None),
                  auth_service: AuthService = Depends(get_auth_service)
                  ) -> TokenResponse:
    """
    Rotate the refresh token stored in the HttpOnly cookie and return a new
    access token.
    """
    _require_csrf_header(x_requested_with)
    if not refresh_cookie:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No refresh token",
        )
    settings = get_settings()
    try:
        tokens = await auth_service.refresh(RefreshRequest(refresh_token=refresh_cookie))
    except HTTPException:
        _clear_refresh_cookie(response, settings)
        raise
    _set_refresh_cookie(response, tokens.refresh_token, settings)
    return tokens

@router.post("/logout", response_model = MessageResponse)
async def logout(response: Response,
                 refresh_cookie: str | None = Cookie(default=None, alias=REFRESH_COOKIE),
                 x_requested_with: str | None = Header(default=None),
                 auth_service: AuthService = Depends(get_auth_service)) -> MessageResponse:
    _require_csrf_header(x_requested_with)
    if refresh_cookie:
        await auth_service.revoke_refresh_token(RefreshRequest(refresh_token=refresh_cookie))
    _clear_refresh_cookie(response, get_settings())
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