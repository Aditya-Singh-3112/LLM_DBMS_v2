"""
Email verification, password reset/change, login throttling, account
deletion, conversations, saved queries and the activity log.
"""
import asyncio
import uuid

import pytest

from tests.conftest import emailed_token

pytestmark = pytest.mark.asyncio

CSRF = {"X-Requested-With": "XMLHttpRequest"}


async def login(client, email, password="password123"):
    return await client.post("/auth/login", json={"email": email, "password": password})


# ------------------------------------------------------------ verification

async def test_email_verification_flow(client, make_user):
    user = await make_user(verified=False)
    me = (await client.get("/auth/me", headers=user.headers)).json()
    assert me["email_verified"] is False

    # Resend is rate-limited right after registration sent one.
    assert (await client.post("/auth/verify-email/resend", headers=user.headers)).status_code == 429

    token = emailed_token(user.email, "/verify-email")
    assert (await client.post("/auth/verify-email", json={"token": token})).status_code == 200
    assert (await client.get("/auth/me", headers=user.headers)).json()["email_verified"] is True
    # Tokens are single-use.
    assert (await client.post("/auth/verify-email", json={"token": token})).status_code == 400
    assert (await client.post("/auth/verify-email", json={"token": "bogus"})).status_code == 400


# ------------------------------------------------------------ passwords

async def test_password_reset_flow(client, make_user):
    from app.services.email_service import OUTBOX

    user = await make_user()
    sent_before = len(OUTBOX)
    r = await client.post("/auth/password/forgot", json={"email": f"nobody-{uuid.uuid4().hex}@example.com"})
    assert r.status_code == 202 and len(OUTBOX) == sent_before  # same answer, no email

    assert (await client.post("/auth/password/forgot", json={"email": user.email})).status_code == 202
    token = emailed_token(user.email, "/reset-password")

    await asyncio.sleep(1.1)  # token `iat` has one-second resolution
    r = await client.post("/auth/password/reset", json={"token": token, "new_password": "brand-new-pass"})
    assert r.status_code == 200

    assert (await login(client, user.email)).status_code == 401
    assert (await login(client, user.email, "brand-new-pass")).status_code == 200
    # Access tokens issued before the reset no longer work.
    assert (await client.get("/auth/me", headers=user.headers)).status_code == 401
    assert (await client.post("/auth/password/reset", json={"token": token, "new_password": "x" * 9})).status_code == 400


async def test_change_password_keeps_this_session_only(client, make_user):
    user = await make_user()
    r = await client.post(
        "/auth/password/change",
        json={"current_password": "wrong-one", "new_password": "another-pass"},
        headers=user.headers,
    )
    assert r.status_code == 400

    await asyncio.sleep(1.1)
    r = await client.post(
        "/auth/password/change",
        json={"current_password": "password123", "new_password": "another-pass"},
        headers=user.headers,
    )
    assert r.status_code == 200
    new_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert (await client.get("/auth/me", headers=new_headers)).status_code == 200
    assert (await client.get("/auth/me", headers=user.headers)).status_code == 401
    # The rotated cookie still refreshes.
    assert (await client.post("/auth/refresh", headers=CSRF)).status_code == 200


async def test_login_is_throttled_per_email(client, make_user):
    user = await make_user()
    for _ in range(5):
        assert (await login(client, user.email, "wrong-password")).status_code == 401
    r = await login(client, user.email)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0

    # Another account from the same client is unaffected.
    other = await make_user()
    assert (await login(client, other.email)).status_code == 200


# ------------------------------------------------------------ account deletion

async def test_delete_account_removes_owned_data_and_grants(client, make_user):
    leaving = await make_user()
    staying = await make_user()

    owned = (await client.post("/databases", json={"name": "mine"}, headers=leaving.headers)).json()["id"]
    await client.post(f"/databases/{owned}/share", json={"email": staying.email, "access_level": "read"}, headers=leaving.headers)
    theirs = (await client.post("/databases", json={"name": "theirs"}, headers=staying.headers)).json()["id"]
    await client.post(f"/databases/{theirs}/share", json={"email": leaving.email, "access_level": "write"}, headers=staying.headers)

    r = await client.request("DELETE", "/auth/me", json={"password": "nope"}, headers=leaving.headers)
    assert r.status_code == 400
    r = await client.request("DELETE", "/auth/me", json={"password": "password123"}, headers=leaving.headers)
    assert r.status_code == 204

    assert (await login(client, leaving.email)).status_code == 401
    assert (await client.get(f"/databases/{owned}/tables", headers=staying.headers)).status_code == 404
    assert (await client.get(f"/databases/{theirs}/permissions", headers=staying.headers)).json() == []
    await client.delete(f"/databases/{theirs}", headers=staying.headers)


# ------------------------------------------------------------ conversations

async def test_conversations_list_open_rename_delete(client, owner, make_user, seeded, fake_agent):
    fake_agent(script=[("run_sql", {"database_id": seeded, "sql": "SELECT count(*) FROM customers"})], answer="3")
    first = (await client.post(f"/databases/{seeded}/ask", json={"query": "How many   customers?"}, headers=owner.headers)).json()
    cid = first["conversation_id"]
    await client.post(f"/databases/{seeded}/ask", json={"query": "and now?", "conversation_id": cid}, headers=owner.headers)
    await client.post(f"/databases/{seeded}/ask", json={"query": "separate"}, headers=owner.headers)

    listed = (await client.get(f"/databases/{seeded}/conversations", headers=owner.headers)).json()
    assert [c["title"] for c in listed] == ["separate", "How many customers?"]
    assert listed[1]["message_count"] == 4

    convo = (await client.get(f"/databases/{seeded}/conversations/{cid}", headers=owner.headers)).json()
    assert [m["role"] for m in convo["messages"]] == ["human", "ai", "human", "ai"]
    assert convo["messages"][1]["sql"] == "SELECT count(*) FROM customers"

    r = await client.patch(f"/databases/{seeded}/conversations/{cid}", json={"title": "Counting"}, headers=owner.headers)
    assert r.status_code == 204

    # Conversations are private, even to people the database is shared with.
    reader = await make_user()
    await client.post(f"/databases/{seeded}/share", json={"email": reader.email, "access_level": "read"}, headers=owner.headers)
    assert (await client.get(f"/databases/{seeded}/conversations/{cid}", headers=reader.headers)).status_code == 404
    r = await client.post(f"/databases/{seeded}/ask", json={"query": "x", "conversation_id": cid}, headers=reader.headers)
    assert r.status_code == 404

    assert (await client.delete(f"/databases/{seeded}/conversations/{cid}", headers=owner.headers)).status_code == 204
    assert len((await client.get(f"/databases/{seeded}/conversations", headers=owner.headers)).json()) == 1


async def test_confirmed_write_is_recorded_in_its_conversation(client, owner, seeded, fake_agent):
    fake_agent(script=[], answer="ok")
    cid = (await client.post(f"/databases/{seeded}/ask", json={"query": "hi"}, headers=owner.headers)).json()["conversation_id"]
    r = await client.post(
        f"/databases/{seeded}/sql/confirm",
        json={"sql": "DELETE FROM customers WHERE id = 2", "conversation_id": cid},
        headers=owner.headers,
    )
    assert r.json()["rows_affected"] == 1
    messages = (await client.get(f"/databases/{seeded}/conversations/{cid}", headers=owner.headers)).json()["messages"]
    assert messages[-1]["content"] == "Executed (1 rows affected)."


# ------------------------------------------------------------ saved queries

async def test_saved_queries_are_per_user(client, owner, make_user, seeded):
    r = await client.post(
        f"/databases/{seeded}/saved-queries", json={"name": "Pune", "sql": "SELECT * FROM customers WHERE city = 'Pune'"},
        headers=owner.headers,
    )
    assert r.status_code == 201
    saved_id = r.json()["id"]

    reader = await make_user()
    await client.post(f"/databases/{seeded}/share", json={"email": reader.email, "access_level": "read"}, headers=owner.headers)
    assert (await client.get(f"/databases/{seeded}/saved-queries", headers=reader.headers)).json() == []
    assert (await client.delete(f"/databases/{seeded}/saved-queries/{saved_id}", headers=reader.headers)).status_code == 404

    listed = (await client.get(f"/databases/{seeded}/saved-queries", headers=owner.headers)).json()
    assert [q["name"] for q in listed] == ["Pune"]
    assert (await client.delete(f"/databases/{seeded}/saved-queries/{saved_id}", headers=owner.headers)).status_code == 204


# ------------------------------------------------------------ activity log

async def test_activity_log_is_owner_only_and_names_users(client, owner, make_user, seeded):
    writer = await make_user()
    await client.post(f"/databases/{seeded}/share", json={"email": writer.email, "access_level": "write"}, headers=owner.headers)
    await client.post(f"/databases/{seeded}/sql/confirm", json={"sql": "DELETE FROM customers WHERE id = 3"}, headers=writer.headers)
    await client.post(f"/databases/{seeded}/undo", headers=owner.headers)

    assert (await client.get(f"/databases/{seeded}/activity", headers=writer.headers)).status_code == 403

    entries = (await client.get(f"/databases/{seeded}/activity", params={"writes_only": True}, headers=owner.headers)).json()
    assert [(e["tool_name"], e["user_email"]) for e in entries[:2]] == [("undo", owner.email), ("run_sql", writer.email)]
    assert entries[1]["sql"] == "DELETE FROM customers WHERE id = 3" and entries[1]["status"] == "success"

    page = (await client.get(f"/databases/{seeded}/activity", params={"limit": 1}, headers=owner.headers)).json()
    older = (await client.get(
        f"/databases/{seeded}/activity", params={"limit": 1, "before": page[0]["timestamp"]}, headers=owner.headers
    )).json()
    assert older and older[0]["timestamp"] < page[0]["timestamp"]
