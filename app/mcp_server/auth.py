import jwt
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.middleware.auth_context import get_access_token

from app.core.config import Settings
from app.core.security import decode_access_token

CLIENT_ID = "llm-dbms"


class JwtTokenVerifier:
    """
    Accepts the same access tokens the REST API issues. The SDK's bearer
    middleware calls this on every HTTP request and answers 401 when it
    returns None, so no tool ever runs without an authenticated user.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            payload = decode_access_token(token, self.settings)
        except jwt.PyJWTError:
            return None
        return AccessToken(
            token=token,
            client_id=CLIENT_ID,
            scopes=[],
            expires_at=int(payload["exp"]),
            subject=payload["sub"],
        )


def current_user_id() -> str:
    """The user id of the bearer token on the request being served."""
    access_token = get_access_token()
    if access_token is None or not access_token.subject:
        # Unreachable behind the bearer middleware; fail closed regardless.
        raise PermissionError("Not authenticated")
    return access_token.subject
