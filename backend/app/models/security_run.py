"""Record of one Garak security-testing run against an agent."""

from datetime import datetime

from sqlalchemy import JSON, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, created_column, pk_column


class SecurityRun(Base):
    __tablename__ = "security_runs"

    id: Mapped[str] = pk_column()
    agent_id: Mapped[str] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # queued | running | completed | failed
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    probes: Mapped[str] = mapped_column(String(500), default="")
    generations: Mapped[int] = mapped_column(Integer, default=1)

    report_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    log_tail: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = created_column()
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
