"""Pydantic schemas for Document REST API."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class DocumentCreate(BaseModel):
    title: str = "Untitled"


class DocumentUpdate(BaseModel):
    title: str


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    content: str
    revision: int
    created_at: datetime
    updated_at: datetime
