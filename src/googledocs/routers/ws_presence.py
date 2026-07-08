"""WebSocket endpoint for live cursor presence via Redis pub/sub.

WS /docs/{id}/presence — clients send cursor positions; server broadcasts
the full presence set via Redis pub/sub to all connected clients.
"""

import asyncio
import json
import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from redis.asyncio import Redis

from googledocs.redis import get_redis
from googledocs.services.connection_manager import manager as conn_manager
from googledocs.services.presence import PresenceService

logger = logging.getLogger(__name__)

router = APIRouter()

# Module-level presence service (initialised on first use)
_presence_svc: PresenceService | None = None


def _get_presence_service(redis: Redis) -> PresenceService:
    global _presence_svc
    if _presence_svc is None:
        _presence_svc = PresenceService(redis)
    return _presence_svc


@router.websocket("/docs/{doc_id}/presence")
async def ws_presence(
    ws: WebSocket,
    doc_id: UUID,
    redis: Redis = Depends(get_redis),
):
    await ws.accept()

    doc_id_str = str(doc_id)
    svc = _get_presence_service(redis)
    conn_manager.connect(doc_id_str, ws)

    # Track the user_id for this connection so we can publish leave on disconnect
    last_user_id: str = "unknown"

    # Send initial presence snapshot
    snapshot = svc.get_snapshot(doc_id_str)
    await conn_manager.send(ws, {"type": "presence", "cursors": snapshot}, doc_id=doc_id_str)

    # Subscribe to Redis presence channel
    redis_queue = await svc.subscribe(doc_id_str)

    # Start a task to forward Redis messages to the WS client
    async def forward_redis() -> None:
        try:
            while True:
                data = await redis_queue.get()
                await conn_manager.send(ws, data, doc_id=doc_id_str)
        except asyncio.CancelledError:
            pass
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.debug("Forward task error for doc %s", doc_id_str, exc_info=True)

    forward_task = asyncio.create_task(forward_redis())

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg: dict[str, Any] = json.loads(raw)
            except json.JSONDecodeError:
                continue

            if msg.get("type") != "cursor":
                continue

            position: int = msg.get("position", 0)
            user_id: str = msg.get("user_id", "unknown")
            user_name: str = msg.get("user_name", "")
            last_user_id = user_id

            await svc.update_cursor(doc_id_str, user_id, position, user_name)

    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Error in WS presence for doc %s", doc_id_str)
    finally:
        forward_task.cancel()
        try:
            await forward_task
        except asyncio.CancelledError:
            pass

        conn_manager.disconnect(doc_id_str, ws)
        # Remove user from cursors and publish leave event
        await svc.remove_user(doc_id_str, last_user_id)
