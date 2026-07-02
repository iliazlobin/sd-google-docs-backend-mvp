"""Pydantic schemas for WebSocket operation messages."""

from pydantic import BaseModel, Field


class OperationIn(BaseModel):
    """Client → Server operation message."""

    type: str = Field(..., description="'insert' or 'delete'")
    position: int
    text: str | None = None
    length: int | None = None
    rev: int = Field(..., description="Client's last seen server revision")
    user_id: str


class OperationOut(BaseModel):
    """Server → Client broadcast operation."""

    type: str
    revision: int
    op_type: str | None = Field(None, alias="type_out")
    position: int | None = None
    text: str | None = None
    length: int | None = None
    user_id: str | None = None

    model_config = {"populate_by_name": True}
