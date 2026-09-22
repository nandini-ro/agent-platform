"""MCP server configuration.

Two transports, one row shape. A `stdio` server is launched as a local
subprocess (`command` + `args`); an `http` server is a remote endpoint reached
over streamable HTTP (`url`), which needs no local process and no code on this
machine.

Secrets are never stored here. `env_keys` holds the *names* of environment
variables a subprocess needs; `headers` values may reference one as
`{{env:NAME}}`. Both are resolved from the backend process at connection time,
so a value never touches the database or any API response.
"""

from datetime import datetime

from sqlalchemy import JSON, Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, created_column, pk_column, updated_column


class MCPServer(Base):
    __tablename__ = "mcp_servers"

    id: Mapped[str] = pk_column()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # Short identifier used to namespace this server's tools for the model.
    slug: Mapped[str] = mapped_column(String(60), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, default="")

    # "stdio" (local subprocess) or "http" (remote streamable-HTTP endpoint).
    transport: Mapped[str] = mapped_column(String(20), default="stdio")

    # -- stdio --
    command: Mapped[str] = mapped_column(String(500), default="")
    args: Mapped[list] = mapped_column(JSON, default=list)
    cwd: Mapped[str | None] = mapped_column(String(500), nullable=True)
    env_keys: Mapped[list] = mapped_column(JSON, default=list)

    # -- http --
    url: Mapped[str] = mapped_column(String(2000), default="")
    # Sent on every request. Values may reference a secret as {{env:NAME}}.
    headers: Mapped[dict] = mapped_column(JSON, default=dict)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = created_column()
    updated_at: Mapped[datetime] = updated_column()
