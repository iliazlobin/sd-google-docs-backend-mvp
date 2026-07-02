"""PresenceService — cursor position tracking with Redis pub/sub broadcast."""

import asyncio
import json
import logging
import time
from typing import Any

from redis.asyncio import Redis

logger = logging.getLogger(__name__)


class PresenceService:
    """In-memory cursor state + Redis pub/sub for cross-client broadcast.

    Cursors are tracked in a per-document dict. Redis pub/sub ensures all
    subscribers (on the same server or across servers) see cursor updates.
    """

    def __init__(self, redis: Redis) -> None:
        self._redis = redis
        # {doc_id: {user_id: {position, user_name, ts}}}
        self._cursors: dict[str, dict[str, dict[str, Any]]] = {}

    async def update_cursor(
        self, doc_id: str, user_id: str, position: int, user_name: str = ""
    ) -> None:
        """Store cursor position and publish to Redis channel."""
        doc_cursors = self._cursors.setdefault(doc_id, {})
        doc_cursors[user_id] = {
            "position": position,
            "user_name": user_name,
            "ts": time.time(),
        }
        payload = json.dumps({
            "type": "presence",
            "doc_id": doc_id,
            "cursors": doc_cursors,
        })
        try:
            await self._redis.publish(f"presence:{doc_id}", payload)
        except Exception:
            logger.warning("Failed to publish presence for doc %s", doc_id, exc_info=True)

    async def remove_user(self, doc_id: str, user_id: str) -> None:
        """Remove user from cursors and publish leave event."""
        doc_cursors = self._cursors.get(doc_id)
        if doc_cursors:
            doc_cursors.pop(user_id, None)
            if not doc_cursors:
                self._cursors.pop(doc_id, None)
        payload = json.dumps({
            "type": "presence",
            "doc_id": doc_id,
            "cursors": self._cursors.get(doc_id, {}),
        })
        try:
            await self._redis.publish(f"presence:{doc_id}", payload)
        except Exception:
            logger.warning("Failed to publish leave for doc %s", doc_id, exc_info=True)

    def get_snapshot(self, doc_id: str) -> dict[str, dict[str, Any]]:
        """Return current cursors, pruning stale entries (>30s)."""
        doc_cursors = self._cursors.get(doc_id, {})
        now = time.time()
        pruned = {}
        for uid, data in doc_cursors.items():
            if now - data.get("ts", 0) <= 30:
                pruned[uid] = data
        # Update in-place
        self._cursors[doc_id] = pruned
        return pruned

    async def subscribe(self, doc_id: str) -> "asyncio.Queue[dict[str, Any]]":
        """Return an async queue that receives presence events for this document.

        The caller must consume from the queue. Redis messages are parsed
        and forwarded into the queue.
        """
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=128)
        channel_name = f"presence:{doc_id}"

        async def _listen() -> None:
            try:
                pubsub = self._redis.pubsub()
                await pubsub.subscribe(channel_name)
                async for message in pubsub.listen():
                    if message["type"] == "message":
                        try:
                            data = json.loads(message["data"])
                            queue.put_nowait(data)
                        except (json.JSONDecodeError, KeyError):
                            logger.debug("Malformed presence message: %s", message.get("data"))
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.warning("Presence listener for doc %s stopped", doc_id, exc_info=True)

        asyncio.create_task(_listen())
        return queue
