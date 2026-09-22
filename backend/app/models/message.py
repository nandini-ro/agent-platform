from datetime import datetime

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, created_column, pk_column


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = pk_column()
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # user | assistant
    content: Mapped[str] = mapped_column(Text, default="")
    # Tool/MCP call records for UI rendering and audit.
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = created_column()

    conversation: Mapped["Conversation"] = relationship(  # noqa: F821
        back_populates="messages"
    )
