"""
Integration test for refresh-token rotation and revocation.

Requires the local MongoDB from docker-compose (skips if unreachable).
"""
import uuid

import pytest
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

from app.core.config import Settings
from app.models.auth import RefreshRequest, UserCreateRequest, UserLoginRequest
from app.services.auth import AuthService


@pytest.fixture
async def auth_service():
    settings = Settings()
    client = AsyncIOMotorClient(settings.mongo_uri, tz_aware=True, serverSelectionTimeoutMS=1500)
    db_name = f"test_auth_{uuid.uuid4().hex[:8]}"
    try:
        await client.admin.command("ping")
    except Exception:
        pytest.skip("MongoDB is not reachable")
    service = AuthService(database=client[db_name], settings=settings)
    await service.initialize()
    yield service
    await client.drop_database(db_name)
    client.close()


@pytest.mark.asyncio
async def test_refresh_token_is_single_use_and_logout_revokes(auth_service):
    email = f"{uuid.uuid4().hex[:8]}@example.com"
    await auth_service.register(UserCreateRequest(email=email, password="password123"))
    tokens = await auth_service.login(UserLoginRequest(email=email, password="password123"))

    rotated = await auth_service.refresh(RefreshRequest(refresh_token=tokens.refresh_token))
    assert rotated.refresh_token != tokens.refresh_token

    with pytest.raises(HTTPException) as exc:
        await auth_service.refresh(RefreshRequest(refresh_token=tokens.refresh_token))
    assert exc.value.status_code == 401

    await auth_service.revoke_refresh_token(RefreshRequest(refresh_token=rotated.refresh_token))
    with pytest.raises(HTTPException) as exc:
        await auth_service.refresh(RefreshRequest(refresh_token=rotated.refresh_token))
    assert exc.value.status_code == 401
