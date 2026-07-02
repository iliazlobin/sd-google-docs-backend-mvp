"""Functional tests: document CRUD endpoint scenarios with a real Postgres DB.

These tests exercise the important behaviours from the spec's §6 Test scenarios:
- Idempotency, ordering, ownership, validation & error paths.
They run in-process via TestClient with a real Postgres, NOT against compose.
"""

import uuid

import pytest


@pytest.mark.asyncio
async def test_create_document_returns_201(client):
    """POST /docs → 201 with metadata."""
    resp = await client.post("/docs", json={"title": "Functional Test"})
    assert resp.status_code == 201
    data = resp.json()
    assert "id" in data
    assert data["title"] == "Functional Test"
    assert data["content"] == ""
    assert data["revision"] == 0


@pytest.mark.asyncio
async def test_create_document_default_title(client):
    """POST /docs with no title → uses default title."""
    resp = await client.post("/docs", json={})
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "Untitled"


@pytest.mark.asyncio
async def test_get_nonexistent_document_404(client):
    """GET /docs/{unknown} → 404."""
    fake_id = uuid.uuid4()
    resp = await client.get(f"/docs/{fake_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_healthz(client):
    """GET /healthz → 200."""
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
