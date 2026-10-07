import pytest

pytestmark = pytest.mark.asyncio


# ------------------------------------------------------------------ auth

async def test_login_sets_httponly_cookie_and_hides_refresh_token(client, make_user):
    user = await make_user()
    r = await client.post("/auth/login", json={"email": user.email, "password": "password123"})
    assert "refresh_token" not in r.json()
    cookie = r.headers["set-cookie"]
    assert "refresh_token=" in cookie and "HttpOnly" in cookie and "Path=/auth" in cookie


async def test_refresh_requires_csrf_header_and_rotates(client, make_user):
    await make_user()  # logs in, which sets the cookie on `client`
    assert (await client.post("/auth/refresh")).status_code == 403
    csrf = {"X-Requested-With": "XMLHttpRequest"}
    r1 = await client.post("/auth/refresh", headers=csrf)
    assert r1.status_code == 200 and "access_token" in r1.json()
    r2 = await client.post("/auth/logout", headers=csrf)
    assert r2.status_code == 200
    assert (await client.post("/auth/refresh", headers=csrf)).status_code == 401


# ------------------------------------------------------------------ tenancy

async def test_cross_tenant_access_is_denied(client, make_user, seeded):
    other = await make_user()
    r = await client.post("/databases", json={"name": "theirs"}, headers=other.headers)
    their_db = r.json()["id"]
    try:
        # by database id
        r = await client.get(f"/databases/{seeded}/tables", headers=other.headers)
        assert r.status_code == 403
        # by schema-qualified SQL under their own database
        r = await client.post(
            f"/databases/{their_db}/export/preview",
            json={"sql": f'SELECT * FROM "tenant_{seeded}".customers', "format": "csv"},
            headers=other.headers,
        )
        assert r.status_code == 400 and "permission denied" in r.text
    finally:
        await client.delete(f"/databases/{their_db}", headers=other.headers)


async def test_read_only_sharee_cannot_write_or_import(client, make_user, owner, seeded):
    reader = await make_user()
    r = await client.post(
        f"/databases/{seeded}/share", json={"email": reader.email, "access_level": "read"}, headers=owner.headers
    )
    assert r.status_code == 204

    r = await client.post(
        f"/databases/{seeded}/export/preview",
        json={"sql": "DELETE FROM customers", "format": "csv"},
        headers=reader.headers,
    )
    assert r.status_code == 400 and "Write operations" in r.text

    r = await client.post(
        f"/databases/{seeded}/tables/import",
        files={"file": ("x.csv", b"a\n1\n")},
        headers=reader.headers,
    )
    assert r.status_code == 403

    r = await client.post(
        f"/databases/{seeded}/export/preview",
        json={"sql": "SELECT count(*) FROM customers", "format": "csv"},
        headers=reader.headers,
    )
    assert r.status_code == 200 and r.json()["rows"] == [[3]]


async def test_permissions_list_and_revoke(client, make_user, owner, database):
    reader = await make_user()
    await client.post(
        f"/databases/{database}/share", json={"email": reader.email, "access_level": "write"}, headers=owner.headers
    )
    r = await client.get(f"/databases/{database}/permissions", headers=owner.headers)
    assert r.status_code == 200
    assert [(p["email"], p["access_level"]) for p in r.json()] == [(reader.email, "write")]

    assert (await client.get(f"/databases/{database}/permissions", headers=reader.headers)).status_code == 403

    r = await client.delete(f"/databases/{database}/permissions/{await reader.me()}", headers=owner.headers)
    assert r.status_code == 204
    assert (await client.get("/databases", headers=reader.headers)).json() == []


# ------------------------------------------------------------------ import / export

async def test_import_then_export_round_trip(client, owner, seeded):
    r = await client.get(f"/databases/{seeded}/tables", headers=owner.headers)
    assert r.json() == {"tables": ["customers"]}

    r = await client.get(f"/databases/{seeded}/tables/customers/export", params={"format": "csv"}, headers=owner.headers)
    assert r.status_code == 200
    assert r.text.splitlines() == ["id,name,city,orders", "1,Ann,Pune,3", "2,Bob,Delhi,0", "3,Cy,Pune,1"]

    r = await client.get(f"/databases/{seeded}/tables/customers/export", params={"format": "xlsx"}, headers=owner.headers)
    assert r.status_code == 200 and r.content[:2] == b"PK"

    # dry run does not write
    r = await client.post(
        f"/databases/{seeded}/tables/import",
        files={"file": ("more.csv", b"x\n1\n")},
        data={"dry_run": "true"},
        headers=owner.headers,
    )
    assert r.status_code == 200 and r.json()["columns"] == [{"name": "x", "type": "bigint", "source_header": "x"}]
    assert (await client.get(f"/databases/{seeded}/tables", headers=owner.headers)).json()["tables"] == ["customers"]

    # create conflict / append
    r = await client.post(
        f"/databases/{seeded}/tables/import",
        files={"file": ("customers.csv", b"id,name,city,orders\n4,Dee,Mumbai,5\n")},
        headers=owner.headers,
    )
    assert r.status_code == 409
    r = await client.post(
        f"/databases/{seeded}/tables/import",
        files={"file": ("customers.csv", b"id,name,city,orders\n4,Dee,Mumbai,5\n")},
        data={"mode": "append"},
        headers=owner.headers,
    )
    assert r.status_code == 200 and r.json()["row_count"] == 1


# ------------------------------------------------------------------ /ask

async def test_ask_runs_tools_and_returns_result(client, owner, seeded, fake_agent):
    fake_agent(
        script=[
            ("list_schemas", {"database_id": seeded}),
            ("run_sql", {"database_id": seeded, "sql": "SELECT name FROM customers WHERE city = 'Pune' ORDER BY id"}),
        ],
        answer="Two customers are in Pune.",
    )
    r = await client.post(f"/databases/{seeded}/ask", json={"query": "who is in Pune?"}, headers=owner.headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["answer"] == "Two customers are in Pune."
    assert body["sql"].startswith("SELECT name FROM customers")
    assert (body["result"]["columns"], body["result"]["rows"]) == (["name"], [["Ann"], ["Cy"]])
    assert [t["status"] for t in body["tool_calls"]] == ["success", "success"]
    assert body["pending_write"] is None


async def test_ask_write_requires_confirmation(client, owner, seeded, fake_agent):
    sql = "DELETE FROM customers WHERE name = 'Bob'"
    fake_agent(script=[("run_sql", {"database_id": seeded, "sql": sql})], answer="Needs confirmation.")

    r = await client.post(f"/databases/{seeded}/ask", json={"query": "delete bob"}, headers=owner.headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pending_write"] == {
        "sql": sql, "operation": "delete", "rows_affected": 1,
        "undo_available": True, "undo_unavailable_reason": None,
    }
    assert body["tool_calls"][0]["status"] == "pending"

    # nothing happened yet
    r = await client.post(
        f"/databases/{seeded}/export/preview", json={"sql": "SELECT count(*) FROM customers", "format": "csv"}, headers=owner.headers
    )
    assert r.json()["rows"] == [[3]]

    r = await client.post(f"/databases/{seeded}/sql/confirm", json={"sql": sql}, headers=owner.headers)
    assert r.status_code == 200, r.text
    r = await client.post(
        f"/databases/{seeded}/export/preview", json={"sql": "SELECT count(*) FROM customers", "format": "csv"}, headers=owner.headers
    )
    assert r.json()["rows"] == [[2]]


async def test_conversation_memory_follows_conversation_id(client, owner, seeded, fake_agent, monkeypatch):
    captured = {}

    from tests.conftest import FakeExecutor

    orig = FakeExecutor.ainvoke

    async def spy(self, inputs, config=None):
        captured["history"] = inputs.get("chat_history")
        return await orig(self, inputs, config)

    monkeypatch.setattr(FakeExecutor, "ainvoke", spy)
    fake_agent(script=[], answer="ok")

    r = await client.post(f"/databases/{seeded}/ask", json={"query": "first"}, headers=owner.headers)
    assert captured["history"] == []
    conversation_id = r.json()["conversation_id"]
    await client.post(
        f"/databases/{seeded}/ask", json={"query": "second", "conversation_id": conversation_id}, headers=owner.headers
    )
    assert [m.content for m in captured["history"]] == ["first", "ok"]

    # Omitting the id starts a new conversation.
    r = await client.post(f"/databases/{seeded}/ask", json={"query": "third"}, headers=owner.headers)
    assert captured["history"] == []
    assert r.json()["conversation_id"] != conversation_id


async def test_unhandled_errors_are_json_with_request_id(client, owner, seeded, fake_agent, monkeypatch):
    from app.api import ask as ask_module

    async def boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(ask_module, "_finish", boom)
    fake_agent(script=[], answer="ok")
    r = await client.post(f"/databases/{seeded}/ask", json={"query": "x"}, headers=owner.headers)
    assert r.status_code == 500, r.text
    assert r.json()["detail"] == "Internal server error", r.text
    assert r.json()["request_id"] == r.headers["x-request-id"]
