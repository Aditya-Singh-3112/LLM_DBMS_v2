# LLM-Powered DBMS — Agentic Design Document & Implementation Plan

**Version:** 5.0
**Author:** Aditya Singh
**Status:** Design finalized — ready for implementation
**Change from v4.0:** Agent loop is no longer hand-rolled — it's built on **LangChain**, using **Google Generative AI (Gemini)** as the tool-calling LLM instead of Ollama. Added a **RAG layer**: a SQL/DBMS textbook is chunked, embedded, and stored in Postgres (`pgvector`), and the agent can retrieve grounded reference passages before writing SQL, instead of relying purely on the LLM's parametric knowledge of SQL syntax and semantics.

---

## 1. Overview

A full-stack application that lets users create their own Postgres-backed databases and query/modify them using natural language. The system exposes the database through the **Model Context Protocol (MCP)**: an MCP server wraps the database as a set of tools (schema inspection, sampling, SQL execution), and the agent that decides which tools to call is now a **LangChain tool-calling agent** running `Gemini` (via `langchain-google-genai`) as its LLM, rather than a hand-written loop against Ollama.

**Why LangChain instead of a hand-rolled loop:** v4.0's `AgentLoop` reimplemented tool-call orchestration, retry-on-malformed-call handling, and conversation-state management from scratch. LangChain's `AgentExecutor` already provides iteration capping, intermediate-step logging, and a stable interface over tool-calling models — reimplementing it added surface area without adding to what the project demonstrates. The parts worth building custom stay custom: the **MCP tool adapter** (there's no first-party LangChain↔MCP bridge assumed here — tool schemas are converted by hand into LangChain `StructuredTool`s) and the **RAG grounding layer**.

**Why RAG:** Gemini's parametric knowledge of SQL is generally solid, but it still hallucinates dialect-specific syntax, misapplies normalization/join semantics on ambiguous schemas, and can't be corrected without evidence. Grounding the agent against a real SQL/DBMS textbook — retrieved just-in-time via a `sql_reference_lookup` tool — gives the agent citable, retrievable passages to reason from before it commits to a query, and gives the system a way to show *why* a query was shaped the way it was, not just that it ran.

MongoDB still never stores queryable user data — it tracks *who owns what, who can access what, and what the agent did*. All actual CRUD happens against per-tenant Postgres schemas. The textbook's embeddings also live in Postgres (via `pgvector`), in a schema separate from any tenant's data, so the project doesn't need a second vector-store dependency.

---

## 2. Requirements

### Functional
1. Full CRUD via natural language, mediated by a LangChain agent that can inspect schema, retrieve reference material, and retry
2. Database exposed as MCP tools (schema inspection, sampling, SQL execution) behind an MCP server
3. LangChain `AgentExecutor` running Gemini as the tool-calling LLM, with MCP-derived tools plus a RAG retrieval tool
4. RAG layer: a SQL/DBMS textbook chunked, embedded (Google embedding model), and retrievable by the agent before it generates or validates SQL
5. ACID-compliant data operations
6. User authentication (JWT-based), carried over MCP transport
7. Role- and permission-based access control per database/table, enforced both in the MCP server and at the DB layer (RLS)
8. Users can create, list, and delete their own databases (Postgres schemas)
9. Users can share database access with other users at a defined access level
10. Full audit log per user: every tool call the agent made (including retrieval calls), not just the final SQL

### Non-functional
1. Support 100+ concurrent users
2. Sub-second response for cached/simple queries
3. Metadata store: MongoDB. Data store: **Postgres**, one schema per user database, RLS-enforced. Reference-material store: Postgres `pgvector` (separate schema, read-only at runtime)
4. Rate-limited LLM calls and tool calls per user
5. Redis cache for repeated tool-call results (schema lookups, SQL translations, and retrieval results)
6. Agent loop bounded via `AgentExecutor(max_iterations=...)` — same cap philosophy as v4.0, now enforced by LangChain
7. MCP transport must work over the network (not just local stdio), since the client and server are separate deployable services
8. RAG ingestion is a one-time/offline job, not something that runs per-request — the vector table is read-only from the agent's perspective

---

## 3. Architecture

```
Frontend (React)
   |
   v
FastAPI Backend  --------------------------------------------+
   |                                                          |
   |--> MongoDB   (users, permissions,                        |
   |               database registry,                         |
   |               tool_call_logs,                             |
   |               refresh_tokens)                              |
   |                                                          |
   |--> LangChain AgentExecutor (custom-assembled)              |
          |                                                    |
          |  1. list_tools() from MCP server -> MCPToolAdapter  |
          |     converts each to a LangChain StructuredTool     |
          |  2. sql_reference_lookup tool registered alongside   |
          |     the MCP-derived tools (RAGService-backed)        |
          |  3. AgentExecutor(llm=Gemini, tools=[...],           |
          |     max_iterations=6) runs the loop:                 |
          |       NL query -> Gemini -> tool_use -> tool result  |
          |       -> Gemini -> ... -> final answer                |
          |                                                    |
          v                                                    |
   MCP Server (custom-built, Streamable HTTP)                   |
          |                                                    |
          |--> SQLValidator   (parse + guard SQL)                |
          |--> PermissionService (Mongo lookup) ------------------+
          |--> Postgres  (schema-per-tenant, RLS)
          |--> Postgres  (pgvector schema, textbook embeddings — read path only)
          |--> CacheService (Redis)
          |--> RateLimiter (Redis)

   Gemini (Google Generative AI, tool-calling) <--- called by AgentExecutor via langchain-google-genai
   GoogleGenerativeAIEmbeddings <--- called by RAGService (ingestion) and sql_reference_lookup (query time)
```

The `sql_reference_lookup` tool is deliberately **not** an MCP tool: MCP is scoped to "the tenant's database," and the textbook has nothing to do with any tenant's schema or permissions. It's a plain LangChain tool backed by `RAGService`, registered into the same `AgentExecutor` as the MCP-derived tools so the agent can reach for it mid-loop.

---

## 4. Data Models

### 4.1 MongoDB collections

Unchanged from v4.0: `users`, `databases`, `permissions`, `tool_call_logs`, `refresh_tokens`. One addition to `tool_call_logs`:

```
tool_call_logs
  ...
  toolCalls: [
    { toolName: str, args: obj, result: obj | null,
      status: enum[success, denied, error], durationMs: int, ts: datetime,
      source: enum[mcp, rag] }        # NEW — distinguishes DB tool calls from retrieval calls
  ]
```

### 4.2 Postgres — tenant schemas (unchanged)

- One schema per database: `tenant_<databaseId>`
- RLS enforced as in v4.0, `app.current_user_id` session variable, identifier sanitization, etc. — no changes here.

### 4.3 Postgres — `rag` schema (new)

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE SCHEMA rag;

CREATE TABLE rag.textbook_chunks (
  id            SERIAL PRIMARY KEY,
  source        TEXT NOT NULL,        -- e.g. "Database System Concepts, ch.4"
  chapter       TEXT,
  page_start    INT,
  page_end      INT,
  content       TEXT NOT NULL,
  embedding     VECTOR(768),          -- dimension matches the Google embedding model used
  created_at    TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX ON rag.textbook_chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
```

- `rag` schema has no RLS — it's global reference data, not tenant data, and is read-only at request time
- Populated once via an offline ingestion script (§8), not touched by the request-serving path except for `SELECT ... ORDER BY embedding <=> $1 LIMIT k`
- Kept in a separate schema (not a separate database) so it shares the connection pool but is trivially excludable from any tenant-scoped query by construction

---

## 5. Auth Design

Unchanged from v4.0 — bcrypt/argon2 password hashing, JWT payload with no role claims, 15–30 min access token / 7 day refresh token, Bearer JWT on MCP transport, per-tool-call `PermissionService` re-check.

---

## 6. MCP Tool Contracts

Unchanged from v4.0: `list_schemas`, `describe_table`, `sample_rows`, `run_sql`, `list_databases`. Same input/output/error shapes and the same server-side `run_sql` handler flow (permission check → cache check → `SQLValidator` → access-level check → RLS-guarded execution → cache invalidation → log append).

**Tool call cadence, updated:** `list_databases` (if unknown) → `list_schemas` → `describe_table` → optionally `sql_reference_lookup` (when the request involves ambiguous SQL semantics — e.g. multi-table joins, window functions, normalization questions) → optionally `sample_rows` → `run_sql`. The `AgentExecutor` iteration cap stays at **6**, now enforced by LangChain rather than a hand-rolled loop counter; hitting it surfaces as `finalStatus: max_iterations_exceeded`, same contract as v4.0.

### 6.6 `sql_reference_lookup` (new — LangChain tool, not MCP)
```
Input:  { query: str, k: int (default 4, max 10) }
Output: { passages: [{ source, chapter, content, similarityScore }] }
```
Handler flow:
1. Embed `query` with `GoogleGenerativeAIEmbeddings`
2. `SELECT source, chapter, content, 1 - (embedding <=> $1) AS score FROM rag.textbook_chunks ORDER BY embedding <=> $1 LIMIT k`
3. Check Redis cache on `(normalizedQuery, k)` first — reference lookups are stable and cacheable, TTL longer than tool-call cache (reference material doesn't change)
4. Log the call to the current request's `tool_call_logs` entry with `source: "rag"`

---

## 7. Remaining REST API

Unchanged from v4.0 (§7.1–§7.11): auth endpoints, database CRUD, sharing, `POST /databases/{id}/ask`, download, standard error shape. `POST /databases/{id}/ask`'s response gains one field:

```
200: { answer: str, sql: str | null, result: { columns, rows } | null,
       toolCalls: [{ toolName, args, status, source }],   # source: mcp | rag
       groundedOn: [{ source, chapter }] | null,           # NEW — textbook passages actually cited in the final answer, if any
       totalExecutionTimeMs: int }
```

---

## 8. Agent Assembly & RAG Ingestion (replaces v4.0 §8 "MCP Client — Agent Loop")

### 8.1 Agent assembly (LangChain)

```
1. On backend startup:
   a. Connect to MCP server, call list_tools(), cache tool schemas
   b. MCPToolAdapter converts each MCP tool schema into a LangChain StructuredTool
      (Pydantic input schema derived from the MCP tool's input contract;
       the tool's `_run`/`_arun` calls the MCP client's call_tool() under the hood)
   c. Instantiate sql_reference_lookup as a plain LangChain StructuredTool backed by RAGService
   d. llm = ChatGoogleGenerativeAI(model="gemini-<tool-calling-capable version>", temperature=0)
   e. agent = create_tool_calling_agent(llm, tools=[mcp_tools..., sql_reference_lookup], prompt)
   f. executor = AgentExecutor(agent=agent, tools=tools, max_iterations=6, return_intermediate_steps=True)

2. On POST /databases/{id}/ask:
   a. Build the system prompt: available tools + schemas + target databaseId + an explicit instruction
      to consult sql_reference_lookup before writing SQL involving joins, subqueries, window functions,
      or ambiguous normalization — not for every trivial single-table SELECT
   b. executor.invoke({"input": nlQuery, ...}) — LangChain owns the tool-call/result loop internally
   c. Walk `intermediate_steps` after the run completes to build the tool_call_logs entry
      (each step already carries the tool name, args, and result — no manual bookkeeping needed)
   d. If the executor hits max_iterations -> map LangChain's stop reason to
      finalStatus: max_iterations_exceeded, same 422 contract as v4.0
   e. Extract any sql_reference_lookup results actually used in the final answer into groundedOn
```

**Model constraint to plan around:** Gemini's function-calling support via `langchain-google-genai` is reliable for well-typed Pydantic tool schemas, but nested/optional fields in a `StructuredTool`'s args schema occasionally get flattened oddly — keep MCP tool input schemas as flat as practical when writing the `MCPToolAdapter`, and add a schema-validation unit test per adapted tool rather than trusting the conversion silently. Retry-on-malformed-call is now handled by LangChain's agent executor rather than a hand-written retry path, but it's still worth capping retries explicitly (`max_execution_time` / `max_iterations`) so a bad adapter doesn't burn the whole iteration budget on one tool.

### 8.2 RAG ingestion (offline script, not part of the request path)

```
1. Load the SQL/DBMS textbook (PDF/text)
2. Chunk by section/paragraph (not fixed-size only — keep chapter/section boundaries where possible,
   since a chunk that spans two unrelated topics retrieves poorly)
3. Embed each chunk with GoogleGenerativeAIEmbeddings
4. Insert into rag.textbook_chunks (source, chapter, page_start, page_end, content, embedding)
5. Build/verify the ivfflat index once ingestion is complete
6. Spot-check retrieval quality with a handful of known SQL questions before wiring it into the agent
```

This runs once (and again on textbook updates) — it is explicitly not triggered by user requests, so it doesn't affect the sub-second cached-query latency target.

---

## 9. Caching & Rate Limiting (Redis)

- Same as v4.0 for MCP tool results (`hash(databaseId + toolName + normalizedArgs)`, TTL 1h, invalidated on mutation)
- New: `sql_reference_lookup` results cached on `hash(normalizedQuery + k)`, longer TTL (e.g. 24h) since the textbook doesn't change between deploys
- Rate limiting unchanged — `ratelimit:{userId}` sliding window still covers total tool calls per minute, and retrieval calls count against the same budget as DB tool calls (an agent that leans too heavily on `sql_reference_lookup` should still be rate-limited)

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
| `MCPServer` | expose DB tools, route calls to the right service | run the agent loop (that's LangChain's/the executor's job) |
| `MCPToolAdapter` | convert MCP tool schemas into LangChain `StructuredTool`s | implement any tool logic itself — it only forwards to `call_tool()` |
| `RAGService` | chunk retrieval via `pgvector` similarity search, embedding calls | tool-call orchestration, SQL execution |
| `AgentExecutor` (LangChain) | coordinate tool-call iterations with Gemini | implement any tool itself |
| `CacheService` | Redis get/set/invalidate | knowing what's being cached |
| `RateLimiter` | sliding window check per user | anything about queries or auth |

**Open/Closed:** LLM choice is isolated behind `ChatGoogleGenerativeAI` — swapping providers later means changing the `llm` passed into `create_tool_calling_agent`, not the agent-assembly logic. New MCP tools are added by extending `MCPToolAdapter`'s coverage, not by touching the executor. New retrieval sources (a second textbook, a schema-design guide) are added as more rows in `rag.textbook_chunks` with a `source` tag, not new tables or new tools.

**Liskov Substitution:** Any `LLMProvider`-shaped object handed to `create_tool_calling_agent` must accept LangChain's standard `(messages, tools)` calling convention and return either a text answer or a tool-call request.

**Interface Segregation:** `PermissionChecker` exposes only `hasAccess(userId, databaseId, requiredLevel) -> bool`; grant/revoke live on a separate `PermissionAdmin` interface. `RAGService` exposes only `retrieve(query, k) -> list[Chunk]` to the tool layer — ingestion (`ingest(source, chunks)`) lives on a separate interface the request path never touches.

**Dependency Inversion:** `MCPServer`'s tool handlers depend on `PermissionChecker`, `PostgresManager`, `CacheService` interfaces via FastAPI `Depends()`. The agent-assembly code depends on an `LLMProvider` abstraction and a `RAGService` abstraction, not concrete Gemini/pgvector calls, so unit tests can inject a fake LLM and a fake retriever to test the executor wiring without hitting Google's API or Postgres.

---

## 11. Implementation Plan

### Phase 0 — Project scaffolding (Day 1)
- [ ] FastAPI project structure: `app/{api,mcp_server,agent,rag,services,repositories,models,core}`
- [ ] MongoDB (Motor) + Redis connections
- [ ] Postgres connection pool + schema-per-tenant convention + `rag` schema with `pgvector` extension enabled
- [ ] `.env` config: Mongo URI, Postgres URI, Redis URI, JWT secret, Google API key
- [ ] Define Pydantic models from §4 and §6/§7

### Phase 1 — Auth (Days 2–3)
Unchanged from v4.0.

### Phase 2 — Database & permission management (Days 4–5)
Unchanged from v4.0.

### Phase 3 — MCP server (Days 6–8)
Unchanged from v4.0 — `list_schemas`, `describe_table`, `sample_rows`, `run_sql`, `SQLValidator`, Bearer-JWT session validation.

### Phase 4 — RAG ingestion pipeline (Days 9–10) — new phase
- [ ] Ingestion script: load textbook, chunk by section, embed with `GoogleGenerativeAIEmbeddings`, write to `rag.textbook_chunks`
- [ ] Build `ivfflat` index, tune `lists` parameter against chunk count
- [ ] `RAGService.retrieve(query, k)` — embed query, similarity search, return passages
- [ ] Manual retrieval quality check against a held-out set of SQL questions before wiring into the agent
- [ ] Tests: retrieval returns expected chapter for a handful of known queries, empty/garbage query doesn't crash the similarity search

### Phase 5 — LangChain agent assembly (Days 11–13)
- [ ] `MCPToolAdapter` — MCP tool schema → `StructuredTool`, with a schema-validation test per tool
- [ ] `sql_reference_lookup` `StructuredTool` wired to `RAGService`
- [ ] `ChatGoogleGenerativeAI` LLM instance + system prompt (tool list, target databaseId, when to consult the textbook)
- [ ] `create_tool_calling_agent` + `AgentExecutor(max_iterations=6, return_intermediate_steps=True)`
- [ ] `POST /databases/{id}/ask` — invoke executor, walk `intermediate_steps` into `tool_call_logs`, populate `groundedOn`
- [ ] Tests: executor terminates on final answer, terminates on iteration cap with correct error code, `tool_call_logs` entry correctly tags `source: mcp` vs `source: rag` per step

### Phase 6 — Caching & rate limiting (Day 14)
- [ ] `CacheService` keyed on `(databaseId, toolName, args)` for MCP tools, `(query, k)` for `sql_reference_lookup` with a longer TTL
- [ ] `RateLimiter` sliding window across the whole agent run, including retrieval calls
- [ ] Load test: cache-hit path (both MCP and RAG) stays sub-second

### Phase 7 — Export & download (Day 15)
Unchanged from v4.0 — `ExportStrategy`, CSV/XLSX, streamed download.

### Phase 8 — Frontend (Days 16–20)
- [ ] Auth pages, token storage + silent refresh
- [ ] Database list/create/delete UI
- [ ] Ask interface: NL input, live tool-call trace **now distinguishing DB tool calls from retrieval calls**, results table, and a small "grounded on" panel showing cited textbook passages when present
- [ ] Schema browser view
- [ ] Share-access modal, download button

### Phase 9 — Hardening & deployment (Days 21–23)
- [ ] Rate limit + agent-iteration-cap UX on frontend
- [ ] Structured logging correlating `tool_call_logs` entries with request IDs, tagged by `source`
- [ ] Mongo TTL index on `tool_call_logs.createdAt`
- [ ] Deploy: backend + MCP server to Render/Railway/Fly.io, Postgres with `pgvector` (Neon/Supabase — confirm `pgvector` extension is available on the chosen free tier before committing), Redis (Upstash), Google Generative AI via API key (no local model hosting needed — this drops the "Ollama needs headroom" deployment concern from v4.0 entirely)
- [ ] README with architecture diagram, tradeoffs section (RLS + app-layer permission checks as defense in depth; why MCP for DB tools but a plain LangChain tool for retrieval; why pgvector over a dedicated vector DB at this scale)

### Phase 10 — Polish for resume/portfolio (Day 24)
- [ ] Rerun the 89.6%/500-command NL→SQL benchmark against the LangChain + RAG agent — compare accuracy against the v4.0 hand-rolled loop, and specifically whether RAG-grounded answers reduce syntax-level errors on join/window-function-heavy queries
- [ ] New metric: retrieval-hit rate (fraction of requests where `sql_reference_lookup` was called) and whether grounded answers correlate with fewer `EXECUTION_ERROR`s on the first `run_sql` attempt
- [ ] Write up the MCP + LangChain + RAG split and SOLID structure in the README

---

## 12. Open Items to Revisit

- Whether `sql_reference_lookup` should be scoped by the target database's schema style (e.g. suppress passages about features the tenant's Postgres version doesn't support) — probably out of scope for portfolio scale, note as a limitation
- `ivfflat` index tuning (`lists` parameter) as the textbook corpus grows — fine at a single-textbook scale, would need revisiting with a larger corpus
- Whether MCP tool results and RAG passages should share one Redis cache namespace or stay separate — currently separate (different TTLs), revisit if cache memory becomes a constraint
- Whether to expose a `dry_run` mode on `run_sql` (validate + explain without executing), carried over from v4.0 as still-open
- Embedding-dimension lock-in: `VECTOR(768)` is tied to the specific Google embedding model chosen at ingestion time — re-ingestion is required if the embedding model changes, worth noting explicitly in the README rather than discovering it later