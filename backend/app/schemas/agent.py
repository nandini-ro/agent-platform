from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AgentBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    system_prompt: str = ""
    provider: str = "mock"
    model: str = "mock-1"
    temperature: float = Field(default=1.0, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2048, ge=1, le=64000)
    tools: list[str] = Field(default_factory=list)
    mcp_server_ids: list[str] = Field(default_factory=list)


class AgentCreate(AgentBase):
    pass


class AgentUpdate(BaseModel):
    """All fields optional - PATCH semantics."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    system_prompt: str | None = None
    provider: str | None = None
    model: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=64000)
    tools: list[str] | None = None
    mcp_server_ids: list[str] | None = None


class AgentRead(AgentBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime
    updated_at: datetime


class ProviderInfo(BaseModel):
    name: str
    available: bool
    supports_tools: bool
    models: list[str]
