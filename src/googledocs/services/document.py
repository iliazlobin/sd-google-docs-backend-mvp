"""DocumentService — CRUD operations and content reconstruction from ops."""

from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from googledocs.models.document import Document
from googledocs.models.operation import Operation


class DocumentService:
    """Handles document CRUD and content computation."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def create(self, title: str) -> Document:
        doc = Document(title=title, content="", revision=0)
        self._session.add(doc)
        await self._session.flush()
        return doc

    async def get(self, doc_id: UUID) -> Document | None:
        stmt = select(Document).where(
            Document.id == doc_id,
            Document.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_any(self, doc_id: UUID) -> Document | None:
        """Get document regardless of soft-delete status. For delete idempotency."""
        stmt = select(Document).where(Document.id == doc_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def update(self, doc_id: UUID, title: str) -> Document | None:
        doc = await self.get(doc_id)
        if doc is None:
            return None
        doc.title = title
        await self._session.flush()
        return doc

    async def soft_delete(self, doc_id: UUID) -> bool:
        """Soft-delete a document. Returns True if the document was found (active or
        already deleted — idempotent)."""
        from datetime import datetime, timezone

        doc = await self.get_any(doc_id)
        if doc is None:
            return False
        if doc.deleted_at is None:
            doc.deleted_at = datetime.now(timezone.utc)
            await self._session.flush()
        return True

    async def get_content(self, doc_id: UUID) -> str:
        """Reconstruct document content from operations, ordered by revision."""
        stmt = (
            select(Operation)
            .where(Operation.document_id == doc_id)
            .order_by(Operation.revision.asc())
        )
        result = await self._session.execute(stmt)
        ops = result.scalars().all()

        content_chars: list[str] = []
        for op in ops:
            if op.type == "insert":
                text = op.text or ""
                # Insert at position — shift existing chars right
                content_chars[op.position : op.position] = list(text)
            elif op.type == "delete":
                length = op.length or 0
                del content_chars[op.position : op.position + length]
        return "".join(content_chars)
