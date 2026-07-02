"""FR4: Causal ordering via server-assigned monotonic revision numbers.

Black-box acceptance: five sequential inserts must produce strictly increasing revisions.
REST GET must reflect the latest revision after each edit.
Uses `websockets` for WS edits + `httpx` for REST verification. No app imports.
"""

import asyncio
import json
import os

import httpx
import pytest
import websockets

API_BASE = os.environ.get("API_BASE_URL", "http://localhost:8010")


def _ws_url(http_url: str, path: str) -> str:
    """Convert HTTP base URL to WebSocket URL."""
    return http_url.replace("http://", "ws://") + path


@pytest.mark.asyncio
async def test_five_sequential_inserts_produce_strictly_increasing_revisions():
    """Five sequential inserts → strictly monotonic revisions. Start rev is 0; first op rev > 0."""
    async with httpx.AsyncClient(base_url=API_BASE) as http_client:
        create_resp = await http_client.post("/docs", json={"title": "FR4 Causal Order Test"})
        assert create_resp.status_code == 201
        doc_id = create_resp.json()["id"]
        assert create_resp.json()["revision"] == 0

    ws_url = _ws_url(API_BASE, f"/docs/{doc_id}/edit")

    revisions: list[int] = []
    received = False

    async with websockets.connect(ws_url) as ws:
        # Insert 5 characters sequentially, each at increasing position
        chars = ["A", "B", "C", "D", "E"]
        for i, char in enumerate(chars):
            msg = json.dumps({
                "type": "insert",
                "position": i,  # position 0, then 1, then 2...
                "text": char,
                "rev": i,       # base rev matches expected server state
                "user_id": "test-user",
            })
            await ws.send(msg)

            # Receive ack (or op broadcast)
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                data = json.loads(raw)

                # An ack or op message with a revision number
                if data.get("type") in ("ack", "op"):
                    rev = data.get("revision")
                    assert rev is not None, f"No revision in response: {data}"
                    revisions.append(rev)
                    received = True
            except asyncio.TimeoutError:
                pytest.fail(f"Timeout waiting for response after insert #{i+1}")

    assert received, "No responses received from WebSocket"

    # Verify: revisions are strictly increasing
    assert len(revisions) == 5, f"Expected 5 revisions, got {len(revisions)}: {revisions}"
    for i in range(len(revisions) - 1):
        assert revisions[i] < revisions[i + 1], (
            f"Revision {revisions[i]} not less than {revisions[i + 1]}. "
            f"All revisions: {revisions}"
        )

    # First revision must be > 0
    assert revisions[0] > 0, f"First revision should be > 0, got {revisions[0]}"


@pytest.mark.asyncio
async def test_rest_get_reflects_updated_revision():
    """GET /docs/{id} after inserts reflects the latest revision number."""
    async with httpx.AsyncClient(base_url=API_BASE) as http_client:
        create_resp = await http_client.post("/docs", json={"title": "FR4 HTTP Rev Test"})
        assert create_resp.status_code == 201
        doc_id = create_resp.json()["id"]

        # Verify starting revision is 0
        assert create_resp.json()["revision"] == 0
        assert create_resp.json()["content"] == ""

    ws_url = _ws_url(API_BASE, f"/docs/{doc_id}/edit")

    async with websockets.connect(ws_url) as ws:
        # Insert "XY"
        await ws.send(json.dumps({
            "type": "insert", "position": 0, "text": "X", "rev": 0, "user_id": "u1",
        }))
        ack1 = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))

        await ws.send(json.dumps({
            "type": "insert", "position": 1, "text": "Y", "rev": 1, "user_id": "u1",
        }))
        ack2 = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))

    # Now verify via HTTP GET
    async with httpx.AsyncClient(base_url=API_BASE) as http_client:
        resp = await http_client.get(f"/docs/{doc_id}")
        assert resp.status_code == 200
        data = resp.json()

        # Content should be "XY"
        assert data["content"] == "XY", f"Expected content 'XY', got {data['content']!r}"

        # Revision should be the latest one
        expected_rev = max(
            ack1.get("revision", 0),
            ack2.get("revision", 0),
        )
        assert data["revision"] == expected_rev, (
            f"Expected revision {expected_rev}, got {data['revision']}"
        )


@pytest.mark.asyncio
async def test_revision_never_repeats():
    """No two operations on the same document share the same revision number."""
    async with httpx.AsyncClient(base_url=API_BASE) as http_client:
        create_resp = await http_client.post("/docs", json={"title": "FR4 No Repeat"})
        assert create_resp.status_code == 201
        doc_id = create_resp.json()["id"]

    ws_url = _ws_url(API_BASE, f"/docs/{doc_id}/edit")

    all_revisions: list[int] = []

    async with websockets.connect(ws_url) as ws:
        for i in range(10):
            await ws.send(json.dumps({
                "type": "insert", "position": i, "text": "x",
                "rev": i, "user_id": "u1",
            }))
            raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
            data = json.loads(raw)
            rev = data.get("revision")
            if rev is not None:
                all_revisions.append(rev)

    # All revisions must be unique
    assert len(all_revisions) == len(set(all_revisions)), (
        f"Duplicate revisions found: {all_revisions}"
    )
