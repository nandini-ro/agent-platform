"""Shared API dependencies."""

from __future__ import annotations

import secrets

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.db.session import get_db
from app.models.agent import Agent


def get_agent_or_404(agent_id: str, db: Session = Depends(get_db)) -> Agent:
    agent = db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Agent {agent_id} not found")
    return agent


def require_agent_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    """Guard the programmatic agent endpoint.

    Unset AGENT_API_KEY leaves the endpoint open, which is the convenient
    default for a local POC. Once set, it is enforced with a constant-time
    comparison - this is the key Garak supplies via $KEY.
    """
    expected = get_settings().agent_api_key
    if not expected:
        return
    if not x_api_key or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing API key")
