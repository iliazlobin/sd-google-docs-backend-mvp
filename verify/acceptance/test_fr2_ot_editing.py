"""FR2: Concurrent real-time text editing via Jupiter OT protocol.

Black-box acceptance: two WebSocket clients insert at the same position concurrently.
Both clients' text must appear in the final document content.
Uses the `websockets` library for WS communication. No app imports.
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
async def test_concurrent_inserts_at_same_position_both_appear():
    """Two clients insert at pos 0 (same base rev). Final content contains both strings, length 10."""
    async with httpx.AsyncClient(base_url=API_BASE) as http_client:
        # Create a document first
        create_resp = await http_client.post("/docs", json={"title": "FR2 OT Test"})
        assert create_resp.status_code == 201
        doc_id = create_resp.json()["id"]

    ws_url = _ws_url(API_BASE, f"/docs/{doc_id}/edit")

    # Barrier: both clients exchange a signal before sending their insert
    # This ensures both see rev=0 as their base revision
    barrier = asyncio.Barrier(2)

    received_a: list[dict] = []
    received_b: list[dict] = []

    async def client_a():
        async with websockets.connect(ws_url) as ws:
            await barrier.wait()  # sync with client B
            # Insert "Hello" at position 0, base rev 0
            msg = json.dumps({
                "type": "insert",
                "position": 0,
                "text": "Hello",
                "rev": 0,
                "user_id": "client-a",
            })
            await ws.send(msg)

            # Collect all responses (ack + op broadcast)
            for _ in range(3):  # at least ack, possibly op from B
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    received_a.append(json.loads(raw))
                except asyncio.TimeoutError:
                    break

    async def client_b():
        async with websockets.connect(ws_url) as ws:
            await barrier.wait()  # sync with client A
            # Insert "World" at position 0, base rev 0
            msg = json.dumps({
                "type": "insert",
                "position": 0,
                "text": "World",
                "rev": 0,
                "user_id": "client-b",
            })
            await ws.send(msg)

            # Collect all responses
            for _ in range(3):
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    received_b.append(json.loads(raw))
                except asyncio.TimeoutError:
                    break

    # Run both clients concurrently
    await asyncio.gather(client_a(), client_b())

    # Each client should receive at least 2 messages: their own ack + the other's broadcast
    assert len(received_a) >= 2, f"Client A received only {len(received_a)} messages: {received_a}"
    assert len(received_b) >= 2, f"Client B received only {len(received_b)} messages: {received_b}"

    # Verify final content via GET
    async with httpx.AsyncClient(base_url=API_BASE) as http_client:
        resp = await http_client.get(f"/docs/{doc_id}")
        assert resp.status_code == 200
        content = resp.json()["content"]

    # Both strings must be present in the final content
    assert "Hello" in content, f"'Hello' missing from content: {content!r}"
    assert "World" in content, f"'World' missing from content: {content!r}"

    # Total length must be 10 (Hello=5 + World=5)
    assert len(content) == 10, f"Expected content length 10, got {len(content)}: {content!r}"


@pytest.mark.asyncio
async def test_edit_on_nonexistent_document_receives_error():
    """WS /docs/{unknown_id}/edit → error or connection closed."""
    ws_url = _ws_url(API_BASE, "/docs/00000000-0000-0000-0000-000000000000/edit")

    try:
        async with websockets.connect(ws_url) as ws:
            # Try to send an op
            msg = json.dumps({
                "type": "insert",
                "position": 0,
                "text": "test",
                "rev": 0,
                "user_id": "test-user",
            })
            await ws.send(msg)

            # Should receive an error or connection close
            raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
            data = json.loads(raw)
            assert data.get("type") == "error", f"Expected error, got {data}"
    except websockets.exceptions.ConnectionClosed as e:
        # Connection being closed (e.g., 404) is also acceptable
        assert e.code in (4000, 4004, 1008, 1011), f"Unexpected close code: {e.code}"
