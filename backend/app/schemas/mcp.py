from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

TRANSPORTS = ("stdio", "http")


class MCPServerBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    transport: str = "stdio"

    # -- stdio --
    command: str = Field(default="", max_length=500)
    args: list[str] = Field(default_factory=list)
    cwd: str | None = None
    # Names of environment variables to forward to the subprocess. Values are
    # read from the backend's own environment and never stored or returned.
    env_keys: list[str] = Field(default_factory=list)

    # -- http --
    url: str = Field(default="", max_length=2000)
    # Values may reference a secret as {{env:NAME}}; the name is stored, never
    # the value. Same convention as the user-defined HTTP tools.
    headers: dict[str, str] = Field(default_factory=dict)

    enabled: bool = True

    @model_validator(mode="after")
    def _require_the_fields_the_transport_needs(self) -> MCPServerBase:
        """Each transport has exactly one required field; enforce it here.

        Doing this in the schema means the API, the tests and the UI all get
        the same message, and an unusable row can never reach the database.
        """
        if self.transport not in TRANSPORTS:
            raise ValueError(
                f"transport must be one of {', '.join(TRANSPORTS)}, got {self.transport!r}"
            )
        if self.transport == "stdio" and not self.command.strip():
            raise ValueError("A stdio server needs a command.")
        if self.transport == "http" and not self.url.strip():
            raise ValueError("An http server needs a URL.")
        return self


class MCPServerCreate(MCPServerBase):
    pass


class MCPServerUpdate(BaseModel):
    """Every field optional; the transport's requirement is re-checked in the API.

    A partial update cannot be validated in isolation - clearing `url` is only
    invalid if the stored transport is http - so that check lives where the
    existing row is in hand.
    """

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    transport: str | None = None
    command: str | None = Field(default=None, max_length=500)
    args: list[str] | None = None
    cwd: str | None = None
    env_keys: list[str] | None = None
    url: str | None = Field(default=None, max_length=2000)
    headers: dict[str, str] | None = None
    enabled: bool | None = None


class MCPServerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    slug: str
    description: str
    transport: str
    command: str
    args: list[str]
    cwd: str | None
    env_keys: list[str]
    url: str
    headers: dict[str, str]
    enabled: bool
    created_at: datetime
    updated_at: datetime


class MCPToolRead(BaseModel):
    server_id: str
    server_slug: str
    tool_name: str
    qualified_name: str
    description: str
    input_schema: dict[str, Any]


class MCPDiscoverResponse(BaseModel):
    server_id: str
    connected: bool
    tools: list[MCPToolRead] = Field(default_factory=list)
    error: str | None = None
