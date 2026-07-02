"""FR1: Document CRUD — create, open, rename, soft-delete documents via REST.

Black-box acceptance: verifies full CRUD lifecycle, status codes, and idempotency.
Talks to the running app via API_BASE_URL. No app imports.
"""

import os

import httpx
import pytest

API_BASE = os.environ.get("API_BASE_URL", "http://localhost:8010")


@pytest.mark.asyncio
async def test_create_document_returns_201_with_metadata():
    """POST /docs → 201 with {id, title, content: "", revision: 0}."""
    async with httpx.AsyncClient(base_url=API_BASE) as client:
        resp = await client.post("/docs", json={"title": "FR1 Test Doc"})
        assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
        data = resp.json()

        assert "id" in data
        assert data["title"] == "FR1 Test Doc"
        assert data["content"] == ""
        assert data["revision"] == 0
        assert "created_at" in data
        assert "updated_at" in data


@pytest.mark.asyncio
async def test_get_existing_document_returns_200():
    """GET /docs/{id} → 200 with full metadata."""
    async with httpx.AsyncClient(base_url=API_BASE) as client:
        # Create a doc first
        create_resp = await client.post("/docs", json={"title": "FR1 Get Test"})
        assert create_resp.status_code == 201
        doc_id = create_resp.json()["id"]

        # Get it
        resp = await client.get(f"/docs/{doc_id}")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()

        assert data["id"] == doc_id
        assert data["title"] == "FR1 Get Test"
        assert data["content"] == ""
        assert data["revision"] == 0


@pytest.mark.asyncio
async def test_get_nonexistent_document_returns_404():
    """GET /docs/{unknown_id} → 404."""
    async with httpx.AsyncClient(base_url=API_BASE) as client:
        resp = await client.get("/docs/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_patch_rename_document_returns_200():
    """PATCH /docs/{id} rename → 200 with new title."""
    async with httpx.AsyncClient(base_url=API_BASE) as client:
        # Create a doc
        create_resp = await client.post("/docs", json={"title": "Original Title"})
        assert create_resp.status_code == 201
        doc_id = create_resp.json()["id"]

        # Rename it
        resp = await client.patch(f"/docs/{doc_id}", json={"title": "Renamed Title"})
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()

        assert data["id"] == doc_id
        assert data["title"] == "Renamed Title"

        # Verify via GET
        get_resp = await client.get(f"/docs/{doc_id}")
        assert get_resp.json()["title"] == "Renamed Title"


@pytest.mark.asyncio
async def test_patch_nonexistent_document_returns_404():
    """PATCH /docs/{unknown_id} → 404."""
    async with httpx.AsyncClient(base_url=API_BASE) as client:
        resp = await client.patch(
            "/docs/00000000-0000-0000-0000-000000000000",
            json={"title": "Nope"},
        )
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_document_returns_204():
    """DELETE /docs/{id} → 204, subsequent GET → 404."""
    async with httpx.AsyncClient(base_url=API_BASE) as client:
        # Create a doc
        create_resp = await client.post("/docs", json={"title": "To Be Deleted"})
        assert create_resp.status_code == 201
        doc_id = create_resp.json()["id"]

        # Delete it
        resp = await client.delete(f"/docs/{doc_id}")
        assert resp.status_code == 204, f"Expected 204, got {resp.status_code}: {resp.text}"

        # Verify it's gone
        get_resp = await client.get(f"/docs/{doc_id}")
        assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_is_idempotent():
    """Second DELETE on same document also returns 204."""
    async with httpx.AsyncClient(base_url=API_BASE) as client:
        # Create and delete
        create_resp = await client.post("/docs", json={"title": "Idempotent Delete"})
        doc_id = create_resp.json()["id"]

        await client.delete(f"/docs/{doc_id}")  # First delete
        resp = await client.delete(f"/docs/{doc_id}")  # Second delete

        assert resp.status_code == 204, f"Expected 204 idempotent, got {resp.status_code}"


@pytest.mark.asyncio
async def test_delete_nonexistent_document_returns_404():
    """DELETE /docs/{unknown_id} → 404."""
    async with httpx.AsyncClient(base_url=API_BASE) as client:
        resp = await client.delete("/docs/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404
