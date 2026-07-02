# Google Docs MVP — Design

> Collaborative text-editing backend with Jupiter OT, WebSocket presence,
> PostgreSQL storage, and Redis pub/sub.

- **Stack:** Python 3.12 · FastAPI · PostgreSQL 16 · Redis 7 · SQLAlchemy (async) · Alembic
- **Protocol:** Jupiter OT (single-server) with in-memory ring buffer
- **Presence:** Redis pub/sub per-document channels

## 1. Functional requirements (MVP)

| ID | Requirement | Acceptance test | Transport |
|----|-----------|----------------|-----------|
| FR1 | Create, open, rename, soft-delete documents via REST | `test_fr1_crud.py` (8 cases) | HTTP |
| FR2 | Concurrent real-time text editing via WebSocket (Jupiter OT) | `test_fr2_ot_editing.py` (2 cases) | WebSocket + HTTP |
| FR3 | Live cursor presence via WebSocket + Redis pub/sub | `test_fr3_cursor_presence.py` (2 cases) | WebSocket |
| FR4 | Causal ordering via server-assigned monotonic revision numbers | `test_fr4_causal_ordering.py` (3 cases) | WebSocket + HTTP |

**Out of scope:** Rich text, images/tables, version history, auth, scaling, offline editing.

## 2. Back-of-the-envelope

| Metric | Calculation | Result |
|--------|-----------|--------|
| Edit throughput | 1M DAU × 5 docs × 30 edits/min | ~42K writes/sec (MVP targets 100 concurrent) |
| Operation storage | ~120 bytes/op × 10K ops/doc | ~1.2 MB/document |
| Ring buffer memory | 500 ops × 120 bytes × 100 active docs | ~6 MB |

## 3. Data model

```sql
Document {
  id:         uuid PK DEFAULT gen_random_uuid()
  title:      text NOT NULL
  content:    text NOT NULL DEFAULT ''
  revision:   int NOT NULL DEFAULT 0         -- server-assigned monotonic
  created_at: timestamptz NOT NULL DEFAULT now()
  updated_at: timestamptz NOT NULL DEFAULT now()
  deleted_at: timestamptz                    -- soft delete (NULL = active)
}

Operation {
  id:          uuid PK DEFAULT gen_random_uuid()
  document_id: uuid NOT NULL FK ON DELETE CASCADE
  user_id:     text NOT NULL
  type:        text NOT NULL CHECK IN ('insert', 'delete')
  position:    int NOT NULL
  text:        text
  length:      int
  revision:    int NOT NULL
  created_at:  timestamptz NOT NULL DEFAULT now()
}

UNIQUE INDEX ON Operation(document_id, revision)
```

## 4. API

| Method | Path | Status | Description |
|--------|------|--------|-------------|
| POST | /docs | 201 | Create document; body {title} |
| GET | /docs/{id} | 200/404 | Get document metadata + content |
| PATCH | /docs/{id} | 200/404 | Rename document |
| DELETE | /docs/{id} | 204/404 | Soft delete (idempotent) |
| GET | /healthz | 200 | Liveness probe |

**WS /docs/{id}/edit** — send insert/delete ops; server transforms, persists, broadcasts.
**WS /docs/{id}/presence** — send cursor positions; server relays via Redis pub/sub.

## 5. Architecture

Edit flow: Client sends op over WebSocket → OTEngine transforms against concurrent ops in ring buffer → persists Operation row + bumps Document.revision → ConnectionManager broadcasts to all clients → sender gets ack.

Presence flow: Client sends cursor → server stores in-memory + PUBLISH to Redis `presence:{doc_id}` → subscribed clients receive full presence set. 30s TTL prunes stale cursors.

## 6. Deep dives

### 6.1 Jupiter OT engine

2 clients insert at position 5 concurrently (same base rev=10). Without transformation both apply at pos 5. Decision: Jupiter OT with 4-rule transform matrix (insert-insert: shift right on tie; insert-delete: shift right; delete-insert: shift left; delete-delete: truncate overlap). Chosen over last-write-wins (loses data) and CRDTs (over-engineered for single-server MVP).

Ring buffer: deque(maxlen=500) per doc. Clients behind >500 ops get STALE_REVISION error. Per-doc asyncio.Lock serializes edits.

### 6.2 Connection manager

dict[doc_id, set[WebSocket]] singleton. Broadcast with asyncio.wait_for(send, timeout=5). Empty sets cleaned up. Disconnects during broadcast caught and removed.

### 6.3 Redis presence

Redis pub/sub on per-document channels. On connect: send snapshot + subscribe. Cursors published, forwarded to all WS clients. Redis unavailable: presence no-op, editing unaffected.

## 7. Trade-offs

| Decision | Choice | Rationale |
|----------|--------|-----------|
| OT protocol | Jupiter (single-server) | Simplest causality; proven in Etherpad |
| Content storage | Recomputed from ops on read | Ops are source of truth; enables rich text later |
| Locking | asyncio.Lock per doc | Serializes per-doc; no deadlock |
| Op buffer | 500 ops ring buffer | 6 MB for 100 active docs |
| Presence | Redis pub/sub | Survives restart; extends to multi-server |
| Soft delete | deleted_at timestamp | Fast, reversible |
| No auth | user_id string | MVP scope |

## 8. Project layout

```
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml
├── DEPLOY.md
├── DESIGN.md (this file)
├── README.md
├── alembic/versions/001_initial.py
├── src/googledocs/
│   ├── main.py
│   ├── config.py
│   ├── database.py
│   ├── redis.py
│   ├── models/ (Document, Operation ORM)
│   ├── schemas/ (Pydantic DTOs)
│   ├── routers/ (documents, ws_edit, ws_presence)
│   ├── services/ (document, ot_engine, connection_manager, presence)
│   └── ot/transforms.py (pure functions, no I/O)
├── tests/ (white-box, import googledocs)
└── verify/acceptance/ (black-box, HTTP/WS only)
```

## 9. Test evidence

White-box: `pytest tests/ -v` → 1 passed (test_healthz_returns_200). Verifies app boots via ASGITransport.

Acceptance tests (15 cases across 4 FRs):
- test_fr1_crud.py (8): Create 201, Get 200/404, Rename 200/404, Delete 204 idempotent/404
- test_fr2_ot_editing.py (2): Concurrent inserts at pos 0 → both in final content, length 10
- test_fr3_cursor_presence.py (2): Cross-client cursor visibility within 5s
- test_fr4_causal_ordering.py (3): Monotonic revisions, REST reflects latest, no duplicates

Run: `docker compose up -d --wait && docker compose run --rm app alembic upgrade head && API_BASE_URL=http://localhost:${APP_PORT:-8010} pytest verify/acceptance/ -v`
