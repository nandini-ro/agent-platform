"""Agent configuration.

An agent is pure data. AgentRuntime reads these rows; there is no per-agent
code anywhere in the codebase.
"""

from datetime import datetime

from sqlalchemy import JSON, Connection, Float, Integer, String, Text, event, or_, select
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, created_column, pk_column, updated_column
from app.services.agent_access import first_free_slug, slugify_name


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[str] = pk_column()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    system_prompt: Mapped[str] = mapped_column(Text, default="")

    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    # Only for the "custom" provider: the OpenAI-compatible endpoint to call.
    base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    temperature: Mapped[float] = mapped_column(Float, default=1.0)
    max_tokens: Mapped[int] = mapped_column(Integer, default=2048)

    # Allowlists. An empty list means "no access" - never "all access".
    tools: Mapped[list] = mapped_column(JSON, default=list)
    mcp_server_ids: Mapped[list] = mapped_column(JSON, default=list)

    # The provider API key, encrypted (app/services/credentials.py) and bound
    # to the provider it was entered for. Write-only: the API never returns
    # either column, only `has_api_key`.
    api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    api_key_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # The agent's public address, /v1/agents/{slug}/chat. Derived from the name
    # on insert when not given. Nullable only so that existing databases can
    # gain the column; init_db() fills it in for older rows.
    slug: Mapped[str | None] = mapped_column(
        String(80), nullable=True, unique=True, index=True
    )

    # The key for that public address (app/services/agent_access.py). Calls
    # are checked against the digest; the hint is the key's first characters.
    access_key_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    access_key_hint: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # The same key encrypted like provider keys, so the agent's settings can
    # show it again. Null for keys issued before this existed.
    access_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = created_column()
    updated_at: Mapped[datetime] = updated_column()

    @property
    def has_api_key(self) -> bool:
        """A key is stored for the *current* provider. One saved for a provider
        the agent has since switched away from does not count."""
        return bool(self.api_key_encrypted) and self.api_key_provider == self.provider

    @property
    def has_access_key(self) -> bool:
        return bool(self.access_key_hash)


def next_free_slug(connection: Connection, name: str) -> str:
    """A slug derived from `name` that no other agent holds."""
    base = slugify_name(name)
    taken = set(
        connection.scalars(
            select(Agent.slug).where(
                or_(Agent.slug == base, Agent.slug.like(f"{base}-%"))
            )
        )
    )
    return first_free_slug(base, taken)


@event.listens_for(Agent, "before_insert")
def _assign_slug(_mapper, connection: Connection, target: Agent) -> None:
    # Here rather than in the API so that every insert path - seed.py
    # included - gets an address.
    if target.slug is None:
        target.slug = next_free_slug(connection, target.name)
