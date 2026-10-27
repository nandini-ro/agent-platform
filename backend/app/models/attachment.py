"""A file sent with a chat message: an image the model sees, or a document
whose text the model reads.

An attachment is uploaded first, unclaimed (no message_id), and claimed by the
message it is sent with. Images keep their bytes, because they are resent to
the model as images. Documents keep only their extracted text, which is all
the model ever receives.
"""

from datetime import datetime

from sqlalchemy import ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, created_column, pk_column


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[str] = pk_column()
    message_id: Mapped[str | None] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), nullable=True, index=True
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # image | document
    data: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = created_column()
