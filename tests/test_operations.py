"""Health checks, metrics, migrations and LLM usage limits."""
import uuid

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

from app.core.config import get_settings
from app.core.migrations import MIGRATIONS, run_migrations

pytestmark = pytest.mark.asyncio


async def test_health_reports_every_dependency(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "postgres": "ok", "mongo": "ok", "redis": "ok", "mcp": "ok"}
    assert (await client.get("/health/live")).json() == {"status": "alive"}


async def test_health_is_503_when_postgres_is_down(client, app, monkeypatch):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def broken():
        raise ConnectionError("down")
        yield

    monkeypatch.setattr(app.state.database_manager, "postgres_connection", broken)
    r = await client.get("/health")
    assert r.status_code == 503 and r.json()["status"] == "unavailable" and r.json()["postgres"] == "unavailable"


async def test_health_is_degraded_without_mcp(client, app, monkeypatch):
    monkeypatch.setattr(app.state, "mcp_transport", None)
    monkeypatch.setattr(get_settings(), "mcp_server_url", "http://127.0.0.1:9/mcp")
    r = await client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "degraded" and r.json()["mcp"] == "unavailable"


async def test_metrics_use_route_templates(client, owner, seeded):
    await client.get(f"/databases/{seeded}/tables", headers=owner.headers)
    text = (await client.get("/metrics")).text
    assert 'route="/databases/{database_id}/tables"' in text
    assert seeded not in text
    assert "tool_calls_total" in text


async def test_migrations_upgrade_legacy_data_once():
    settings = get_settings()
    mongo_client = AsyncIOMotorClient(settings.mongo_uri)
    mongo = mongo_client[f"migration_test_{uuid.uuid4().hex[:8]}"]

    class Manager:
        mongo_database = mongo

    try:
        legacy = mongo["conversations"]
        await legacy.create_index([("user_id", 1), ("database_id", 1)], unique=True)
        await legacy.create_index("updated_at", expireAfterSeconds=7 * 86400)
        await legacy.insert_one({"user_id": "u", "database_id": "d", "messages": [{"role": "human", "content": "Old   question"}]})
        await mongo["user"].insert_one({"email": "old@example.com"})

        assert await run_migrations(Manager()) == [m[0] for m in MIGRATIONS]
        assert await run_migrations(Manager()) == []

        assert list((await legacy.index_information()).keys()) == ["_id_"]
        assert (await legacy.find_one())["title"] == "Old question"
        assert (await mongo["user"].find_one())["email_verified"] is False
        # Many conversations per (user, database) are now allowed.
        await legacy.insert_one({"user_id": "u", "database_id": "d", "messages": []})
    finally:
        await mongo_client.drop_database(mongo.name)
        mongo_client.close()


async def test_daily_token_limit_blocks_asks(client, app, owner, seeded, fake_agent, monkeypatch):
    from app.services.usage_service import UsageService

    usage = UsageService(app.state.database_manager.mongo_database)
    await usage.record(await owner.me(), input_tokens=700, output_tokens=400)
    r = await client.get("/auth/me/usage", headers=owner.headers)
    today = r.json()["days"][0]
    assert (today["input_tokens"], today["output_tokens"], today["requests"]) == (700, 400, 1)

    monkeypatch.setattr(get_settings(), "ask_daily_token_limit", 1000)
    fake_agent(script=[], answer="unused")
    r = await client.post(f"/databases/{seeded}/ask", json={"query": "x"}, headers=owner.headers)
    assert r.status_code == 429 and "tokens" in r.json()["detail"]
