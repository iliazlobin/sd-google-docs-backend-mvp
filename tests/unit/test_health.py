"""Skeleton health-check test — verifies the app boots and healthz responds."""

import pytest


@pytest.mark.asyncio
async def test_healthz_returns_200(client):
    """GET /healthz returns 200 with status ok."""
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
