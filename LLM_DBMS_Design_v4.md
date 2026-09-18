# LLM-Powered DBMS — Agentic Design Document & Implementation Plan

**Version:** 4.0
**Author:** Aditya Singh
**Status:** Design finalized — ready for implementation
**Change from v3.0:** Pivoted from a direct NL→SQL translation service to an **agentic architecture**: a custom **MCP server** exposes the database as tools, and a custom **MCP client** runs the agent loop that decides which tools to call. Data store moved from per-user SQLite files to **Postgres** with schema-per-tenant + Row-Level Security.

---

## 1. Overview

A full-stack application that lets users create their own Postgres-backed databases and query/modify them using natural language. Instead of a single NL→SQL translation step, the system exposes the database through the **Model Context Protocol (MCP)**: an MCP server wraps the database as a set of tools (schema inspection, sampling, SQL execution), and a purpose-built MCP client runs an agent loop — powered by a tool-calling LLM (via Ollama) — that decides which tools to call, in what order, to satisfy the user's request.

**Why this design:** The v3.0 architecture treated NL→SQL as a single blind translation step: query in, SQL out, execute. That doesn't reflect how these systems actually behave well in production — a good agent inspects the schema before guessing at column names, samples data to disambiguate ambiguous requests, and retries when a generated query errors. MCP formalizes the boundary between "the agent that reasons" and "the tool surface it's allowed to touch," which is also the boundary every real permission and audit control has to sit on.

MongoDB still never stores queryable user data — it tracks *who owns what, who can access what, and what the agent did*. All actual CRUD happens against per-tenant Postgres schemas, so the system keeps MongoDB's flexible metadata modeling and gets Postgres's ACID guarantees plus Row-Level Security where correctness and isolation actually matter.

---

## 2. Requirements

### Functional
1. Full CRUD via natural language, mediated by an agent that can inspect schema and retry, not a single blind translation
2. Database exposed as MCP tools (schema inspection, sampling, SQL execution) behind an MCP server
3. Custom MCP client implementing the agent loop (tool-call → execute → feed result back → repeat until final answer)
4. ACID-compliant data operations
5. User authentication (JWT-based), carried over MCP transport
6. Role- and permission-based access control per database/table, enforced both in the MCP server and at the DB layer (RLS)
7. Users can create, list, and delete their own databases (Postgres schemas)
8. Users can share database access with other users at a defined access level
9. Full audit log per user: every tool call the agent made, not just the final SQL

### Non-functional
1. Support 100+ concurrent users
2. Sub-second response for cached/simple queries
3. Metadata store: MongoDB. Data store: **Postgres**, one schema per user database, RLS-enforced
4. Rate-limited LLM calls and tool calls per user
5. Redis cache for repeated tool-call results (schema lookups, SQL translations)
6. Agent loop bounded — max N tool-call iterations per request, to prevent runaway loops
7. MCP transport must work over the network (not just local stdio), since the client and server are separate deployable services

---

## 3. Architecture

```
Frontend (React)
   |
   v
FastAPI Backend  ------------------------------+
   |                                            |
   |--> MongoDB   (users, permissions,          |
   |               database registry,           |
   |               query_logs / tool_call_logs,  |
   |               refresh_tokens)               |
   |                                            |
   |--> MCP Client (custom-built)                |
          |                                      |
          |  1. list_tools()                     |
          |  2. NL query + tool schemas -> LLM    |
          |  3. LLM emits tool_use -> call_tool() |
          |  4. result fed back to LLM            |
          |  5. repeat until final answer         |
          |                                      |
          v                                      |
   MCP Server (custom-built, Streamable HTTP)     |
          |                                      |
          |--> SQLValidator   (parse + guard SQL) |
          |--> PermissionService (Mongo lookup) --+
          |--> Postgres  (schema-per-tenant, RLS)
          |--> CacheService (Redis)
          |--> RateLimiter (Redis)

   Ollama (local LLM, tool-calling enabled) <--- called by MCP Client
```

The MCP client is embedded in the FastAPI backend for v1 (simplest deploy — one process talks to Ollama and to the MCP server over HTTP). It's built as a standalone module so it could be extracted into its own service later without changing its interface.

---

## 4. Data Models

### 4.1 MongoDB collections

```
users
  _id: ObjectId
  email: str (unique, indexed)
  passwordHash: str
  createdAt: datetime
  updatedAt: datetime

databases
  _id: ObjectId
  ownerId: ObjectId (ref users._id, indexed)
  name: str
  pgSchemaName: str        # e.g. "tenant_<ownerId>_<slug>"
  createdAt: datetime

permissions
  _id: ObjectId
  userId: ObjectId (indexed)
  databaseId: ObjectId (indexed)
  accessLevel: enum[read, write, admin]
  grantedBy: ObjectId
  grantedAt: datetime
  # compound unique index on (userId, databaseId)

tool_call_logs                      # replaces query_logs
  _id: ObjectId
  userId: ObjectId (indexed)
  databaseId: ObjectId (indexed)
  requestId: str (indexed)          # groups all tool calls from one NL request
  naturalLanguageQuery: str
  toolCalls: [
    { toolName: str, args: obj, result: obj | null,
      status: enum[success, denied, error], durationMs: int, ts: datetime }
  ]
  finalStatus: enum[success, denied, error, max_iterations_exceeded]
  totalExecutionTimeMs: int
  createdAt: datetime (TTL index, e.g. 90 days)

refresh_tokens
  _id: ObjectId
  userId: ObjectId (indexed)
  tokenHash: str
  expiresAt: datetime
  revoked: bool
```

### 4.2 Postgres (per-user schema)

- One schema per database: `tenant_<databaseId>`
- `PGCurrentUser` session variable set per connection: `SET app.current_user_id = '<userId>'`
- **Row-Level Security** enabled on every table in the schema; policies reference `app.current_user_id` and a per-schema ownership/permission mirror table (kept in sync with Mongo `permissions` on grant/revoke) so the DB itself refuses cross-tenant access even if the MCP server's own check has a bug
- Identifiers sanitized (alphanumeric + underscore only, no reserved SQL keywords) before any generated DDL executes
- Implicit `id SERIAL PRIMARY KEY` if the LLM doesn't specify one
- No fixed schema — tables generated dynamically from natural language, same as v3.0

---

## 5. Auth Design

- Passwords hashed with bcrypt/argon2, never stored or returned in plaintext
- JWT payload: `{ sub: userId, email, iat, exp }` — no role/permission claims in the token; permissions are looked up fresh on every tool call since access is per-database and can change
- Access token TTL: 15–30 min; refresh token TTL: 7 days, stored hashed so it can be revoked
- **MCP transport auth:** MCP server runs on Streamable HTTP (not stdio — client and server are separate services). JWT passed as a `Bearer` header on every MCP request. Server validates it once per session and caches the resolved `userId` for the life of the connection; each individual tool call still re-checks `PermissionService` before touching Postgres, since a session can span multiple databases with different access levels.

---

## 6. MCP Tool Contracts

The MCP server exposes these tools. The agent (via the MCP client) decides which to call and in what sequence — this replaces the old single `POST /databases/{id}/query` blind-translation endpoint.

### 6.1 `list_schemas`
```
Input:  { databaseId: str }
Output: { tables: [str] }
Errors: PERMISSION_DENIED
```

### 6.2 `describe_table`
```
Input:  { databaseId: str, table: str }
Output: { columns: [{ name, type, nullable, primaryKey, foreignKey? }] }
Errors: PERMISSION_DENIED, TABLE_NOT_FOUND
```

### 6.3 `sample_rows`
```
Input:  { databaseId: str, table: str, limit: int (default 5, max 50) }
Output: { columns: [str], rows: [[...]] }
Errors: PERMISSION_DENIED, TABLE_NOT_FOUND
# read-only regardless of caller's accessLevel — sampling never requires 'write'
```

### 6.4 `run_sql`
```
Input:  { databaseId: str, sql: str }
Output: { columns: [str], rows: [[...]], rowsAffected: int, executionTimeMs: int }
Errors: PERMISSION_DENIED, SQL_VALIDATION_FAILED, EXECUTION_ERROR
```
Server-side handler flow:
1. Resolve `userId` from session; look up `permissions(userId, databaseId)` → deny if none
2. Check Redis cache for `(databaseId, normalizedSql)` if it's a pure SELECT
3. Parse `sql` with `SQLValidator` (sqlglot) — extract target table(s) + operation type; reject anything touching tables outside `databaseId`'s schema
4. Check operation type against `accessLevel` (read = SELECT only; write = CRUD; admin = +DDL)
5. `SET app.current_user_id` on the connection, execute against the tenant's Postgres schema (RLS as second guard)
6. Cache SELECTs in Redis; invalidate the databaseId's cache entries on any mutation
7. Append the call to the request's `tool_call_logs` entry

### 6.5 `list_databases` (metadata, not per-database)
```
Input:  {}
Output: { databases: [{ id, name, accessLevel }] }
```

Tool call cadence for a typical request: `list_databases` (if not already known) → `list_schemas` → `describe_table` (one or more, only if the schema isn't already in context) → optionally `sample_rows` on an ambiguous column → `run_sql`. The client caps this at **6 tool calls per request**; hitting the cap returns `finalStatus: max_iterations_exceeded` to the user rather than looping silently.

---

## 7. Remaining REST API (unchanged from v3.0)

Auth and database/permission management stay as plain REST — no reason to route account creation or sharing through the agent.

### 7.1 `POST /auth/register`
```
Request:  { email: str, password: str }
201:      { user: PublicUserRow { id, email, createdAt } }
409:      { code: "USER_EXISTS" }
```

### 7.2 `POST /auth/login`
```
Request:  { email: str, password: str }
200:      { user: PublicUserRow, accessToken: str, refreshToken: str, expiresAt: datetime }
401:      { code: "INVALID_CREDENTIALS" }
```

### 7.3 `POST /auth/refresh`
```
Request:  { refreshToken: str }
200:      { accessToken: str, expiresAt: datetime }
401:      { code: "INVALID_REFRESH_TOKEN" }
```

### 7.4 `POST /auth/logout`
```
Request:  { refreshToken: str }
204
```

### 7.5 `POST /databases`
```
Request (auth required): { name: str }
201: { database: { id, name, createdAt } }
# creates the Postgres schema + RLS policies, registers it, grants owner 'admin'
```

### 7.6 `GET /databases`
```
200: { databases: [{ id, name, accessLevel, createdAt }] }
```

### 7.7 `DELETE /databases/{id}`
```
204
403: { code: "FORBIDDEN" }   # only owner/admin
```

### 7.8 `POST /databases/{id}/share`
```
Request: { targetUserEmail: str, accessLevel: enum[read, write, admin] }
200: { permission: { userId, databaseId, accessLevel, grantedAt } }
403: { code: "FORBIDDEN" }
```

### 7.9 `POST /databases/{id}/ask`  (replaces the old `.../query`)
```
Request (auth required): { query: str }
# This is where the MCP client's agent loop is invoked server-side
200: { answer: str, sql: str | null, result: { columns, rows } | null,
       toolCalls: [{ toolName, args, status }], totalExecutionTimeMs: int }
403: { code: "FORBIDDEN" }
422: { code: "AGENT_MAX_ITERATIONS_EXCEEDED" }
```

### 7.10 `GET /databases/{id}/download?format=csv|xlsx`
```
200: Binary file stream
403: { code: "FORBIDDEN" }
```

### 7.11 Standard error shape (all endpoints)
```
{ code: str, message: str, statusCode: int }
```

---

## 8. MCP Client — Agent Loop

Custom-built module, not a wrapper around an existing MCP client library's CLI — this is the deliverable that demonstrates protocol-level understanding.

```
1. On backend startup: connect to MCP server, call list_tools(), cache tool schemas
2. On POST /databases/{id}/ask:
   a. Build system prompt: available tools + their schemas + the target databaseId
   b. Send user's NL query to Ollama (tool-calling enabled) as first turn
   c. Loop (max 6 iterations):
        - If LLM response is a final text answer -> break, return it
        - If LLM response is a tool_use block -> call_tool(name, args) against MCP server
        - Append tool result to conversation, send back to LLM for next turn
        - Log the call to this request's tool_call_logs entry
   d. If loop exceeds max iterations -> return 422 AGENT_MAX_ITERATIONS_EXCEEDED
   e. Write final tool_call_logs entry (finalStatus, totalExecutionTimeMs)
```

**Model constraint to plan around:** Ollama's tool-calling reliability varies a lot by model. Use Llama 3.1/3.3, Qwen2.5, or Mistral-Nemo — smaller models frequently emit malformed tool-call JSON. Build a retry-on-malformed-call path (re-prompt with the parse error, 1 retry max) rather than failing the whole request on the first bad tool call.

---

## 9. Caching & Rate Limiting (Redis)

- Cache key: `hash(databaseId + toolName + normalizedArgs)` → tool result, TTL 1h (was query-level in v3.0, now tool-call-level since schema/sample lookups are cacheable independent of the final SQL)
- Invalidate a `databaseId`'s cache entries on any successful mutation via `run_sql`
- Rate limit: `ratelimit:{userId}` sliding window on both LLM calls and total tool calls per minute — an agent loop can burn several tool calls per user request, so rate-limit the loop, not just the initial NL query

---

## 10. SOLID Principles Applied

| Module | Responsibility | NOT responsible for |
|---|---|---|
| `AuthService` | password hashing, JWT issue/verify | databases or permissions |
| `UserRepository` | CRUD on `users` collection | password hashing, token logic |
| `PermissionService` | check/grant access levels | SQL generation, execution |
| `DatabaseRegistryService` | create/list/delete database + schema records | touching Postgres connections directly |
| `PostgresManager` | open/execute/close per-tenant connections, set RLS session var | permission checks |
| `SQLValidator` | parse SQL, extract tables + operation type | execute the SQL |
| `MCPServer` | expose tools, route calls to the right service | run the agent loop (that's the client's job) |
| `MCPClient` / `AgentLoop` | coordinate tool-call iterations with the LLM | implement any tool itself — it only calls the server |
| `CacheService` | Redis get/set/invalidate | knowing what's being cached |
| `RateLimiter` | sliding window check per user | anything about queries or auth |

**Open/Closed:** `LLMProvider` interface with `OllamaProvider` implementation — swapping in a hosted tool-calling LLM later is a new class. New MCP tools (e.g. `explain_query`) are added without touching existing tool handlers.

**Liskov Substitution:** Any `LLMProvider` must accept `(messages, tools)` and return either a text answer or a tool-call request — never throw for a "low quality" answer, only for actual failures (timeout, unreachable).

**Interface Segregation:** `PermissionChecker` exposes only `hasAccess(userId, databaseId, requiredLevel) -> bool`; grant/revoke live on a separate `PermissionAdmin` interface the MCP server's tool handlers never touch.

**Dependency Inversion:** `MCPServer`'s tool handlers depend on `PermissionChecker`, `PostgresManager`, `CacheService` interfaces, injected via FastAPI `Depends()`. Unit tests inject a `FakeMCPServer` so the client's agent-loop logic can be tested without a real LLM or DB.

---

## 11. Implementation Plan

### Phase 0 — Project scaffolding (Day 1)
- [ ] FastAPI project structure: `app/{api,mcp_server,mcp_client,services,repositories,models,core}`
- [ ] MongoDB (Motor) + Redis connections
- [ ] Postgres connection pool + schema-per-tenant convention
- [ ] `.env` config: Mongo URI, Postgres URI, Redis URI, JWT secret, Ollama endpoint
- [ ] Define Pydantic models from §4 and §6/§7

### Phase 1 — Auth (Days 2–3)
- [ ] `UserRepository`, `AuthService`
- [ ] `POST /auth/register|login|refresh|logout`
- [ ] `refresh_tokens` collection + revocation
- [ ] Bearer-token dependency for protected routes
- [ ] Tests: dupes, wrong password, expired/revoked tokens

### Phase 2 — Database & permission management (Days 4–5)
- [ ] `DatabaseRegistryService` — create/list/delete + Postgres schema + RLS policy provisioning
- [ ] `PostgresManager` — schema creation, connection handling, session-var setting
- [ ] `PermissionService` — grant/check, keep Postgres RLS mirror table in sync with Mongo on every grant/revoke
- [ ] `POST /databases`, `GET /databases`, `DELETE /databases/{id}`, `POST /databases/{id}/share`
- [ ] Tests: owner-only delete, share grants correct level, RLS actually blocks a cross-tenant query at the DB layer (not just app layer)

### Phase 3 — MCP server (Days 6–8)
- [ ] Stand up MCP server on Streamable HTTP transport
- [ ] Implement `list_databases`, `list_schemas`, `describe_table`, `sample_rows`, `run_sql` tool handlers
- [ ] `SQLValidator` (sqlglot) wired into `run_sql`
- [ ] Bearer-JWT validation at the MCP session layer
- [ ] Tests: permission denial blocks execution before Postgres is touched, malformed SQL returns a structured error not a 500, cross-schema access attempt is rejected by both validator and RLS

### Phase 4 — MCP client / agent loop (Days 9–11)
- [ ] `LLMProvider` interface + `OllamaProvider` (tool-calling model)
- [ ] `AgentLoop`: system prompt construction, tool-call/result loop, 6-iteration cap, malformed-tool-call retry
- [ ] `POST /databases/{id}/ask`
- [ ] `tool_call_logs` write-through per iteration
- [ ] Tests: loop terminates on final answer, loop terminates on cap with correct error code, retry path recovers from one malformed tool call

### Phase 5 — Caching & rate limiting (Day 12)
- [ ] `CacheService` keyed on `(databaseId, toolName, args)`, wired into `run_sql` and read-only tools
- [ ] `RateLimiter` sliding window across the whole agent loop, not just the initial request
- [ ] Load test: cache-hit path stays sub-second even inside the loop

### Phase 6 — Export & download (Day 13)
- [ ] `ExportStrategy` interface, `CsvExportStrategy`, `XlsxExportStrategy`
- [ ] `GET /databases/{id}/download`
- [ ] Test: large table streams without loading fully into memory

### Phase 7 — Frontend (Days 14–18)
- [ ] Auth pages, token storage + silent refresh
- [ ] Database list/create/delete UI
- [ ] Ask interface: NL input, **live tool-call trace** (new — shows the agent's reasoning steps, not just final SQL), results table
- [ ] Schema browser view
- [ ] Share-access modal, download button

### Phase 8 — Hardening & deployment (Days 19–21)
- [ ] Rate limit + agent-loop-cap UX on frontend
- [ ] Structured logging correlating `tool_call_logs` entries with request IDs
- [ ] Mongo TTL index on `tool_call_logs.createdAt`
- [ ] Deploy: backend + MCP server to Render/Railway/Fly.io, Postgres (Neon/Supabase free tier), Redis (Upstash), Ollama on a host with enough RAM — flag this explicitly, since tool-calling models need more headroom than the original translation-only setup
- [ ] README with architecture diagram, tradeoffs section (RLS + app-layer permission checks as defense in depth, why MCP instead of a single translation endpoint)

### Phase 9 — Polish for resume/portfolio (Day 22)
- [ ] Rerun the 89.6%/500-command NL→SQL benchmark against the new agent loop — note whether schema-inspection tool calls change accuracy
- [ ] New metric: average tool calls per successful request (shows the agent isn't just guessing SQL blind)
- [ ] Write up the MCP server/client split and SOLID structure in the README

---

## 12. Open Items to Revisit

- Whether the MCP client should stay embedded in the FastAPI backend process or be extracted into its own service — start embedded, revisit if the agent loop's latency profile needs isolating from the request/response cycle
- RLS policy complexity as the number of shared databases per user grows — acceptable for portfolio scale, note as a tradeoff vs. a simpler app-layer-only check
- Ollama hosting for tool-calling models at the free-tier deploy — likely needs dedicated compute; decide before Phase 8
- Whether to expose a `dry_run` mode on `run_sql` (validate + explain without executing) — would help the agent self-correct before a real mutation, worth prototyping in Phase 4 if time allows