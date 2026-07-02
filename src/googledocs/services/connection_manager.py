"""ConnectionManager — per-document WebSocket connection pools with broadcast."""

import asyncio
import logging
from typing import Any

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Singleton that tracks WebSocket connections per document.

    Relies on asyncio event-loop safety for set operations — no explicit lock needed
    since all mutations happen on the same event loop.
    """

    def __init__(self) -> None:
        self._pools: dict[str, set[WebSocket]] = {}

    def connect(self, doc_id: str, ws: WebSocket) -> None:
        self._pools.setdefault(doc_id, set()).add(ws)

    def disconnect(self, doc_id: str, ws: WebSocket) -> None:
        pool = self._pools.get(doc_id)
        if pool is None:
            return
        pool.discard(ws)
        if not pool:
            del self._pools[doc_id]

    async def broadcast(
        self, doc_id: str, message: dict[str, Any], exclude: WebSocket | None = None
    ) -> None:
        """Send a JSON message to every connected client for the given document.

        Optionally exclude one WebSocket (e.g., the sender who already received an ack).
        """
        pool = self._pools.get(doc_id)
        if not pool:
            return
        # Copy to avoid mutation during iteration
        tasks = []
        for _ws in list(pool):
            if exclude is not None and _ws is exclude:
                continue
            tasks.append(self._safe_send(_ws, message, doc_id))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _safe_send(self, ws: WebSocket, message: dict[str, Any], doc_id: str) -> None:
        try:
            await asyncio.wait_for(ws.send_json(message), timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning("Broadcast timeout for client on doc %s", doc_id)
        except WebSocketDisconnect:
            self.disconnect(doc_id, ws)
        except Exception:
            logger.debug("Error sending to client on doc %s", doc_id, exc_info=True)
            self.disconnect(doc_id, ws)


# Module-level singleton
manager = ConnectionManager()
