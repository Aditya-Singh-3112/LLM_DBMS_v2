import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from passlib.context import CryptContext
from app.core.config import Settings

password_context = CryptContext(
    schemes = ["argon2"],
    deprecated = "auto"
)

def hash_password(password: str) -> str:
    return password_context.hash(password)

def verify_password(password: str, password_hash: str) -> bool:
    return password_context.verify(password, password_hash)

def create_access_token(subject: str, settings: Settings) -> tuple[str, int]:
    expires_in = settings.access_token_expire_minutes * 60
    expires_at = datetime.now(timezone.utc) + timedelta(seconds = expires_in)

    payload = {
        "sub": subject,
        "type": "access",
        "exp": expires_at,
        "iat": datetime.now(timezone.utc)
    }

    token = jwt.encode(
        payload,
        settings.jwt_secret,
        algorithm = settings.jwt_algorithm
    )

    return token, expires_in

def create_refresh_token() -> tuple[str, str]:
    raw_token = secrets.token_urlsafe(64)
    token_hash = hash_token(raw_token)

    return raw_token, token_hash

def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

def decode_access_token(token: str, settings: Settings) -> dict[str, Any]:
    payload = jwt.decode(
        token,
        settings.jwt_secret,
        algorithms = [settings.jwt_algorithm]
    )

    if payload.get("type") != 'access':
        raise jwt.InvalidTokenError("invalid token type")

    if not payload.get("sub"):
        raise jwt.InvalidTokenError("Token has no subject")

    return payload