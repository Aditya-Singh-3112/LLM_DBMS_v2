"""
Shared fixtures.

API tests run the real FastAPI app against the docker-compose Postgres,
MongoDB and Redis, with a throwaway Mongo database per session. The LLM is
never called: RAG is disabled and the agent is replaced by a fake.
"""
import os
import uuid

os.environ.setdefault("APP_ENV", "development")
os.environ["MONGO_DATABASE"] = f"test_{uuid.uuid4().hex[:8]}"
os.environ["RAG_ENABLED"] = "false"
os.environ["ASK_DAILY_LIMIT"] = "0"

import httpx  # noqa: E402
import pytest  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from app.core.config import get_settings  # noqa: E402


async def _services_reachable() -> bool:
    settings = get_settings()
    try:
        client = AsyncIOMotorClient(settings.mongo_uri, serverSelectionTimeoutMS=1500)
        await client.admin.command("ping")
        client.close()
        import asyncpg
        conn = await asyncpg.connect(settings.postgres_uri, timeout=3)
        await conn.close()
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
async def app():
    if not await _services_reachable():
        pytest.skip("docker-compose services are not reachable")

    from app.main import app as fastapi_app

    async with fastapi_app.router.lifespan_context(fastapi_app):
        yield fastapi_app

    settings = get_settings()
    client = AsyncIOMotorClient(settings.mongo_uri)
    await client.drop_database(settings.mongo_database)
    client.close()


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class User:
    def __init__(self, email: str, headers: dict, client: httpx.AsyncClient):
        self.email = email
        self.headers = headers
        self.client = client
        self.id: str | None = None

    async def me(self) -> str:
        if self.id is None:
            r = await self.client.get("/auth/me", headers=self.headers)
            self.id = r.json()["id"]
        return self.id


@pytest.fixture
async def make_user(client):
    async def _make() -> User:
        email = f"{uuid.uuid4().hex[:8]}@example.com"
        r = await client.post("/auth/register", json={"email": email, "password": "password123"})
        assert r.status_code == 201, r.text
        r = await client.post("/auth/login", json={"email": email, "password": "password123"})
        assert r.status_code == 200, r.text
        return User(email, {"Authorization": f"Bearer {r.json()['access_token']}"}, client)

    return _make


@pytest.fixture
async def owner(make_user):
    return await make_user()


@pytest.fixture
async def database(client, owner):
    """A fresh database owned by `owner`, dropped after the test."""
    r = await client.post("/databases", json={"name": "test_db"}, headers=owner.headers)
    assert r.status_code == 201, r.text
    database_id = r.json()["id"]
    yield database_id
    await client.delete(f"/databases/{database_id}", headers=owner.headers)


@pytest.fixture
async def seeded(client, owner, database):
    """`database` with a customers table imported from CSV."""
    csv_bytes = b"id,name,city,orders\n1,Ann,Pune,3\n2,Bob,Delhi,0\n3,Cy,Pune,1\n"
    r = await client.post(
        f"/databases/{database}/tables/import",
        files={"file": ("customers.csv", csv_bytes)},
        headers=owner.headers,
    )
    assert r.status_code == 200, r.text
    return database


class FakeAction:
    def __init__(self, tool: str, tool_input: dict):
        self.tool = tool
        self.tool_input = tool_input


class FakeExecutor:
    """
    Stands in for the LangChain AgentExecutor. `script` is a list of
    (tool_name, args) calls to make for real through the tool client,
    followed by a canned answer.
    """

    def __init__(self, tool_client, script, answer):
        self.tool_client = tool_client
        self.script = script
        self.answer = answer

    async def ainvoke(self, inputs):
        steps = []
        for tool, args in self.script:
            fn = getattr(self.tool_client, tool)
            try:
                observation = await fn(**args)
                observation = str(observation)
            except Exception as e:  # ToolException -> observation text, like LangChain does
                observation = str(e)
            steps.append((FakeAction(tool, args), observation))
        return {"output": self.answer, "intermediate_steps": steps}


@pytest.fixture
def fake_agent(monkeypatch):
    """
    Patch AgentFactory so /ask runs `script` through the real tool service
    and returns `answer`, without touching an LLM.
    """
    from app.api import ask as ask_module

    def _install(script, answer="done"):
        class FakeFactory:
            def __init__(self, tool_client, rag_service, google_api_key, model=None):
                self.tool_client = tool_client
                self.rag_tool = None

            async def create_agent_executor(self, database_id, max_iterations=10):
                return FakeExecutor(self.tool_client, script, answer)

        monkeypatch.setattr(ask_module, "AgentFactory", FakeFactory)

    return _install
