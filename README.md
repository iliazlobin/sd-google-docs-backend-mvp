# Google Docs MVP — Collaborative Text Editor Backend

[![CI](https://github.com/iliazlobin/sd-google-docs-backend/actions/workflows/ci.yml/badge.svg)](https://github.com/iliazlobin/sd-google-docs-backend/actions/workflows/ci.yml)

A collaborative real-time text-editing backend implementing the **Jupiter OT protocol** for concurrent document editing. Built with Python + FastAPI, PostgreSQL, and Redis.

## What it is

A WebSocket-backed REST API that lets multiple users edit the same document simultaneously. Uses **Operational Transformation (OT)** to merge concurrent edits without data loss. Tracks live cursor positions via Redis pub/sub. Designed as a single-server MVP — scales vertically for 100+ concurrent editors.

## Quick start

```bash
# Prerequisites: Docker 24+ with Compose V2

# 1. Start the stack
docker compose up -d --wait

# 2. Run migrations
docker compose run --rm app alembic upgrade head

# 3. Verify it's running
curl -sf http://localhost:8010/healthz
# → {"status":"ok"}

# 4. Create a document
curl -s -X POST http://localhost:8010/docs \
  -H "Content-Type: application/json" \
  -d '{"title":"Hello World"}'
# → {"id":"<uuid>","title":"Hello World","content":"","revision":0,...}

# 5. Teardown
docker compose down --volumes
```

No `.env` or Python installation needed on the host — the app runs entirely in containers.

## Architecture

```
┌──────────────┐      HTTP/WS      ┌─────────────────────────────────────┐
│  Client A     │──────────────────▶│          FastAPI (uvicorn)          │
│  (browser)    │                   │                                     │
│              │                   │  ┌──────────┐  ┌──────────────────┐ │
│              │  WS /edit  ◀──────│  │  Router   │  │  WSEditEndpoint  │ │
│              │                   │  │ (CRUD)    │  │  (OT ingest +    │ │
└──────────────┘                   │  └─────┬────┘  │   broadcast)     │ │
                                    │        │       └────────┬─────────┘ │
┌──────────────┐      WS/presence  │        │                 │           │
│  Client B     │──────────────────▶│        │         ┌──────▼────────┐  │
│  (browser)    │                   │        │         │  OTEngine     │  │
│              │                   │        │         │  (transform + │  │
│              │                   │        │         │   apply)      │  │
│              │                   │        │         └──┬────┬───────┘  │
└──────────────┘                   │        │            │    │          │
                                    │        │            │    │          │
                                    │  ┌─────▼─────┐ ┌────▼───▼───────┐  │
                                    │  │  Postgres │ │  Redis        │  │
                                    │  │  (ops +   │ │  (pub/sub     │  │
                                    │  │  docs)    │ │   cursors)    │  │
                                    │  └───────────┘ └────────────────┘  │
                                    └─────────────────────────────────────┘
```

**Edit flow:**
1. Client sends an insert/delete op over `WS /docs/{id}/edit`
2. OTEngine transforms the op against concurrent ops (same-base-revision conflict detection)
3. The transformed op is persisted as an `Operation` row and applied to `Document.content`
4. Transformed op is broadcast to all connected clients
5. Sender gets an `ack` with the assigned revision number

**Presence flow:**
1. Client sends cursor position over `WS /docs/{id}/presence`
2. Server stores in-memory cursor state + publishes to Redis `presence:{doc_id}` channel
3. All subscribed clients receive the full presence set
4. Stale cursors (>30s) are pruned silently

## API

### REST endpoints

| Method | Path | Status | Purpose |
|--------|------|--------|---------|
| `POST` | `/docs` | `201` | Create a new blank document |
| `GET` | `/docs/{id}` | `200` / `404` | Get document metadata + current content |
| `PATCH` | `/docs/{id}` | `200` / `404` | Rename a document |
| `DELETE` | `/docs/{id}` | `204` | Soft-delete (idempotent) |
| `GET` | `/healthz` | `200` | Liveness check |

### WebSocket — editing

**Endpoint:** `WS /docs/{id}/edit`

Send insert/delete operations. The server transforms against conflicting concurrent ops, persists, and broadcasts back to all clients.

```json
// Client sends:
{"type": "insert", "position": 0, "text": "Hello", "rev": 0, "user_id": "alice"}

// Server responds with ack + broadcasts operator to all
{"type": "ack", "revision": 1, ...}
{"type": "op", "revision": 2, "type": "insert", "position": 5, "text": "World", "user_id": "bob"}

// On stale revision (client behind the ring buffer):
{"type": "error", "code": "STALE_REVISION", "message": "...Reload the document."}
```

### WebSocket — presence

**Endpoint:** `WS /docs/{id}/presence`

Send cursor positions. Server broadcasts the full presence set via Redis pub/sub.

```json
// Client sends:
{"type": "cursor", "position": 42, "user_id": "alice", "user_name": "Alice"}

// Server broadcasts:
{"type": "presence", "cursors": {"alice": {"position": 42, "user_name": "Alice", "ts": 1719792000}}}
```

## Stack

| Component | Technology | Role |
|-----------|-----------|------|
| Runtime | Python 3.12 + FastAPI + uvicorn | Async HTTP/WS server |
| Database | PostgreSQL 16 | Document + operation storage |
| Cache/PubSub | Redis 7 | Cursor presence relay |
| ORM | SQLAlchemy 2.0 (async) + Alembic | Schema management |
| Testing | pytest + httpx + websockets | Black-box acceptance suite |
| Container | Docker + Compose V2 | Local dev / deployment |

## Tests

Two test suites live in the repo:

### White-box unit tests (`tests/`)

Import app modules and run in-process via `ASGITransport`. Run without Docker:

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

Currently verifies: `GET /healthz` returns 200.

### Black-box acceptance tests (`verify/acceptance/`)

Pure HTTP/WS tests that hit a running stack via `API_BASE_URL`. No app imports.

```bash
docker compose up -d --wait
docker compose run --rm app alembic upgrade head
API_BASE_URL=http://localhost:8010 pytest verify/acceptance/ -v
```

15 acceptance cases across 4 functional requirements:

| Suite | FR | Cases | What it verifies |
|-------|----|-------|-----------------|
| `test_fr1_crud.py` | FR1 — Document CRUD | 8 | Create (201), Get (200/404), Rename (200/404), Delete (204 idempotent/404) |
| `test_fr2_ot_editing.py` | FR2 — Concurrent editing | 2 | Two concurrent inserts at pos 0 → both in final content |
| `test_fr3_cursor_presence.py` | FR3 — Cursor presence | 2 | Cross-client cursor visibility within 5s |
| `test_fr4_causal_ordering.py` | FR4 — Causal ordering | 3 | Strictly monotonic revisions; REST reflects latest; no repeats |

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_PORT` | `8010` | Host port for the app |
| `DB_URL` | (compose default) | PostgreSQL connection string |
| `REDIS_URL` | (compose default) | Redis connection string |

All have safe defaults — the stack starts without a `.env` file. See `.env.example` for documentation.

## Project structure

```
├── src/googledocs/          ← Application code
│   ├── main.py              ← App factory + lifespan + /healthz
│   ├── config.py            ← pydantic-settings configuration
│   ├── database.py          ← Async SQLAlchemy engine + session
│   ├── redis.py             ← Redis client factory
│   ├── models/              ← ORM models (Document, Operation)
│   ├── schemas/             ← Pydantic DTOs (create, update, response, WS messages)
│   ├── routers/             ← FastAPI routers (REST CRUD, WS edit, WS presence)
│   ├── services/            ← Business logic (DocumentService, OTEngine, ConnectionManager, Presence)
│   └── ot/                  ← Pure OT transform functions (no I/O)
├── tests/                   ← White-box unit tests
├── verify/                  ← Black-box acceptance tests + CI manifest
├── alembic/                 ← Database migrations
└── Dockerfile + compose.yml ← Container orchestration
```

## License

MIT
