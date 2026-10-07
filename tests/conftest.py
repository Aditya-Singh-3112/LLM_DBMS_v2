"""
Shared fixtures.

API tests run the real FastAPI app and the real MCP server against the
docker-compose Postgres, MongoDB and Redis, with a throwaway Mongo database
per session. The API reaches the MCP server over Streamable HTTP through an
in-process ASGI transport. The LLM is never called: RAG is disabled and the
agent is replaced by a fake that calls tools through the MCP client.
"""
import asyncio
import os
import uuid

os.environ.setdefault("APP_ENV", "development")
os.environ["MONGO_DATABASE"] = f"test_{uuid.uuid4().hex[:8]}"
os.environ["RAG_ENABLED"] = "false"
os.environ["ASK_DAILY_LIMIT"] = "0"
os.environ["EMAIL_BACKEND"] = "memory"
# Every test client shares one IP; only the throttle tests should trip it.
os.environ["LOGIN_MAX_FAILURES_PER_IP"] = "100000"

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
    from app.mcp_server.main import app as mcp_app

    # The MCP session manager runs an anyio task group, which must be
    # entered and exited in one task; fixture setup and teardown are not.
    mcp_started, mcp_stop = asyncio.Event(), asyncio.Event()

    async def serve_mcp():
        async with mcp_app.router.lifespan_context(mcp_app):
            mcp_started.set()
            await mcp_stop.wait()

    mcp_task = asyncio.create_task(serve_mcp())
    await asyncio.wait({mcp_task, asyncio.create_task(mcp_started.wait())}, return_when=asyncio.FIRST_COMPLETED)
    if mcp_task.done():
        mcp_task.result()  # startup failed: raise its error

    async with fastapi_app.router.lifespan_context(fastapi_app):
        fastapi_app.state.mcp_transport = httpx.ASGITransport(app=mcp_app)
        yield fastapi_app

    mcp_stop.set()
    await mcp_task

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
async def mcp_client_for(app):
    """Open a real MCP client session as the given user (or with a raw token)."""
    from contextlib import asynccontextmanager

    from app.agent.mcp_client import MCPToolClient

    @asynccontextmanager
    async def _open(user: "User | None" = None, token: str | None = None):
        if token is None:
            token = user.headers["Authorization"].removeprefix("Bearer ")
        async with MCPToolClient(
            get_settings().mcp_server_url, token, transport=app.state.mcp_transport
        ) as client:
            yield client

    return _open


def emailed_token(to: str, path: str) -> str:
    """The token from the newest email to `to` whose link points at `path`."""
    from app.services.email_service import OUTBOX

    for message in reversed(OUTBOX):
        if message.to == to and f"{path}?token=" in message.body:
            return message.body.split(f"{path}?token=")[1].split()[0]
    raise AssertionError(f"no email to {to} with a {path} link")


@pytest.fixture
async def make_user(client):
    async def _make(verified: bool = True) -> User:
        email = f"{uuid.uuid4().hex[:8]}@example.com"
        r = await client.post("/auth/register", json={"email": email, "password": "password123"})
        assert r.status_code == 201, r.text
        if verified:
            r = await client.post("/auth/verify-email", json={"token": emailed_token(email, "/verify-email")})
            assert r.status_code == 200, r.text
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
    (tool_name, args) calls to make for real through the MCP client,
    followed by a canned answer.
    """

    def __init__(self, mcp_client, script, answer):
        self.mcp_client = mcp_client
        self.script = script
        self.answer = answer

    async def ainvoke(self, inputs, config=None):
        steps = []
        for tool, args in self.script:
            try:
                observation = await self.mcp_client.call_tool(tool, args)
            except Exception as e:  # ToolException -> observation text, like LangChain does
                observation = str(e)
            steps.append((FakeAction(tool, args), observation))
        return {"output": self.answer, "intermediate_steps": steps}

    async def astream_events(self, inputs, version, config=None):
        """The subset of LangChain's v2 events the /ask/stream route reads."""
        result = await self.ainvoke(inputs)
        for action, observation in result["intermediate_steps"]:
            yield {"event": "on_tool_start", "name": action.tool, "data": {"input": action.tool_input}}
            yield {"event": "on_tool_end", "name": action.tool, "data": {"input": action.tool_input, "output": observation}}
        yield {"event": "on_chain_end", "name": "AgentExecutor", "data": {"output": {"output": result["output"]}}}


@pytest.fixture
def fake_agent(monkeypatch):
    """
    Patch AgentFactory so /ask runs `script` against the real MCP server
    and returns `answer`, without touching an LLM.
    """
    from app.api import ask as ask_module

    def _install(script, answer="done"):
        class FakeFactory:
            def __init__(self, mcp_client, google_api_key, model=None):
                self.mcp_client = mcp_client

            async def create_agent_executor(self, database_id, max_iterations=10):
                return FakeExecutor(self.mcp_client, script, answer)

        monkeypatch.setattr(ask_module, "AgentFactory", FakeFactory)

    return _install
