from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


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
    content: str = Field(min_length=1)
