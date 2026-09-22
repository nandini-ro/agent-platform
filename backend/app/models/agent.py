"""Agent configuration.

An agent is pure data. AgentRuntime reads these rows; there is no per-agent
code anywhere in the codebase.
"""

from datetime import datetime

from sqlalchemy import JSON, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, created_column, pk_column, updated_column


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[str] = pk_column()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    system_prompt: Mapped[str] = mapped_column(Text, default="")

    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    temperature: Mapped[float] = mapped_column(Float, default=1.0)
    max_tokens: Mapped[int] = mapped_column(Integer, default=2048)

    # Allowlists. An empty list means "no access" - never "all access".
    tools: Mapped[list] = mapped_column(JSON, default=list)
    mcp_server_ids: Mapped[list] = mapped_column(JSON, default=list)

    created_at: Mapped[datetime] = created_column()
    updated_at: Mapped[datetime] = updated_column()
