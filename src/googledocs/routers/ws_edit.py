"""WebSocket endpoint for collaborative editing via Jupiter OT protocol.

WS /docs/{id}/edit — clients send insert/delete operations; server transforms,
applies, persists, and broadcasts to all connected clients.
"""

from __future__ import annotations

import json
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from googledocs.database import get_session
from googledocs.models.document import Document
from googledocs.services.connection_manager import manager as conn_manager
from googledocs.services.ot_engine import OTEngine, StaleRevisionError

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/docs/{doc_id}/edit")
async def ws_edit(
    ws: WebSocket,
    doc_id: UUID,
    session: AsyncSession = Depends(get_session),
):
    await ws.accept()

    # Validate document exists
    stmt = select(Document).where(
        Document.id == doc_id,
        Document.deleted_at.is_(None),
    )
    result = await session.execute(stmt)
    doc = result.scalar_one_or_none()
    if doc is None:
        await ws.send_json({"type": "error", "code": "DOC_NOT_FOUND", "message": "Document not found"})
        await ws.close(code=4004)
        return

    doc_id_str = str(doc_id)
    conn_manager.connect(doc_id_str, ws)

    engine = OTEngine(session)

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_json({"type": "error", "code": "INVALID_MESSAGE", "message": "Invalid JSON"})
                continue

            op_type = msg.get("type")
            if op_type not in ("insert", "delete"):
                await ws.send_json({"type": "error", "code": "INVALID_MESSAGE", "message": f"Unknown type: {op_type}"})
                continue

            position = msg.get("position")
            base_rev = msg.get("rev")
            user_id = msg.get("user_id", "unknown")
            text = msg.get("text")
            length = msg.get("length")

            if position is None or base_rev is None:
                await ws.send_json({"type": "error", "code": "INVALID_MESSAGE", "message": "Missing position or rev"})
                continue

            try:
                # Hold the per-document lock across process + commit so
                # the next op's engine sees committed data when it reads.
                lock = OTEngine.lock_for(doc_id_str)
                async with lock:
                    new_rev, _ = await engine.process(
                        doc_id=doc_id_str,
                        user_id=user_id,
                        op_type=op_type,
                        position=position,
                        base_rev=base_rev,
                        text=text,
                        length=length,
                    )
                    await session.commit()
            except StaleRevisionError as e:
                await ws.send_json({"type": "error", "code": "STALE_REVISION", "message": str(e)})
                continue

            # Broadcast to all clients
            broadcast_msg = {
                "type": "op",
                "revision": new_rev,
                "op_type": op_type,
                "position": position,
                "user_id": user_id,
            }
            if op_type == "insert":
                broadcast_msg["text"] = text
            elif op_type == "delete":
                broadcast_msg["length"] = length

            # Send ack to sender
            ack_msg = {
                "type": "ack",
                "revision": new_rev,
            }
            await ws.send_json(ack_msg)

            # Broadcast the op to everyone else (not the sender)
            await conn_manager.broadcast(doc_id_str, broadcast_msg, exclude=ws)

    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Error in WS edit for doc %s", doc_id_str)
    finally:
        conn_manager.disconnect(doc_id_str, ws)
