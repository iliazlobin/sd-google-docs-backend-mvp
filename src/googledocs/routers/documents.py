"""REST router for document CRUD operations.

POST /docs → 201 create
GET /docs/{id} → 200 (metadata + computed content) or 404
PATCH /docs/{id} → 200 rename or 404
DELETE /docs/{id} → 204 idempotent soft delete or 404
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from googledocs.database import get_session
from googledocs.schemas.document import DocumentCreate, DocumentResponse, DocumentUpdate
from googledocs.services.document import DocumentService

router = APIRouter(prefix="/docs", tags=["documents"])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=DocumentResponse)
async def create_document(
    body: DocumentCreate,
    session: AsyncSession = Depends(get_session),
):
    svc = DocumentService(session)
    doc = await svc.create(title=body.title)
    await session.commit()
    await session.refresh(doc)
    return doc


@router.get("/{doc_id}", response_model=DocumentResponse)
async def get_document(
    doc_id: UUID,
    session: AsyncSession = Depends(get_session),
):
    svc = DocumentService(session)
    doc = await svc.get(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    # Compute content from operations
    doc.content = await svc.get_content(doc_id)
    return doc


@router.patch("/{doc_id}", response_model=DocumentResponse)
async def update_document(
    doc_id: UUID,
    body: DocumentUpdate,
    session: AsyncSession = Depends(get_session),
):
    svc = DocumentService(session)
    doc = await svc.update(doc_id, title=body.title)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    await session.commit()
    await session.refresh(doc)
    doc.content = await svc.get_content(doc_id)
    return doc


@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    doc_id: UUID,
    session: AsyncSession = Depends(get_session),
):
    svc = DocumentService(session)
    found = await svc.soft_delete(doc_id)
    if not found:
        raise HTTPException(status_code=404, detail="Document not found")
    await session.commit()
