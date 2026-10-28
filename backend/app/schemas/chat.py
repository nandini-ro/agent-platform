from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.services.attachments import MAX_ATTACHMENTS_PER_MESSAGE


class AgentChatRequest(BaseModel):
    """Stateless single-turn request - the shape Garak's REST generator posts."""

    message: str = Field(min_length=1)


class ToolCallRead(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: str = ""
    source: str = "local"
    ok: bool = True
    error: str | None = None
    duration_ms: int = 0


class AgentChatResponse(BaseModel):
    agent_id: str
    response: str
    tool_calls: list[ToolCallRead] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SendMessageRequest(BaseModel):
    # May be empty when the message is only attachments.
    content: str = ""
    # Ids from POST /api/attachments, claimed by this message.
    attachment_ids: list[str] = Field(
        default_factory=list, max_length=MAX_ATTACHMENTS_PER_MESSAGE
    )

    @model_validator(mode="after")
    def has_something_to_send(self) -> "SendMessageRequest":
        if not self.content.strip() and not self.attachment_ids:
            raise ValueError("A message needs text or an attachment.")
        return self
