from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SecurityRunCreate(BaseModel):
    agent_id: str
    # A garak --spec selector, e.g. "probes.dan.Dan_11_0" or "tag:owasp:llm01".
    probes: str | None = None
    generations: int = Field(default=1, ge=1, le=10)


class SecurityRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    agent_id: str
    status: str
    probes: str
    generations: int
    report_path: str | None
    summary: dict[str, Any] = Field(default_factory=dict)
    error: str | None
    log_tail: str | None
    created_at: datetime
    finished_at: datetime | None


class ProbePreset(BaseModel):
    id: str
    label: str
    description: str
