from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class HTTPToolBase(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str = ""
    parameters: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )
    method: str = "GET"
    url_template: str = Field(min_length=1, max_length=2000)
    # Values may reference a secret as {{env:NAME}}; the name is stored, never
    # the value.
    headers: dict[str, str] = Field(default_factory=dict)
    query_template: dict[str, str] = Field(default_factory=dict)
    body_template: str | None = None
    response_path: str | None = Field(default=None, max_length=200)
    enabled: bool = True
    timeout_seconds: int = Field(default=10, ge=1, le=60)


class HTTPToolCreate(HTTPToolBase):
    pass


class HTTPToolUpdate(BaseModel):
    description: str | None = None
    parameters: dict[str, Any] | None = None
    method: str | None = None
    url_template: str | None = Field(default=None, min_length=1, max_length=2000)
    headers: dict[str, str] | None = None
    query_template: dict[str, str] | None = None
    body_template: str | None = None
    response_path: str | None = Field(default=None, max_length=200)
    enabled: bool | None = None
    timeout_seconds: int | None = Field(default=None, ge=1, le=60)


class HTTPToolRead(HTTPToolBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime
    updated_at: datetime


class HTTPToolTestRequest(BaseModel):
    """Arguments to try the tool with, as the model would supply them."""

    arguments: dict[str, Any] = Field(default_factory=dict)


class HTTPToolTestResult(BaseModel):
    ok: bool
    result: str = ""
    error: str | None = None
    duration_ms: int = 0


class ToolCatalogueEntry(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]
    source: str  # builtin | http
