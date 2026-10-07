# LLM-Powered DBMS

Ask questions about your own Postgres data in plain English. A LangChain agent
(Gemini) inspects the schema, optionally looks up SQL concepts in an ingested
textbook (pgvector RAG), writes and runs the SQL, and explains the result. The
agent reaches the database only through tools served by a separate
**Model Context Protocol server**.

## Stack

- **API**: FastAPI, asyncpg, Motor (MongoDB), redis-py
- **Agent**: LangChain + `langchain-google-genai`; MCP client in `app/agent/`
- **Tools**: MCP server (official `mcp` SDK, Streamable HTTP) in `app/mcp_server/`
- **RAG**: `langchain-postgres` PGVector collection, `gemini-embedding-001` @ 768 dims
- **Frontend**: React (CRA), Zustand, Tailwind

## Features

- Natural-language questions answered by a tool-using agent, **streamed** to
  the browser (tool calls and tokens as they happen).
- **Conversations** saved per user and database: list, reopen, rename,
  delete. Follow-ups refine the previous question in the same conversation.
- **Data view**: browse each table's columns (primary keys marked) and rows,
  paginated and sortable, and a **SQL editor** for queries you write
  yourself, with **saved queries**.
- **Every write needs confirmation**, from the agent or the editor. It is
  first **dry-run** in a rolled-back transaction, so the confirmation shows
  how many rows it affects (and SQL errors surface before you're asked).
- **Undo** the latest confirmed change to a database (see below).
- **Schema changes** for owners: `ALTER TABLE`, `DROP TABLE|VIEW|INDEX`,
  `TRUNCATE`, all through the same confirmation.
- **Storage limit** per database (`DATABASE_MAX_MB`); writes and imports that
  would grow past it are rolled back, deletes always work.
- **Accounts**: email verification, password reset by email, password
  change, account deletion, and sign-in lockout after repeated failures.
- **Activity log** of every tool call against a database, for its owner.
- **Import** CSV/XLSX files as tables (type inference, preview, create/append/
  replace; up to 500 000 rows) and **export** any table or query result as CSV
  or XLSX.
- **Sharing** by email with read or write access; owners manage grants. New
  grants require the recipient to have verified their address.
- Textbook **RAG** grounding for SQL concepts.

## Running locally

```bash
cp .env.example .env            # set GOOGLE_API_KEY and JWT_SECRET
./start-dev.sh                  # docker compose (infra) + uvicorn + npm start
```

or by hand:

```bash
docker compose up -d --wait postgres mongodb redis
pip install -r requirements.txt
uvicorn app.mcp_server.main:app --reload --port 8001   # MCP server
uvicorn app.main:app --reload --port 8000              # API
cd frontend && npm install && npm start   # http://localhost:3000
```

### Whole stack in Docker

```bash
docker compose --profile app up --build   # web on :3000, api on :8000, mcp on :8001
```

The `web` image serves the React build through nginx and proxies `/api/*` to
the API, so the auth cookie is first-party.

### Ingesting reference material

```bash
python -m scripts.ingest_textbook \
  --source "Database System Concepts, 7ed" \
  --file path/to/textbook.md \
  --test-query "how does an outer join differ from an inner join"
```

Chunks are split on markdown headers; `#` becomes the `chapter` shown in the
UI's "Grounded on" panel.

## MCP server

`app/mcp_server` is a standalone process serving MCP over Streamable HTTP at
`/mcp`:

| Tool | What it does |
|---|---|
| `list_databases` | Databases the caller can access, with access level |
| `list_schemas` | Tables in a database |
| `describe_table` | Columns, types, nullability |
| `sample_rows` | Up to 100 rows from a table |
| `run_sql` | One validated statement; writes return `CONFIRMATION_REQUIRED` |
| `sql_reference_lookup` | Textbook passages (RAG) |

- **Auth**: every request needs `Authorization: Bearer <access token>`, the
  same JWT `/auth/login` issues. Without one the server answers 401 before
  any tool runs. Each tool call then rechecks the caller's access to the
  requested database, rate-limits it and writes it to `tool_call_logs`.
- **Stateless**: the server runs in stateless mode with JSON responses, so
  any replica can serve any request.
- **Results**: each result has readable text for the model plus
  `structuredContent` (rows, columns, passages). Failures are tool results
  with `isError: true` and `{"error": {"code", "message"}}`, with codes such
  as `PERMISSION_DENIED`, `NOT_FOUND`, `INVALID_REQUEST`, `RATE_LIMITED` and
  `CONFIRMATION_REQUIRED`.
- **Writes**: `run_sql` has no "confirmed" argument, so no MCP client can run
  a write. A write runs only when the user confirms it in the app, through
  REST `POST /databases/{id}/sql/confirm`.

For each `/ask`, the API opens an MCP session as the user
(`app/agent/mcp_client.py`), discovers the tools with `tools/list`, and gives
them to the agent with the schemas the server advertised. Its address is
`MCP_SERVER_URL`. If the server can't be reached, `/ask` returns 503.

Other MCP clients can use the same server. Point them at
`http://localhost:8001/mcp` with an access token from `/auth/login` in the
`Authorization` header.

## Tenant isolation

Each database registered through the API gets a Postgres schema
`tenant_<database_id>` **owned by a NOLOGIN role of the same name**. Every
user statement runs inside a transaction with `SET LOCAL ROLE tenant_<id>`,
so Postgres itself denies access to other tenants' schemas. The app's login
role is granted membership in each tenant role so it can switch into it.

In production the app must connect as a **non-superuser** role. Run
`scripts/bootstrap_db.sql` once as the superuser to create `llm_dbms_app`
(it has `CREATEROLE` and `CREATE` on the database, nothing more) and point
`POSTGRES_URI` at it. The API logs a warning at startup if it finds itself
running as a superuser outside `development`.

`app/services/sql_validator.py` additionally restricts statements to one of:

| Access | Allowed |
|---|---|
| read | `SELECT` |
| write | + `INSERT`, `UPDATE`, `DELETE`, `CREATE TABLE\|INDEX\|VIEW` |
| owner | + `ALTER TABLE`, `DROP TABLE\|VIEW\|INDEX`, `TRUNCATE` |

It rejects everything else (`GRANT`, `COPY`, `CREATE FUNCTION`, `DROP
SCHEMA`, `ALTER TABLE ... OWNER TO / SET SCHEMA`, session-changing functions,
...), and treats data-modifying CTEs as writes. Tables created through
`run_sql` land in the tenant schema because it is first on the `search_path`.

## Undo

Each confirmed write runs in one transaction that also:

1. drops the previous change's snapshots (only the latest change is undoable;
   an import also discards them),
2. copies what the write can destroy: the rows of the tables it modifies, or
   for `ALTER`/`DROP TABLE` the whole table (columns, constraints, indexes,
   identity and generated columns),
3. runs the statement, then records what it created, renamed or dropped,
   with the definitions of views and indexes to recreate.

`POST /databases/{id}/undo` replays that plan backwards. Snapshots live in the
tenant schema as `_undo_*` tables, hidden from table lists and excluded from
the storage limit. Undo is unavailable, and the confirmation says so, when the
affected tables exceed `UNDO_MAX_MB`, when other tables reference them by
foreign key, or when Postgres's transaction statistics show the write changed
a table the snapshot didn't cover (an updatable view, say). Restoring a dropped
or altered table restores its data, constraints and indexes, and recreates
plain column defaults and serial sequences; foreign keys *from other tables*
to it are the reason undo is refused in the first place.

## Authentication

- Short-lived JWT access token, returned by `/auth/login` and `/auth/refresh`
  and held **only in memory** by the frontend.
- Refresh token in an **HttpOnly cookie** (`Path=/auth`, `SameSite=Lax`,
  `Secure` in production), rotated on every refresh and revoked on logout.
- `/auth/refresh` and `/auth/logout` require the header
  `X-Requested-With: XMLHttpRequest`, so a cross-site form cannot trigger them
  with the victim's cookie.

`SameSite=Lax` means the frontend and API must share a site
(`app.example.com` + `api.example.com`, or one host with the nginx proxy).
For a genuinely cross-site deployment set `COOKIE_SAMESITE=none` and
`COOKIE_SECURE=true`.

## Operations

- Every response carries `X-Request-ID`; logs are JSON outside `development`
  and include it. Unhandled errors return `{"detail": "Internal server
  error", "request_id": ...}`, never a traceback.
- `/ask` is capped per user per day, by questions (`ASK_DAILY_LIMIT`) and
  optionally by model tokens (`ASK_DAILY_TOKEN_LIMIT`), and per request
  (`ASK_TIMEOUT_SECONDS`); tool calls are rate-limited at 60/min per user.
  Token use is recorded per user per day (`GET /auth/me/usage`, shown on the
  account page).
- **Health**: `GET /health` pings Postgres, Mongo, Redis and the MCP server;
  it answers 503 when Postgres or Mongo is down and `"degraded"` when only
  Redis or MCP is. `GET /health/live` never touches dependencies. The MCP
  server has its own `/health`.
- **Metrics**: Prometheus at `/metrics` on the API and the MCP server: request
  counts and latency by route template, questions by outcome, LLM tokens, and
  tool calls by tool and status. The nginx proxy refuses `/api/metrics`;
  scrape the services directly. `METRICS_ENABLED=false` turns it off.
- **Migrations**: `app/core/migrations.py` holds ordered data migrations,
  applied at API startup and recorded in Mongo's `schema_migrations`; a lock
  keeps replicas from running them twice. Index creation stays in each
  service's idempotent `initialize()`.
- **Email**: `EMAIL_BACKEND=smtp` with `SMTP_*` sends verification and reset
  emails; the default `console` logs them (with the links) instead.
  `FRONTEND_URL` is where the links point.
- **Sign-in lockout**: 5 failures per email (`LOGIN_MAX_FAILURES`) or 50 per
  client IP lock that email/IP for `LOGIN_LOCKOUT_MINUTES`. Behind a proxy,
  run uvicorn with `--proxy-headers` so the client IP is the real one.
- CI (`.github/workflows/ci.yml`) runs ruff, the backend tests against real
  Postgres/Mongo/Redis service containers, the frontend tests, and the
  frontend build. The LLM is never called in CI (`RAG_ENABLED=false`, agent
  stubbed).

## Tests

```bash
pip install pytest pytest-asyncio
pytest
```

`tests/test_api.py`, `tests/test_mcp_server.py` and `tests/test_auth_refresh.py`
need the docker-compose services and skip if they are unreachable; the rest
are pure unit tests. The API tests start the MCP server in-process, and the
API talks to it over real MCP (an `httpx` ASGI transport replaces the socket).
The agent is a scripted fake that calls tools through the MCP client. Emails
go to an in-memory outbox (`EMAIL_BACKEND=memory`), so the verification and
reset flows run end to end.

Frontend tests (Jest + React Testing Library):

```bash
cd frontend && npm test
```

### Agent evaluation

`scripts/eval_agent.py` measures how well the agent turns questions into SQL.
It runs against the live stack and the real model, so it costs tokens and
isn't part of CI:

```bash
python -m scripts.eval_agent --min-accuracy 0.8 --report eval.json
```

It seeds a throwaway database from `evals/data/*.csv`, asks each question in
`evals/nl2sql_cases.json` through `/ask`, and compares the returned rows with
those of the case's reference SQL (execution accuracy: equivalent SQL counts,
extra columns are allowed unless `--strict`). Add cases there when you change
the prompt or the tools.

## Environment variables

See `.env.example`. `JWT_SECRET` must be changed whenever `APP_ENV` is not
`development`; the app refuses to start otherwise. `CORS_ORIGINS` is a
comma-separated list of allowed browser origins.
