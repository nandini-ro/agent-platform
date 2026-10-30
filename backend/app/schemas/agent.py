from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.services.llm.custom_provider import normalize_base_url


def _clean_base_url(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return normalize_base_url(value)


class AgentBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    system_prompt: str = ""
    provider: str = "mock"
    # Blank is allowed only where the provider lets the endpoint choose
    # (`model_optional`); the API enforces that per provider.
    model: str = Field(default="mock-1", max_length=120)
    # The endpoint for the "custom" provider; ignored by the others.
    base_url: str | None = Field(default=None, max_length=500)
    temperature: float = Field(default=1.0, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2048, ge=1, le=64000)
    tools: list[str] = Field(default_factory=list)
    mcp_server_ids: list[str] = Field(default_factory=list)

    @field_validator("base_url")
    @classmethod
    def check_base_url(cls, value: str | None) -> str | None:
        return _clean_base_url(value)


class AgentCreate(AgentBase):
    # Write-only. SecretStr keeps it out of reprs and logs; it is encrypted
    # before storage and never returned.
    api_key: SecretStr | None = None
    # The public URL name. Absent derives one from the agent's name.
    slug: str | None = None
    # The key for that URL, so it can be shown before the agent exists.
    # Absent leaves the URL closed until a key is issued.
    access_key: str | None = None


class AgentUpdate(BaseModel):
    """All fields optional - PATCH semantics."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    system_prompt: str | None = None
    provider: str | None = None
    model: str | None = Field(default=None, max_length=120)
    base_url: str | None = Field(default=None, max_length=500)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=64000)
    tools: list[str] | None = None
    mcp_server_ids: list[str] | None = None
    # Blank or absent keeps the stored key; a value replaces it.
    api_key: SecretStr | None = None
    # Changing it moves the agent's public URL; the old one stops working.
    slug: str | None = None

    @field_validator("base_url")
    @classmethod
    def check_base_url(cls, value: str | None) -> str | None:
        return _clean_base_url(value)


class AgentRead(AgentBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    slug: str | None
    has_api_key: bool
    # The access key for the public URL is never returned after it is issued;
    # only whether one exists and its first characters.
    has_access_key: bool
    access_key_hint: str | None
    created_at: datetime
    updated_at: datetime


class AccessKeySet(BaseModel):
    """Body of POST /access-key. Without `access_key` a key is generated."""

    access_key: str | None = None
    # Formerly: reuse the agent's provider key as its access key. Now always
    # refused (it would hand the provider key to every caller); kept so an
    # old client gets that explanation rather than a silently generated key.
    use_api_key: bool = False


class AccessKeyIssued(BaseModel):
    """The agent's current key, returned when it is set or revealed."""

    access_key: str
    access_key_hint: str


class ProviderInfo(BaseModel):
    name: str
    requires_api_key: bool
    accepts_api_key: bool
    requires_base_url: bool
    model_optional: bool
    supports_tools: bool
    models: list[str]
