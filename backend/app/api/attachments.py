"""Uploading chat attachments, and serving images back for display.

An upload is stored unclaimed and returned with its id; the chat message that
sends it claims it (app/api/chat.py). Like every other write, an upload needs
the admin key when one is configured.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.db.session import get_db
from app.models.attachment import Attachment
from app.services.attachments import MAX_UPLOAD_BYTES, AttachmentError, classify, summary

router = APIRouter(prefix="/api/attachments", tags=["attachments"])

# Uploads never sent with a message are removed after this long.
_UNCLAIMED_TTL = timedelta(hours=24)


class AttachmentRead(BaseModel):
    id: str
    filename: str
    content_type: str
    size: int
    kind: str


@router.post("", response_model=AttachmentRead, status_code=status.HTTP_201_CREATED)
async def upload_attachment(
    file: UploadFile = File(...), db: Session = Depends(get_db)
) -> dict:
    # Read one byte past the limit, so an oversized file is refused without
    # holding all of it in memory.
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"Files can be at most {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
        )
    filename = (file.filename or "file").rsplit("/", 1)[-1][:255] or "file"
    try:
        kind, content_type, kept, text = classify(filename, data)
    except AttachmentError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None

    db.execute(
        delete(Attachment).where(
            Attachment.message_id.is_(None),
            Attachment.created_at < utcnow() - _UNCLAIMED_TTL,
        )
    )
    attachment = Attachment(
        filename=filename,
        content_type=content_type,
        size=len(data),
        kind=kind,
        data=kept,
        text=text,
    )
    db.add(attachment)
    db.commit()
    return summary(attachment)


@router.get("/{attachment_id}/content")
def attachment_content(attachment_id: str, db: Session = Depends(get_db)) -> Response:
    """An image's bytes, for the chat to display. Documents have none kept."""
    attachment = db.get(Attachment, attachment_id)
    if attachment is None or attachment.data is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found")
    return Response(
        content=attachment.data,
        # Only sniffed image types are ever stored with bytes, so this header
        # cannot be talked into text/html.
        media_type=attachment.content_type,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=86400, immutable",
            "Content-Security-Policy": "default-src 'none'",
        },
    )
