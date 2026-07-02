"""FR3: Live cursor presence via WebSocket + Redis pub/sub.

Black-box acceptance: two clients connect to presence endpoint.
Client A's cursor update must be visible to Client B.
Uses the `websockets` library. No app imports.
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
async def test_client_b_receives_client_a_cursor():
    """Client A sends cursor; Client B receives presence update containing A's user_id and position."""
    async with httpx.AsyncClient(base_url=API_BASE) as http_client:
        create_resp = await http_client.post("/docs", json={"title": "FR3 Presence Test"})
        assert create_resp.status_code == 201
        doc_id = create_resp.json()["id"]

    ws_url = _ws_url(API_BASE, f"/docs/{doc_id}/presence")

    received_by_b: list[dict] = []

    async def client_a():
        async with websockets.connect(ws_url) as ws:
            # Discard initial snapshot
            await asyncio.wait_for(ws.recv(), timeout=5.0)

            # Send cursor update
            msg = json.dumps({
                "type": "cursor",
                "position": 42,
                "user_id": "alice",
                "user_name": "Alice",
            })
            await ws.send(msg)

            # Wait a bit for propagation
            await asyncio.sleep(1.0)

    async def client_b():
        async with websockets.connect(ws_url) as ws:
            # Discard initial snapshot
            initial = await asyncio.wait_for(ws.recv(), timeout=5.0)
            initial_data = json.loads(initial)

            # Then listen for presence updates (up to 5s)
            deadline = asyncio.get_event_loop().time() + 5.0
            while asyncio.get_event_loop().time() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
                    msg_data = json.loads(raw)
                    received_by_b.append(msg_data)
                    # If we got a presence update with alice, we can stop early
                    if msg_data.get("type") == "presence":
                        cursors = msg_data.get("cursors", {})
                        if "alice" in cursors:
                            break
                except asyncio.TimeoutError:
                    pass

    # Start client B first so it's subscribed, then client A sends
    await asyncio.gather(client_b(), client_a())

    # Check that B received a presence update with alice's cursor
    presence_updates = [m for m in received_by_b if m.get("type") == "presence"]
    assert len(presence_updates) > 0, f"No presence updates received by B: {received_by_b}"

    # The last presence update should contain alice
    alice_found = False
    for update in presence_updates:
        cursors = update.get("cursors", {})
        if "alice" in cursors:
            assert cursors["alice"]["position"] == 42, (
                f"Expected alice position 42, got {cursors['alice']}"
            )
            alice_found = True
            break

    assert alice_found, f"Alice not found in any presence update: {presence_updates}"


@pytest.mark.asyncio
async def test_both_clients_see_each_other():
    """Both clients sending cursors result in both seeing each other."""
    async with httpx.AsyncClient(base_url=API_BASE) as http_client:
        create_resp = await http_client.post("/docs", json={"title": "FR3 Mutual Presence"})
        assert create_resp.status_code == 201
        doc_id = create_resp.json()["id"]

    ws_url = _ws_url(API_BASE, f"/docs/{doc_id}/presence")

    received_a: list[dict] = []
    received_b: list[dict] = []

    async def client_a():
        async with websockets.connect(ws_url) as ws:
            await ws.recv()  # discard snapshot
            # Send cursor
            await ws.send(json.dumps({
                "type": "cursor",
                "position": 10,
                "user_id": "alice",
                "user_name": "Alice",
            }))
            # Listen for bob's presence
            deadline = asyncio.get_event_loop().time() + 5.0
            while asyncio.get_event_loop().time() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
                    received_a.append(json.loads(raw))
                    for m in received_a:
                        cursors = m.get("cursors", {})
                        if "bob" in cursors:
                            return
                except asyncio.TimeoutError:
                    pass

    async def client_b():
        async with websockets.connect(ws_url) as ws:
            await ws.recv()  # discard snapshot
            # Send cursor
            await ws.send(json.dumps({
                "type": "cursor",
                "position": 99,
                "user_id": "bob",
                "user_name": "Bob",
            }))
            # Listen for alice's presence
            deadline = asyncio.get_event_loop().time() + 5.0
            while asyncio.get_event_loop().time() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
                    received_b.append(json.loads(raw))
                    for m in received_b:
                        cursors = m.get("cursors", {})
                        if "alice" in cursors:
                            return
                except asyncio.TimeoutError:
                    pass

    await asyncio.gather(client_a(), client_b())

    # Verify A saw Bob
    a_has_bob = any(
        "bob" in m.get("cursors", {})
        for m in received_a if m.get("type") == "presence"
    )
    assert a_has_bob, f"Client A never saw Bob's cursor. Received: {received_a}"

    # Verify B saw Alice
    b_has_alice = any(
        "alice" in m.get("cursors", {})
        for m in received_b if m.get("type") == "presence"
    )
    assert b_has_alice, f"Client B never saw Alice's cursor. Received: {received_b}"
