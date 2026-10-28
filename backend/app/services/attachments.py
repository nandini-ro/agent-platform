"""Turning uploaded files into something a model can use.

Two kinds, decided by the file's content rather than its claimed type:

* image    - PNG, JPEG, WebP or GIF. Sent to the model as an image, so the
             agent's model must accept images (on Groq, qwen/qwen3.8-27b).
* document - PDF, or UTF-8 text (.txt .md .csv .json and similar). The text is
             extracted here and sent as part of the message, so it works
             with every model.

Limits follow the strictest provider: Groq takes at most 3 images per request
and about 4 MB per base64 image.
"""

from __future__ import annotations

import base64
from pathlib import PurePath
from typing import Any

from app.models.attachment import Attachment
from app.services.builtin_tools import pdf_text
from app.services.tool_base import ToolError

MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
MAX_UPLOAD_BYTES = max(MAX_IMAGE_BYTES, MAX_DOCUMENT_BYTES)
MAX_ATTACHMENTS_PER_MESSAGE = 5
MAX_IMAGES_PER_REQUEST = 3
MAX_DOCUMENT_CHARS = 20_000
MAX_PDF_PAGES = 50

TEXT_EXTENSIONS = frozenset(
    {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".xml", ".yaml", ".yml",
     ".html", ".htm", ".log", ".py", ".js", ".ts", ".sql"}
)

# Magic numbers. The browser's Content-Type is a hint, not a fact.
_IMAGE_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


class AttachmentError(ValueError):
    """An upload was refused. The message is safe to show the user."""


def sniff_image(data: bytes) -> str | None:
    for signature, media_type in _IMAGE_SIGNATURES:
        if data.startswith(signature):
            return media_type
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def classify(filename: str, data: bytes) -> tuple[str, str, bytes | None, str | None]:
    """(kind, content_type, bytes to keep, extracted text) for an upload."""
    if not data:
        raise AttachmentError("The file is empty.")

    image_type = sniff_image(data)
    if image_type:
        if len(data) > MAX_IMAGE_BYTES:
            raise AttachmentError(
                f"Images can be at most {MAX_IMAGE_BYTES // (1024 * 1024)} MB."
            )
        return "image", image_type, data, None

    if len(data) > MAX_DOCUMENT_BYTES:
        raise AttachmentError(
            f"Documents can be at most {MAX_DOCUMENT_BYTES // (1024 * 1024)} MB."
        )

    if data.lstrip()[:5].startswith(b"%PDF"):
        try:
            text = pdf_text(data, MAX_PDF_PAGES)
        except ToolError as exc:
            raise AttachmentError(str(exc)) from None
        return "document", "application/pdf", None, _cap(text)

    suffix = PurePath(filename).suffix.lower()
    if suffix in TEXT_EXTENSIONS or _looks_like_text(data):
        return "document", "text/plain", None, _cap(data.decode("utf-8", errors="replace"))

    raise AttachmentError(
        "Unsupported file. Attach an image (PNG, JPEG, WebP, GIF), a PDF, or a "
        "text file (.txt, .md, .csv, .json)."
    )


def _looks_like_text(data: bytes) -> bool:
    sample = data[:4096]
    if b"\x00" in sample:
        return False
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError as exc:
        # A multi-byte character cut off by the sample boundary is still text.
        return exc.start >= len(sample) - 3
    return True


def _cap(text: str) -> str:
    text = text.strip()
    if len(text) > MAX_DOCUMENT_CHARS:
        return text[:MAX_DOCUMENT_CHARS] + "\n...[truncated]"
    return text


def summary(attachment: Attachment) -> dict[str, Any]:
    """What the UI shows and the message's metadata records."""
    return {
        "id": attachment.id,
        "filename": attachment.filename,
        "content_type": attachment.content_type,
        "size": attachment.size,
        "kind": attachment.kind,
    }


def message_blocks(
    text: str, attachments: list[Attachment], *, include_images: bool = True
) -> str | list[dict[str, Any]]:
    """A user turn as the runtime's neutral content.

    Documents are folded into the text, after what the user typed. Images
    become image blocks, or a placeholder when `include_images` is false
    (older turns, once the per-request image limit is spent).
    """
    if not attachments:
        return text

    parts = [text] if text else []
    images: list[dict[str, Any]] = []
    for a in attachments:
        if a.kind == "document":
            parts.append(f"[Attached file: {a.filename}]\n{a.text or '(no text)'}")
        elif include_images and a.data:
            images.append(
                {
                    "type": "image",
                    "media_type": a.content_type,
                    "data": base64.b64encode(a.data).decode(),
                    "name": a.filename,
                }
            )
        else:
            parts.append(f"[Image {a.filename} was shared earlier in the conversation]")

    combined = "\n\n".join(parts)
    if not images:
        return combined
    return [{"type": "text", "text": combined}, *images] if combined else images
