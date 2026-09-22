"""SQLAlchemy declarative base and shared column helpers."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlalchemy.orm import DeclarativeBase, mapped_column


class Base(DeclarativeBase):
    pass


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def pk_column():
    """String UUID primary key - portable across SQLite and PostgreSQL."""
    return mapped_column(primary_key=True, default=new_id)


def created_column():
    return mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


def updated_column():
    return mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
