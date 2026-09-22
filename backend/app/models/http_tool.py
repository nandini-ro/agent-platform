"""A tool defined at runtime through the UI, backed by an HTTP request.

Deliberately *not* code. A user-defined tool that could execute Python or shell
would hand any successful jailbreak remote code execution - which is precisely
the boundary this application exists to hold. An HTTP call is expressive enough
for most real tools and has a boundary that can actually be enforced.

Secrets are never stored here: a header value may reference an environment
variable as `{{env:NAME}}`, resolved from the backend process at call time. Same
principle as `mcp_servers.env_keys`.
"""

from datetime import datetime

from sqlalchemy import JSON, Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, created_column, pk_column, updated_column


class HTTPTool(Base):
    __tablename__ = "http_tools"

    id: Mapped[str] = pk_column()
    # The name the model sees; must satisfy the providers' tool-name rules.
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, default="")

    # JSON Schema for the arguments the model supplies.
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)

    method: Mapped[str] = mapped_column(String(10), default="GET")
    # May contain {param} placeholders, which are URL-encoded on substitution.
    url_template: Mapped[str] = mapped_column(String(2000), nullable=False)
    headers: Mapped[dict] = mapped_column(JSON, default=dict)
    query_template: Mapped[dict] = mapped_column(JSON, default=dict)
    body_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Optional dotted path into a JSON response, e.g. "data.items.0.title".
    response_path: Mapped[str | None] = mapped_column(String(200), nullable=True)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=10)

    created_at: Mapped[datetime] = created_column()
    updated_at: Mapped[datetime] = updated_column()
