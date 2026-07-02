"""OTEngine — Jupiter OT transform + apply pipeline with in-memory ring buffer."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from googledocs.models.document import Document
from googledocs.ot.transforms import Op as TransOp
from googledocs.ot.transforms import delete_delete, delete_insert, insert_delete, insert_insert

logger = logging.getLogger(__name__)

OpType = Literal["insert", "delete"]


@dataclass
class BufferedOp:
    """An op stored in the ring buffer for transform context."""

    type: OpType
    position: int
    revision: int
    text: str | None = None
    length: int | None = None
    user_id: str = ""


class StaleRevisionError(Exception):
    """Client's base revision is too old — must reload."""


class OTEngine:
    """Jupiter OT engine with per-document asyncio.Lock and ring buffer.

    The lock is per document — serializes all edits on a document
    to guarantee correct transform ordering. Callers must acquire the
    lock themselves via ``OTEngine.lock_for(doc_id)`` so they can commit
    inside the lock scope.
    """

    # Shared state across ALL engine instances (class-level)
    _locks: dict[str, asyncio.Lock] = {}
    _buffers: dict[str, deque[BufferedOp]] = {}

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @classmethod
    def _ensure_doc_state(cls, doc_id: str) -> tuple[asyncio.Lock, deque[BufferedOp]]:
        if doc_id not in cls._locks:
            cls._locks[doc_id] = asyncio.Lock()
        if doc_id not in cls._buffers:
            cls._buffers[doc_id] = deque(maxlen=500)
        return cls._locks[doc_id], cls._buffers[doc_id]

    @classmethod
    def lock_for(cls, doc_id: str) -> asyncio.Lock:
        """Return the per-document lock so callers can hold it across commit."""
        lock, _ = cls._ensure_doc_state(doc_id)
        return lock

    @classmethod
    def _get_concurrent_ops(cls, buffer: deque[BufferedOp], base_rev: int) -> list[BufferedOp]:
        """Return ops in the buffer with revision > base_rev (concurrent ops)."""
        return [op for op in buffer if op.revision > base_rev]

    def _transform_against(
        self, client_op: TransOp, concurrent: BufferedOp
    ) -> TransOp | None:
        """Transform client_op against one concurrent op. Returns None if absorbed."""
        c = TransOp(
            type=concurrent.type,
            position=concurrent.position,
            text=concurrent.text,
            length=concurrent.length,
        )
        if client_op.type == "insert" and concurrent.type == "insert":
            return insert_insert(c, client_op)
        elif client_op.type == "insert" and concurrent.type == "delete":
            return delete_insert(c, client_op)
        elif client_op.type == "delete" and concurrent.type == "insert":
            return insert_delete(c, client_op)
        elif client_op.type == "delete" and concurrent.type == "delete":
            return delete_delete(c, client_op)
        return client_op

    async def process(
        self,
        doc_id: str,
        user_id: str,
        op_type: OpType,
        position: int,
        base_rev: int,
        text: str | None = None,
        length: int | None = None,
    ) -> tuple[int, str]:
        """Accept a client op, transform, apply, persist, and return (new_revision, updated_content).

        Does NOT hold the per-document lock — the caller must hold OTEngine.lock_for(doc_id)
        and commit the session inside it.

        Returns:
            (revision, new_content) — the assigned revision and resulting document text.

        Raises:
            StaleRevisionError: client's base_rev is too old for the ring buffer.
        """
        doc_id_str = str(doc_id)
        _, buffer = self._ensure_doc_state(doc_id_str)

        # Check if the base revision is too old — the client missed ops
        # that have fallen off the ring buffer and can't be transformed against.
        if (
            buffer
            and base_rev < buffer[0].revision
            and len(buffer) >= buffer.maxlen
        ):
            raise StaleRevisionError(
                f"Client rev {base_rev} is behind oldest buffered rev {buffer[0].revision} "
                "and buffer is full. Reload the document."
            )

        # Fetch the document — expire any cached copy to force a fresh read from DB
        stmt = select(Document).where(Document.id == doc_id).execution_options(
            populate_existing=True
        )
        result = await self._session.execute(stmt)
        doc = result.scalar_one()

        # Build the client op
        client_op = TransOp(type=op_type, position=position, text=text, length=length)

        # Get concurrent ops and transform
        concurrent_ops = self._get_concurrent_ops(buffer, base_rev)
        for concurrent in concurrent_ops:
            client_op = self._transform_against(client_op, concurrent)
            if client_op is None:
                # Op was absorbed — return current revision with no change
                return doc.revision, doc.content

        # Validate transformed op
        if client_op.type == "insert" and (client_op.text or "") == "":
            return doc.revision, doc.content
        if client_op.type == "delete" and (client_op.length or 0) <= 0:
            return doc.revision, doc.content

        # Assign next revision
        new_rev = doc.revision + 1

        # Persist the Operation record
        from googledocs.models.operation import Operation

        op_record = Operation(
            document_id=doc.id,
            user_id=user_id,
            type=client_op.type,
            position=client_op.position,
            text=client_op.text,
            length=client_op.length,
            revision=new_rev,
        )
        self._session.add(op_record)

        # Update document content
        if client_op.type == "insert":
            insert_text = client_op.text or ""
            doc.content = (
                doc.content[: client_op.position]
                + insert_text
                + doc.content[client_op.position :]
            )
        elif client_op.type == "delete":
            del_len = client_op.length or 0
            doc.content = (
                doc.content[: client_op.position]
                + doc.content[client_op.position + del_len :]
            )

        doc.revision = new_rev
        doc.updated_at = datetime.now(timezone.utc)

        await self._session.flush()

        # Add to ring buffer
        buffer.append(
            BufferedOp(
                type=client_op.type,
                position=client_op.position,
                revision=new_rev,
                text=client_op.text,
                length=client_op.length,
                user_id=user_id,
            )
        )

        return new_rev, doc.content

    async def get_current_revision(self, doc_id: str) -> int:
        """Read the current revision for a document."""
        stmt = select(Document.revision).where(Document.id == doc_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
