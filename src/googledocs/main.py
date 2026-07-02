"""FastAPI application factory with lifespan, health check, and router registration."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from googledocs.database import engine
from googledocs.redis import close_redis, init_redis
from googledocs.routers import documents, ws_edit, ws_presence

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    """Application lifespan: connect services on startup, disconnect on shutdown."""
    try:
        await init_redis()
    except Exception:
        logger.warning("Redis unavailable — continuing without it", exc_info=True)
    yield
    await close_redis()
    await engine.dispose()


def create_app() -> FastAPI:
    """Build and return a configured FastAPI application."""
    app = FastAPI(
        title="Google Docs MVP",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    # Register routers
    app.include_router(documents.router)
    app.include_router(ws_edit.router)
    app.include_router(ws_presence.router)

    return app


app = create_app()
