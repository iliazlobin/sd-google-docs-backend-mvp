# Google Docs MVP — MVP Scope (the contract for what we build NOW)

This file is the **contract**. The architect turns it into `design.md` + the executable
`verify/acceptance/` suite; the verifier gates against the Acceptance Criteria below. Be concrete.

## Stack

Python 3.12 · FastAPI (uvicorn) · PostgreSQL 16 · Redis 7 · SQLAlchemy (async) · Alembic ·
pydantic-settings · websockets · httpx · pytest + pytest-asyncio · Docker Compose

## Scope

**In (build now):**
- FR1: Create, open, rename, soft-delete documents via REST
- FR2: Concurrent real-time text editing via WebSocket (Jupiter OT, single server)
- FR3: Live cursor presence via WebSocket + Redis pub/sub
- FR4: Causal ordering via server-assigned monotonic revision numbers
- Health check endpoint (`GET /healthz`)
- White-box unit tests under `tests/`
- CI/CD via GitHub Actions (lint + unit + functional)

**Out (later phases):**
- Rich text formatting (bold, italic, headers, lists)
- Images, tables, comments
- Version history with point-in-time restore
- Multi-region replication, sharding, horizontal scale
- Offline editing with sync-on-reconnect
- Authentication / user management
- Collaborative undo/redo

## Functional Requirements

Be specific about status codes, payloads, error cases, idempotency, and concurrency.

- **FR1 — Document CRUD.** `POST /docs` → `201` with `{id, title, content, revision}`. `GET /docs/{id}` → `200` or `404`. `PATCH /docs/{id}` rename → `200`. `DELETE /docs/{id}` soft delete → `204`, idempotent (second delete also `204`). Unknown id → `404`.
- **FR2 — Concurrent real-time text editing.** `WS /docs/{id}/edit` — Jupiter OT protocol. Client sends `{type, position, rev, text?, length?}`; server transforms against concurrent ops and broadcasts to all connected clients. Two clients inserting at the same position concurrently must both see their text in the final document content.
- **FR3 — Live cursor presence.** `WS /docs/{id}/presence` — Client sends `{type: "cursor", position, user_id, user_name?}`; server broadcasts the full presence set via Redis pub/sub. Client B must see Client A's cursor within 5s.
- **FR4 — Causal ordering.** Server-assigned revision numbers are strictly monotonic per document. Five sequential inserts must produce strictly increasing revisions. REST GET must reflect the latest revision after each edit.

## Acceptance Criteria

One per functional requirement, phrased as an assertion the verifier can EXECUTE against the running system.
These map 1:1 to files under `verify/acceptance/`.

- **AC-1 (FR1)** — `POST /docs` → `201` with `{id, title, content: "", revision: 0}`. `GET /docs/{id}` → `200` with full metadata. `PATCH /docs/{id}` rename → `200` with new title. `DELETE /docs/{id}` → `204`; second delete also `204`. Unknown id → `404` on all three read/mutate endpoints.
- **AC-2 (FR2)** — Two WebSocket clients connect to `WS /docs/{id}/edit`. Client A inserts "Hello" at pos 0; Client B inserts "World" at pos 0 (same base revision). Both receive at least 2 messages (ack + broadcast). Final content via `GET /docs/{id}` contains both strings, total length = 10.
- **AC-3 (FR3)** — Two clients connect to `WS /docs/{id}/presence`. Client A sends cursor position 42. Client B receives a presence update containing Client A's user_id and position. Both clients sending cursors result in both seeing each other.
- **AC-4 (FR4)** — Five sequential inserts produce strictly increasing revisions on the server. `GET /docs/{id}` after each insert reflects the current revision number. Start revision is 0; first op rev > 0.

**Gate rules (NEVER violate):**
- Do NOT edit/skip/`xfail`/loosen acceptance cases or `verify/manifest.env` to go green. Make the SYSTEM satisfy the requirement.

## Build Plan

The kanban dependency chain. Architect delivers `design.md` + `verify/acceptance/` first (this card).

```
architect (design.md + verify/acceptance/)
    ↓
senior-engineer (scaffold, CRUD, WS wiring, schemas, unit tests, CI)
    ↓
staff-engineer (OT engine, concurrency, data model — critical paths)
    ↓
verifier (GATE: clean checkout, full test suite)
    ↓
sre (compose polish, DEPLOY.md, verify/manifest.env)
    ↓
writer (README + synthesis)
```

### Detailed implementation tasks

Each task below is a kanban card. Tagged with `Tier: staff|senior` per the architect's labeling in `design.md` §9.
The `assignee` column is the kanban profile that picks up the card. `Parents` enforces dependency order.

#### Card 1: Project scaffold + config (senior)
**Tier: senior-engineer** | Assignee: `projects-senior-engineer`
- `pyproject.toml` with deps from `design.md` §9 Task 1
- `.env.example`, `.gitignore`
- `src/googledocs/config.py` — `Settings(BaseSettings)` with DB_URL, REDIS_URL, APP_PORT
- `src/googledocs/main.py` — `create_app()`, lifespan, `GET /healthz`
- `src/googledocs/database.py` — async engine, session factory, `get_session`
- `src/googledocs/redis.py` — Redis client factory, `get_redis`
- Multi-stage `Dockerfile` (python:3.12-slim) with HEALTHCHECK
- `docker-compose.yml`: `db` (postgres:16), `redis` (redis:7), `app`; `APP_PORT` override
- Verify: `GET /healthz` → 200; app starts without crashing

#### Card 2: Data model + Alembic migrations (staff)
**Tier: staff-engineer** | Assignee: `projects-staff-engineer` | Parents: Card 1
- `src/googledocs/models/document.py` — `Document` ORM model (`id`, `title`, `content`, `revision`, `created_at`, `updated_at`, `deleted_at`)
- `src/googledocs/models/operation.py` — `Operation` ORM model (`id`, `document_id`, `user_id`, `type`, `position`, `text`, `length`, `revision`, `created_at`)
- `alembic init` + `001_initial.py` — creates both tables, unique index on `(document_id, revision)`, CHECK on `type IN ('insert', 'delete')`
- `alembic/env.py` — imports `googledocs.models`, targets `Base.metadata`
- Verify: `alembic upgrade head` creates tables; no `create_all` in app startup

#### Card 3: Pydantic schemas (senior)
**Tier: senior-engineer** | Assignee: `projects-senior-engineer` | Parents: Card 2
- `src/googledocs/schemas/document.py` — `DocumentCreate(title)`, `DocumentUpdate(title)`, `DocumentResponse(id, title, content, revision, created_at, updated_at)`
- `src/googledocs/schemas/operation.py` — `OperationIn(type, position, text?, length?, rev, user_id)`, `OperationOut(revision, type, position, text?, length?, user_id)`
- All Pydantic v2 models with `model_config = {"from_attributes": True}`

#### Card 4: Document CRUD router + service (senior)
**Tier: senior-engineer** | Assignee: `projects-senior-engineer` | Parents: Card 3
- `src/googledocs/services/document.py` — `DocumentService`: `create()`, `get()`, `update()`, `soft_delete()`, `get_content()` (recomputed from ops)
- `src/googledocs/routers/documents.py` — `POST /docs` → 201, `GET /docs/{id}` → 200/404, `PATCH /docs/{id}` → 200/404, `DELETE /docs/{id}` → 204 (idempotent)/404
- Verify: `test_fr1_crud.py` passes all 8 cases after Card 5 completes

#### Card 5: OT engine core — transform() + revision tracking (staff)
**Tier: staff-engineer** | Assignee: `projects-staff-engineer` | Parents: Card 2
- `src/googledocs/ot/transforms.py` — pure functions for 4-way transform matrix (insert_insert, insert_delete, delete_insert, delete_delete)
- `src/googledocs/services/ot_engine.py` — `OTEngine` class with `transform(client_op)`, `apply(transformed_op)`, `OpRingBuffer` (per-doc `deque(maxlen=500)` + `asyncio.Lock`)
- Implements: accept op → transform against concurrent ops → persist `Operation` row → update `Document.content` + `Document.revision` → broadcast
- Edge cases: stale revision → `STALE_REVISION` error; zero-length transform → drop op
- Verify: `test_fr4_causal_ordering.py` passes; `test_fr2_ot_editing.py` passes after Card 7

#### Card 6: WebSocket connection manager (staff)
**Tier: staff-engineer** | Assignee: `projects-staff-engineer` | Parents: Card 2
- `src/googledocs/services/connection_manager.py` — `ConnectionManager` singleton:
  - `connect(doc_id, ws)`, `disconnect(doc_id, ws)`, `broadcast(doc_id, message)`
  - `asyncio.Lock`-free (relies on asyncio event-loop safety for set ops)
  - Broadcast timeout: `asyncio.wait_for(send, timeout=5)` per client
  - Cleanup: remove empty doc sets to prevent memory leak
- Verify: `tests/test_connection_manager.py` — connect/disconnect/broadcast/timeout

#### Card 7: WebSocket edit endpoint (senior)
**Tier: senior-engineer** | Assignee: `projects-senior-engineer` | Parents: Card 5, Card 6
- `src/googledocs/routers/ws_edit.py` — `WS /docs/{id}/edit`:
  - On connect: register in `ConnectionManager`; validate doc exists (else close 4004)
  - On message: parse JSON → validate field presence → call `OTEngine.transform + apply` → broadcast to all clients
  - Send `{"type": "ack", "revision": N}` to sender
  - On disconnect: unregister
  - Error handling: invalid JSON → `{"type": "error", "code": "INVALID_MESSAGE"}`; stale rev → `STALE_REVISION`
- Verify: `test_fr2_ot_editing.py` passes (concurrent inserts at same position)

#### Card 8: Presence endpoint + Redis pub/sub (senior)
**Tier: senior-engineer** | Assignee: `projects-senior-engineer` | Parents: Card 6
- `src/googledocs/services/presence.py` — `PresenceService`:
  - `update_cursor(doc_id, user_id, position, user_name)` — in-memory dict + `PUBLISH presence:{doc_id}`
  - `get_snapshot(doc_id)` — current cursors, prune stale >30s
  - `subscribe(doc_id)` — async Redis listener
- `src/googledocs/routers/ws_presence.py` — `WS /docs/{id}/presence`:
  - On connect: send snapshot, subscribe to Redis channel
  - On message: `PUBLISH` cursor, broadcast to all WS clients on this doc
  - On disconnect: `PUBLISH` leave, unsubscribe
- Verify: `test_fr3_cursor_presence.py` passes (cross-client cursor visibility within 5s)

#### Card 9: White-box unit tests (senior)
**Tier: senior-engineer** | Assignee: `projects-senior-engineer` | Parents: Card 4, Card 5
- `tests/conftest.py` — async fixtures: test DB, Redis, app client, seeded document
- `tests/test_document_service.py` — CRUD, soft delete, idempotent delete, not-found
- `tests/test_ot_transforms.py` — all 4 transform combos, edge cases (overlapping deletes, zero-length)
- `tests/test_ot_engine.py` — full pipeline, revision monotonicity, stale rev error
- `tests/test_connection_manager.py` — connect/disconnect, broadcast, timeout, cleanup
- Run: `pytest tests/ -v` (all pass)

#### Card 10: CI pipeline (senior)
**Tier: senior-engineer** | Assignee: `projects-senior-engineer` | Parents: Card 9
- `.github/workflows/ci.yml`:
  - lint job: `ruff check src/ tests/ verify/`
  - test job: `pip install -e .[dev] && pytest tests/ -v`
  - docker job: `docker build .`
  - e2e job: `docker compose up -d --wait && pytest verify/acceptance/ -v`

#### Card 11: Docker polish + DEPLOY.md (sre)
**Tier: senior-engineer** | Assignee: `projects-sre` | Parents: Card 10
- Finalize `docker-compose.yml` with healthchecks on all services, `restart: unless-stopped`
- `DEPLOY.md` — host run/teardown, env table, migration step, troubleshooting
- `verify/manifest.env` — already delivered by architect; verify it works with `e2e-verify`

#### Card 12: README + synthesis (writer)
**Tier: senior-engineer** | Assignee: `projects-writer` | Parents: Card 11
- `README.md` — what it is, stack, quick start, API table, evidence trail
- `docs/synthesis.md` — evidence-backed build summary with verifier output
- Final lint pass: zero Hermes/kanban/sandbox refs in product files

### Acceptance test mapping

| File | FR | AC | Runs against |
|---|---|---|---|
| `verify/acceptance/test_fr1_crud.py` (8 cases) | FR1 | AC-1 | HTTP |
| `verify/acceptance/test_fr2_ot_editing.py` (2 cases) | FR2 | AC-2 | WebSocket + HTTP |
| `verify/acceptance/test_fr3_cursor_presence.py` (2 cases) | FR3 | AC-3 | WebSocket |
| `verify/acceptance/test_fr4_causal_ordering.py` (3 cases) | FR4 | AC-4 | WebSocket + HTTP |

All tests are black-box: `httpx` for HTTP, `websockets` for WS. No app imports. Base URL from `API_BASE_URL` env var.
