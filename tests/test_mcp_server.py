"""
The MCP server, exercised through a real MCP client session over
Streamable HTTP (initialize, tools/list, tools/call).
"""
import json

import pytest
from langchain_core.tools import ToolException

from app.agent.mcp_client import MCPUnavailableError
from app.core.config import get_settings


async def test_session_requires_a_valid_bearer_token(mcp_client_for):
    with pytest.raises(MCPUnavailableError):
        async with mcp_client_for(token="not-a-jwt"):
            pass


async def test_tools_are_discovered_with_schemas(owner, mcp_client_for):
    async with mcp_client_for(owner) as mcp:
        tools = {t.name: t for t in mcp.tools}

    assert set(tools) == {
        "list_databases", "list_schemas", "describe_table",
        "sample_rows", "run_sql", "sql_reference_lookup",
    }
    run_sql = tools["run_sql"].inputSchema
    assert set(run_sql["required"]) == {"database_id", "sql"}
    # Writes can only be confirmed through the REST API, never over MCP.
    assert "confirmed" not in run_sql["properties"]
    assert tools["sample_rows"].inputSchema["properties"]["limit"]["maximum"] == 100


async def test_list_databases_and_read_tools(owner, seeded, mcp_client_for):
    async with mcp_client_for(owner) as mcp:
        assert seeded in await mcp.call_tool("list_databases", {})
        assert "customers" in await mcp.call_tool("list_schemas", {"database_id": seeded})
        described = await mcp.call_tool("describe_table", {"database_id": seeded, "table_name": "customers"})
        assert "city" in described

        text = await mcp.call_tool(
            "run_sql", {"database_id": seeded, "sql": "SELECT name FROM customers ORDER BY id"}
        )
        assert json.dumps([["Ann"], ["Bob"], ["Cy"]]) in text
        result = mcp.last_run_sql_result
        assert (result["columns"], result["rows"]) == (["name"], [["Ann"], ["Bob"], ["Cy"]])


async def test_other_users_get_permission_errors(seeded, make_user, mcp_client_for):
    stranger = await make_user()
    async with mcp_client_for(stranger) as mcp:
        with pytest.raises(ToolException, match="Tool call failed"):
            await mcp.call_tool("run_sql", {"database_id": seeded, "sql": "SELECT 1"})
        assert seeded not in await mcp.call_tool("list_databases", {})


async def test_invalid_arguments_are_tool_errors(owner, seeded, mcp_client_for):
    async with mcp_client_for(owner) as mcp:
        with pytest.raises(ToolException, match="INVALID_REQUEST"):
            await mcp.call_tool("describe_table", {"database_id": seeded, "table_name": "a; drop"})
        with pytest.raises(ToolException):
            await mcp.call_tool("sample_rows", {"database_id": seeded, "table_name": "customers", "limit": 1000})


async def test_writes_are_never_executed_over_mcp(client, owner, seeded, mcp_client_for):
    sql = "DELETE FROM customers"
    async with mcp_client_for(owner) as mcp:
        for args in ({"database_id": seeded, "sql": sql}, {"database_id": seeded, "sql": sql, "confirmed": True}):
            with pytest.raises(ToolException, match="^CONFIRMATION_REQUIRED"):
                await mcp.call_tool("run_sql", args)

    r = await client.post(
        f"/databases/{seeded}/export/preview",
        json={"sql": "SELECT count(*) FROM customers", "format": "csv"},
        headers=owner.headers,
    )
    assert r.json()["rows"] == [[3]]


async def test_ask_reports_unreachable_tool_server(client, app, owner, seeded, fake_agent, monkeypatch):
    monkeypatch.setattr(app.state, "mcp_transport", None)
    monkeypatch.setattr(get_settings(), "mcp_server_url", "http://127.0.0.1:9/mcp")
    fake_agent(script=[], answer="unused")

    r = await client.post(f"/databases/{seeded}/ask", json={"query": "x"}, headers=owner.headers)
    assert r.status_code == 503, r.text
    assert "tool server" in r.json()["detail"]


async def test_ask_stream_runs_tools_over_mcp(client, owner, seeded, fake_agent):
    fake_agent(
        script=[("run_sql", {"database_id": seeded, "sql": "SELECT count(*) AS n FROM customers"})],
        answer="There are 3.",
    )
    r = await client.post(f"/databases/{seeded}/ask/stream", json={"query": "how many?"}, headers=owner.headers)
    assert r.status_code == 200, r.text

    events = [
        (block.split("\n")[0].removeprefix("event: "), json.loads(block.split("\n")[1].removeprefix("data: ")))
        for block in r.text.strip().split("\n\n")
    ]
    assert [name for name, _ in events] == ["tool_start", "tool_end", "done"]
    done = events[-1][1]
    assert done["answer"] == "There are 3."
    assert (done["result"]["columns"], done["result"]["rows"]) == (["n"], [[3]])


async def test_agent_errors_inside_the_session_are_classified(client, owner, seeded, fake_agent, monkeypatch):
    from tests.conftest import FakeExecutor

    async def rate_limited(self, inputs, config=None):
        await self.mcp_client.call_tool("list_schemas", {"database_id": seeded})
        raise RuntimeError("429 RESOURCE_EXHAUSTED")

    monkeypatch.setattr(FakeExecutor, "ainvoke", rate_limited)
    fake_agent(script=[], answer="unused")

    r = await client.post(f"/databases/{seeded}/ask", json={"query": "x"}, headers=owner.headers)
    assert r.status_code == 503, r.text
    assert "rate-limited" in r.json()["detail"]
