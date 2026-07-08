"""ConnectionManager — per-document WebSocket connection pools with broadcast.

Uses per-connection asyncio.Lock to serialize sends to the same WebSocket.
This prevents a race condition where two coroutines call ASGI's ``send``
concurrently on the same connection (e.g. a broadcast from one client's handler
and an ack from another), which can silently drop one message.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Singleton that tracks WebSocket connections per document.

    All sends to a given WebSocket are serialised via a per-connection
    ``asyncio.Lock`` so that ``_lock_and_send`` and ``send`` calls never
    overlap on the ASGI send callable (which is not concurrent-safe).
    """

    def __init__(self) -> None:
        self._pools: dict[str, set[WebSocket]] = {}
        self._send_locks: dict[int, asyncio.Lock] = {}

    def connect(self, doc_id: str, ws: WebSocket) -> None:
        self._pools.setdefault(doc_id, set()).add(ws)
        self._send_locks[id(ws)] = asyncio.Lock()

    def disconnect(self, doc_id: str, ws: WebSocket) -> None:
        pool = self._pools.get(doc_id)
        if pool is None:
            return
        pool.discard(ws)
        if not pool:
            del self._pools[doc_id]
        self._send_locks.pop(id(ws), None)

    async def send(self, ws: WebSocket, message: dict[str, Any], doc_id: str) -> None:
        """Deliver a JSON message to a single WebSocket, serialised via its lock.

        This is the counterpart of ``broadcast`` for one-to-one sends such as
        acks.  Using this instead of ``ws.send_json()`` directly ensures that
        the ack and a concurrent broadcast to the same connection do not race
        on the ASGI send callable.
        """
        lock = self._send_locks.get(id(ws))
        if lock is None:
            return  # already disconnected — nothing to protect
        async with lock:
            await self._do_send(ws, message, doc_id)

    async def broadcast(
        self, doc_id: str, message: dict[str, Any], exclude: WebSocket | None = None
    ) -> None:
        """Send a JSON message to every connected client for the given document.

        Optionally exclude one WebSocket (e.g., the sender who already received an ack).
        """
        pool = self._pools.get(doc_id)
        if not pool:
            return
        tasks = []
        for _ws in list(pool):
            if exclude is not None and _ws is exclude:
                continue
            # Acquire the per-connection lock so ASGI ``send`` is never called
            # concurrently by two coroutines on the same WebSocket.
            lock = self._send_locks.get(id(_ws))
            if lock is None:
                continue
            tasks.append(self._lock_and_send(lock, _ws, message, doc_id))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _lock_and_send(
        self, lock: asyncio.Lock, ws: WebSocket, message: dict[str, Any], doc_id: str
    ) -> None:
        async with lock:
            await self._do_send(ws, message, doc_id)

    async def _do_send(self, ws: WebSocket, message: dict[str, Any], doc_id: str) -> None:
        try:
            await asyncio.wait_for(ws.send_json(message), timeout=5.0)
        except TimeoutError:
            logger.warning("Send timeout for client on doc %s", doc_id)
        except WebSocketDisconnect:
            self.disconnect(doc_id, ws)
        except Exception:
            logger.debug("Error sending to client on doc %s", doc_id, exc_info=True)
            self.disconnect(doc_id, ws)


# Module-level singleton
manager = ConnectionManager()
