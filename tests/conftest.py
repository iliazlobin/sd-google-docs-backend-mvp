"""Test fixtures — async app client, test DB, seeded document.

Uses a session-scoped event loop and app to avoid the SQLAlchemy-async
"Future attached to a different loop" error across test functions.
"""

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from googledocs.database import engine
from googledocs.main import create_app


@pytest.fixture(scope="session")
def event_loop():
    """Create a session-scoped event loop so the SQLAlchemy async engine
    stays bound to the same loop across all test functions."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.run_until_complete(engine.dispose())
    loop.close()


@pytest.fixture(scope="session")
def app():
    """Session-scoped FastAPI app — engine is shared across all tests."""
    return create_app()


@pytest.fixture
async def client(app):
    """Per-function async HTTP client for the app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
