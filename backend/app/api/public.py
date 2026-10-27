"""The public, per-agent endpoint.

    POST /v1/agents/{slug}/chat
    Authorization: Bearer agk_...        (or X-API-Key: agk_...)

This is the URL to hand to someone who should be able to talk to one agent.
It sits outside /api on purpose: the admin key gate in main.py covers /api,
and this route is guarded by the agent's own access key instead. Holding that
key lets a caller run this agent - nothing more. The admin key is not
accepted here, so it never has to leave the administrator's hands.

An agent with no access key cannot be called here at all. There is no open
default, unlike the local-checkout convenience on /api: this is the route that
is meant to face the internet.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.chat import run_stateless
from app.db.session import get_db
from app.models.agent import Agent
from app.schemas.chat import AgentChatRequest, AgentChatResponse
from app.services.agent_access import access_key_matches

router = APIRouter(prefix="/v1", tags=["public"])

_CHALLENGE = {"WWW-Authenticate": "Bearer"}


def get_authorised_agent(
    agent_ref: str,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    db: Session = Depends(get_db),
) -> Agent:
    """The agent named by slug (or id), if the caller holds its key."""
    agent = db.scalar(select(Agent).where(Agent.slug == agent_ref)) or db.get(
        Agent, agent_ref
    )
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Agent '{agent_ref}' not found")
    if not agent.has_access_key:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "This agent has no access key, so its public URL is closed. "
            "Generate one in the agent's settings.",
        )

    supplied = x_api_key
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() == "bearer" and token.strip():
        supplied = token.strip()
    if not access_key_matches(agent.access_key_hash, supplied):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid or missing access key for this agent.",
            headers=_CHALLENGE,
        )
    return agent


@router.post("/agents/{agent_ref}/chat", response_model=AgentChatResponse)
async def public_agent_chat(
    payload: AgentChatRequest, agent: Agent = Depends(get_authorised_agent)
) -> AgentChatResponse:
    """Send one message to an agent and get its reply. No history is kept."""
    return await run_stateless(agent, payload.message)
