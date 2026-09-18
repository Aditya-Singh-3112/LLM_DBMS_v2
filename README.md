# LLM-Powered DBMS

Ask questions about your own Postgres data in plain English. A LangChain agent
(Gemini) inspects the schema, optionally looks up SQL concepts in an ingested
textbook (pgvector RAG), writes and runs the SQL, and explains the result.

## Stack

- **API**: FastAPI, asyncpg, Motor (MongoDB), redis-py
- **Agent**: LangChain + `langchain-google-genai`, tools defined in `app/agent/`
- **RAG**: `langchain-postgres` PGVector collection, `gemini-embedding-001` @ 768 dims
- **Frontend**: React (CRA), Zustand, Tailwind

## Features

- Natural-language questions answered by a tool-using agent, **streamed** to
  the browser (tool calls and tokens as they happen).
- **Conversation memory** per user and database, so follow-ups refine the
  previous question. "New conversation" clears it.
- **Every write needs confirmation**: the agent proposes `INSERT`/`UPDATE`/
  `DELETE`/`CREATE`, the UI shows the SQL with a "Run anyway" button, and only
  then is it executed.
- **Import** CSV/XLSX files as tables (type inference, preview, create/append/
  replace; up to 500 000 rows) and **export** any table or query result as CSV
  or XLSX.
- **Sharing** by email with read or write access; owners manage grants.
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
uvicorn app.main:app --reload --port 8000
cd frontend && npm install && npm start   # http://localhost:3000
```

### Whole stack in Docker

```bash
docker compose --profile app up --build   # web on :3000, api on :8000
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

`app/services/sql_validator.py` additionally restricts statements to a
single `SELECT` / `INSERT` / `UPDATE` / `DELETE` / `CREATE TABLE|INDEX|VIEW`
(everything but `SELECT` requires `write` or `owner` access), rejects
`DROP`/`ALTER`/`TRUNCATE` and session-changing functions, and treats
data-modifying CTEs as writes. Tables created through `run_sql` land in the
tenant schema because it is first on the `search_path`.

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
- `/ask` is capped per user per day (`ASK_DAILY_LIMIT`) and per request
  (`ASK_TIMEOUT_SECONDS`); tool calls are rate-limited at 60/min per user.
- CI (`.github/workflows/ci.yml`) runs ruff, the test suite against real
  Postgres/Mongo/Redis service containers, and the frontend build. The LLM is
  never called in tests (`RAG_ENABLED=false`, agent stubbed).

## Tests

```bash
pip install pytest pytest-asyncio
pytest
```

`tests/test_api.py` and `tests/test_auth_refresh.py` need the docker-compose
services and skip if they are unreachable; the rest are pure unit tests.

## Environment variables

See `.env.example`. `JWT_SECRET` must be changed whenever `APP_ENV` is not
`development`; the app refuses to start otherwise. `CORS_ORIGINS` is a
comma-separated list of allowed browser origins.
