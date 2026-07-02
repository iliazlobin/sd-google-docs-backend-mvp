"""SQLAlchemy declarative Base — single import point for all models / metadata."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
