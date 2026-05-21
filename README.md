# The Contextual Maestro — Phase 1–5

Full-stack chat app: **Next.js** frontend (Bun), **FastAPI** backend, **PostgreSQL + pgvector**, **DeepSeek** streaming chat, **Phase 2** context compression, **Phase 3** cross-session memory and user facts, **Phase 4** RLS isolation, episodic memory management, Expert Mode prompt preview, rate limiting, and **Phase 5** selective active-turn retrieval (query-aware prompt packing under an 8K token budget).

## Prerequisites

- [Bun](https://bun.sh) for the frontend
- Python 3.11+ for the backend
- Docker (for PostgreSQL)

## Quick start (full stack with Docker)

```sh
cp .env.example .env
cp server/.env.example server/.env
# Edit .env and server/.env — JWT_SECRET, CORS_ORIGINS, NEXT_PUBLIC_API_URL, API keys

docker compose up --build
```

### Cloud / production (Docker Compose on a VM)

1. Copy `.env.example` → `.env` at the repo root and set:
   - `NEXT_PUBLIC_API_URL` — **public** API URL (e.g. `https://api.yourdomain.com`). This is embedded at **frontend image build** time; rebuild the frontend after changing it.
   - `CORS_ORIGINS` — UI origin(s), comma-separated (e.g. `https://app.yourdomain.com`).
   - `JWT_SECRET` — strong unique secret (not the default).
   - `DEEPSEEK_API_KEY`, `GEMINI_API_KEY` — required for chat and memory.
2. Optionally copy `server/.env.example` → `server/.env` for extra backend-only vars (`ADMIN_EMAIL`, rate limits, etc.).
3. Put a reverse proxy (Caddy, nginx, Traefik) in front of ports **3000** (UI) and **8000** (API) with TLS.
4. Run `docker compose up --build -d`. Backend `/health` checks Postgres connectivity.

There is no platform-specific IaC yet (Fly/Render/Vercel); the supported path is **Docker Compose** on a host with persistent volume `postgres-data`.

- API: [http://localhost:8000/health](http://localhost:8000/health)
- UI: [http://localhost:3000](http://localhost:3000)

The browser calls the API at `http://localhost:8000` via `NEXT_PUBLIC_API_URL` (set at frontend build time in compose).

## 1. Start PostgreSQL only (local dev)

```sh
docker compose up -d postgres
```

## 2. Backend

```sh
cd server
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env — set DEEPSEEK_API_KEY, GEMINI_API_KEY (for Phase 2 compression), JWT_SECRET
```

Phase 2 uses `tiktoken` for approximate prompt size, DeepSeek for summarization, and **Google Gemini** `gemini-embedding-001` with `outputDimensionality` matching `GEMINI_EMBEDDING_DIMENSIONS` (default `768`, aligned with the pgvector column).

```sh
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

On first run the app creates tables, enables the `vector` extension, and runs idempotent schema patches for new columns.

## 3. Frontend

```sh
cd client
cp .env.example .env.local
bun install
bun run dev
```

Open [http://localhost:3000](http://localhost:3000).

### User experience (default)

The product UI is a **simple chatbot**: sign in, pick or start a conversation, and send messages. Context engineering (compression, retrieval, fact extraction, RLS isolation) runs **automatically on the server** on every chat request—users are not asked to manage memory or facts in the main UI.

- **Sidebar:** New chat (creates a `chat_sessions` row), conversation list with title + preview, rename/delete.
- **Chat:** SSE streaming (`POST /api/chat`), Stop to abort (partial assistant text is discarded). No Sources panel or context widgets in the default UI.
- **Settings** (`/settings`): Hidden for normal users. **Expert** tab (prompt preview) when `expert_preview_enabled`; **Memory** inspector only with dev tools (below).

**Expert preview:** `POST /api/chat/preview` is gated by `users.expert_preview_enabled` or `role=admin`. Preview runs compression in **dry-run** mode and rolls back — it does not mutate stored episodes. Seed an admin via `ADMIN_EMAIL` / `ADMIN_PASSWORD` in `server/.env` (see `.env.example`).

**Token quotas (no billing):** Each registered user gets **1M tokens/day** on the primary tier (`DEEPSEEK_MODEL_TIER_PRIMARY`, default `deepseek-v4-flash`) and **1M/day** on the fallback tier (`DEEPSEEK_MODEL_TIER_FALLBACK`, default `deepseek-chat`) after the primary bucket is exhausted. Counts use **provider-reported** tokens from main chat SSE only. Limits reset at **UTC midnight**. Admins (`role=admin` or `token_unlimited`) are unlimited. Manage users at **`/admin`** (admin login only).

**Two-account setup:** Set `ADMIN_EMAIL` / `ADMIN_PASSWORD` in `server/.env` and restart the API for the admin account. Register a normal user via the app login screen (open registration).

**Developer tools:** set `NEXT_PUBLIC_DEV_TOOLS=1` in `client/.env.local`, or open the app with `?debug=1`, to show the context monitor, **Sources** on replies (facts, memories, selective-turn picks), memory settings, and session UUID in the header.

### Chat streaming (SSE)

`POST /api/chat` returns `text/event-stream` only (no plain-text stream). Events:

| Event | Payload |
|-------|---------|
| `compression_started` | `{ "session_id": "..." }` |
| `token` | `{ "text": "..." }` |
| `done` | `{ "assistant_message_id", "session_id", "model", "tier", "quota" }` |
| `error` | `{ "message": "...", "code": "..." }` |

Deploy client and server together when upgrading from pre-SSE builds.

Set `DEEPSEEK_MODEL` in `server/.env` to `deepseek-chat` (default) or `deepseek-reasoner` if your DeepSeek account supports it. Optional: `DEEPSEEK_SUMMARIZE_MODEL` for cheaper summarization.

Tune `CONTEXT_THRESHOLD_TOKENS` (default `4000`) and `MIN_RECENT_MESSAGES_TO_KEEP` (default `8`) for compression behavior.

### Phase 3 environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `RETRIEVAL_TOP_K` | `5` | Vector search candidates before rerank |
| `RETRIEVAL_FINAL_K` | `2` | Memories kept after DeepSeek rerank |
| `RETRIEVAL_MIN_SCORE` | `0.35` | Minimum cosine similarity (approx. `1 - distance`) |
| `RETRIEVAL_KEYWORD_TOP_K` | `5` | Keyword fallback candidates when embed fails |
| `FACT_INJECTION_MAX` | `8` | Max user facts injected per reply |
| `FACT_INJECTION_MIN_SIMILARITY` | `0.25` | Drop low-similarity facts (unless pinned) |
| `IN_SESSION_MEMORY_FINAL_K` | `1` | Older same-session memory chunks (excl. latest summary) |
| `MEMORY_ANN_INDEX` | `hnsw` | ANN index type: `hnsw`, `ivfflat`, or `none` |
| `FACT_EXTRACTION_SESSION_EVERY_N` | `4` | Session-scoped fact extraction every N messages in that session |
| `FACT_EXTRACTION_GLOBAL_EVERY_N` | `8` | User-global fact extraction every N user messages |
| `FACT_EXTRACTION_EVERY_N_MESSAGES` | `4` | Legacy alias for global schedule |
| `FACT_EXTRACTION_LOOKBACK_MESSAGES` | `12` | Recent turns fed to the fact extractor |
| `FACT_EXTRACTION_MEMORY_SESSIONS_CAP` | `5` | Max session memory summaries in global pass |
| `FACT_MAX_PER_USER` | `50` | Cap active facts; deprecate excess |
| `FACT_DEDUP_SIMILARITY_THRESHOLD` | `0.92` | Embedding merge threshold for dedup |
| `EMBEDDING_CACHE_TTL_SECONDS` | `604800` | Postgres embedding cache TTL |
| `RETRIEVAL_BUNDLE_CACHE_TTL_SECONDS` | `60` | Assembled retrieval cache TTL |
| `PROMPT_ASSEMBLY_CACHE_ENABLED` | `false` | Cache `build_completion_messages` output |
| `DEEPSEEK_RERANK_MODEL` | `deepseek-chat` | Model for reranking and fact JSON extraction |

See `docs/fact-extraction-ops.md` and `docs/caching-ops.md`.

Phase 3 injects `<user_profile>`, `<relevant_past_context>`, and optionally `<in_session_memory>` before the latest `Compressed context` block. Cross-session retrieval degrades to keyword search or surfaces **unavailable** in context status (never silent fail-open). `memory_paused` means compression failed; `retrieval_degraded` means cross-session recall is degraded. Attribution lists only facts/memories actually injected; see `docs/retrieval-ops.md` for ANN index tuning.

### Phase 5 environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `SELECTIVE_CONTEXT_ENABLED` | `true` | Query-aware active-turn packing (`false` = legacy: all active messages) |
| `PROMPT_TOKEN_BUDGET` | `8000` | Max tokens in assembled prompt per chat turn |
| `ACTIVE_RETRIEVAL_FLOOR_TURNS` | `6` | Recent turns always sent verbatim |
| `ACTIVE_RETRIEVAL_TOP_K` | `8` | Max retrieved turns (before neighbour expansion) |

See `docs/selective-context-ops.md` for scoring weights, chunking, troubleshooting, and attribution fields.

## API summary

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/auth/register` | Register |
| POST | `/api/auth/login` | Login (returns JWT) |
| GET | `/api/auth/me` | Current user + quota status (Bearer token) |
| GET | `/api/admin/users` | List all users and token usage (admin) |
| GET | `/api/admin/stats` | Platform token aggregates (admin) |
| PATCH | `/api/admin/users/{id}` | Override quotas / unlimited / expert flag (admin) |
| POST | `/api/chat` | Stream chat (Bearer token); runs context reduction when over threshold |
| GET | `/api/history/sessions` | List session IDs for sidebar |
| GET | `/api/history/messages` | Active (non-offloaded) messages for a session |
| GET | `/api/history/context` | Token load, offload counts, last summary (Phase 2) |
| GET | `/api/history/attribution` | Facts + memories used for an assistant message (Phase 3) |
| GET | `/api/memory/facts` | List user profile facts |
| POST | `/api/memory/facts` | Create a fact |
| PATCH | `/api/memory/facts/{id}` | Update a fact |
| DELETE | `/api/memory/facts/{id}` | Delete a fact |
| GET | `/api/memory/episodes` | List episodic memory chunks (paginated) |
| DELETE | `/api/memory/episodes/{id}` | Delete one memory chunk |
| POST | `/api/memory/clear` | Clear all facts + memory episodes (not chat messages); body `{ "confirm": "DELETE_ALL_MEMORY" }` |
| POST | `/api/chat/preview` | Expert mode: preview assembled prompt without persisting |

### Phase 4 environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `ENVIRONMENT` | `development` | Set to `production` to enable HSTS header |
| `CHAT_RATE_LIMIT` | `30/minute` | Rate limit for chat + preview |
| `AUTH_RATE_LIMIT` | `10/minute` | Rate limit for register/login |
| `MESSAGE_MAX_LENGTH` | `8000` | Max characters per chat message (schema) |

PostgreSQL **row-level security** is enabled on `users`, `user_facts`, and `episodes` with `FORCE ROW LEVEL SECURITY`. Each authenticated request sets `app.current_user_id` on the DB session; auth routes use a bypass flag for register/login.

## Tests

```sh
cd server
source .venv/bin/activate
pip install -r requirements.txt
# Requires Postgres running (docker compose up -d postgres)
pytest
```
