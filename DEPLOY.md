# Google Docs MVP — Deploy

> Collaborative text-editing backend with Jupiter OT, WebSocket presence,
> PostgreSQL storage, and Redis pub/sub.

## Prerequisites

- **Docker** 24+ with Compose V2 (`docker compose` plugin)
- **curl** (for health checks — included in the app image via `apt-get`)
- Port **8010** (or `$APP_PORT` override) free on the host
- Ports **5433** and **6380** free for DB/Redis host access (optional — only needed if you want to connect directly; services communicate over the compose network internally)

No `.env` file or Python installation required on the host — the app runs entirely in containers.

## Quick start

```bash
# 1. Clone the repository
git clone <repo-url> sd-google-docs-backend
cd sd-google-docs-backend

# 2. (Optional) Create .env for port overrides
# The app works out of the box with defaults; only create .env if you need
# custom ports or connection strings.
cp .env.example .env
# Edit .env to uncomment and set any overrides you need

# 3. Start the stack
docker compose up -d --wait

# 4. Verify the app is healthy
curl -sf http://localhost:${APP_PORT:-8010}/healthz
# Expected: {"status":"ok"}
```

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_PORT` | `8010` | Host port mapped to the app container (:8000) |
| `DB_PORT` | `5433` | Host port mapped to PostgreSQL (:5432) |
| `REDIS_PORT` | `6380` | Host port mapped to Redis (:6379) |
| `DB_URL` | (set in compose) | AsyncPG connection string — only needed outside compose |
| `REDIS_URL` | (set in compose) | Redis connection string — only needed outside compose |

All compose variables have safe defaults — the stack starts without a `.env` file.

## Services

| Name | Image | Internal port | Host port | Health check |
|------|-------|--------------|-----------|-------------|
| `db` | postgres:16-alpine | 5432 | 5433 (127.0.0.1, override via `DB_PORT`) | `pg_isready -U googledocs` |
| `redis` | redis:7-alpine | 6379 | 6380 (127.0.0.1, override via `REDIS_PORT`) | `redis-cli ping` |
| `app` | (build from `Dockerfile`) | 8000 | 8010 (override via `APP_PORT`) | `GET /healthz` → 200 |

## Running migrations

Schema migrations run automatically **inside the app container** at startup if
`alembic upgrade head` is part of the entrypoint. To run them manually:

```bash
docker compose run --rm app alembic upgrade head
```

## Verifying the full pipeline

```bash
# Health check
curl -sf http://localhost:8010/healthz

# Create a document
curl -s -X POST http://localhost:8010/docs \
  -H "Content-Type: application/json" \
  -d '{"title":"test"}'
# Expected: {"id":"<uuid>","title":"test","content":"","revision":0,...}

# List documents
curl -s http://localhost:8010/docs

# Get document
curl -s http://localhost:8010/docs/<id>

# Rename document
curl -s -X PATCH http://localhost:8010/docs/<id> \
  -H "Content-Type: application/json" \
  -d '{"title":"renamed"}'

# Soft-delete document
curl -s -X DELETE http://localhost:8010/docs/<id>
# Expected: 204 (idempotent — second delete also 204)
```

## Acceptance tests

Run the black-box acceptance suite against a live stack:

```bash
docker compose up -d --wait
API_BASE_URL=http://localhost:8010 pytest verify/acceptance/ -v
```

## Logs

```bash
# All services
docker compose logs --tail=50

# App only
docker compose logs app --tail=50 -f
```

## Teardown

```bash
# Stop and remove containers, network, and volumes
docker compose down --volumes

# Or just stop (keep volumes/data)
docker compose down
```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| App container crash-loops | DB not ready | Compose `depends_on: condition: service_healthy` handles this; check `docker compose logs db` |
| `curl: (56) Recv failure` | Wrong port | Use `APP_PORT` override; default is 8010 |
| `ModuleNotFoundError` | Docker build cache stale | `docker compose build --no-cache app` |
| Tests fail with connection refused | Stack not up | Run `docker compose up -d --wait` first |
