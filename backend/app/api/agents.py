"""Agent CRUD.

Each agent carries its own provider API key. It is accepted on create and
update, encrypted before it is stored, and never returned: responses expose
only `has_api_key`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_agent_or_404
from app.db.base import new_id
from app.db.session import get_db
from app.models.agent import Agent
from app.schemas.agent import AgentCreate, AgentRead, AgentUpdate, ProviderInfo
from app.services.credentials import CredentialError, clean_api_key, encrypt_api_key
from app.services.llm.registry import list_providers, provider_class

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("/providers", response_model=list[ProviderInfo])
def get_providers() -> list[dict]:
    """Providers and their availability, for the agent form."""
    return list_providers()


@router.get("", response_model=list[AgentRead])
def list_agents(db: Session = Depends(get_db)) -> list[Agent]:
    return list(db.scalars(select(Agent).order_by(Agent.created_at.desc())))


@router.post("", response_model=AgentRead, status_code=status.HTTP_201_CREATED)
def create_agent(payload: AgentCreate, db: Session = Depends(get_db)) -> Agent:
    _validate_provider(payload.provider)
    api_key = _clean(payload.api_key)
    if api_key is None:
        _require_key(payload.provider)
    # The id is fixed up front because the ciphertext is bound to it.
    agent = Agent(id=new_id(), **payload.model_dump(exclude={"api_key"}))
    if api_key is not None:
        _store_key(agent, api_key, payload.provider)
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return agent


@router.get("/{agent_id}", response_model=AgentRead)
def read_agent(agent: Agent = Depends(get_agent_or_404)) -> Agent:
    return agent


@router.patch("/{agent_id}", response_model=AgentRead)
def update_agent(
    payload: AgentUpdate,
    agent: Agent = Depends(get_agent_or_404),
    db: Session = Depends(get_db),
) -> Agent:
    changes = payload.model_dump(exclude_unset=True, exclude={"api_key"})
    if "provider" in changes:
        _validate_provider(changes["provider"])
    provider = changes.get("provider", agent.provider)
    api_key = _clean(payload.api_key)
    if api_key is not None:
        _store_key(agent, api_key, provider)
    elif provider != agent.provider and not (
        agent.api_key_encrypted and agent.api_key_provider == provider
    ):
        # Switching provider: the stored key belongs to the old one and is
        # never reused. An unchanged provider keeps working as before, so an
        # agent created before per-agent keys can still be edited.
        _require_key(provider)
    for key, value in changes.items():
        setattr(agent, key, value)
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return agent


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_agent(
    agent: Agent = Depends(get_agent_or_404), db: Session = Depends(get_db)
) -> Response:
    db.delete(agent)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _validate_provider(name: str) -> None:
    known = {p["name"] for p in list_providers()}
    if name not in known:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Unknown provider '{name}'. Known providers: {sorted(known)}",
        )


def _clean(raw: SecretStr | None) -> str | None:
    try:
        return clean_api_key(raw.get_secret_value() if raw is not None else None)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None


def _require_key(provider: str) -> None:
    if provider_class(provider).requires_api_key:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"An API key is required before this agent can use '{provider}'.",
        )


def _store_key(agent: Agent, api_key: str, provider: str) -> None:
    """Encrypt and attach a key, recording which provider it belongs to."""
    if not provider_class(provider).requires_api_key:
        return  # e.g. mock: nothing to store, and nothing to leak
    try:
        agent.api_key_encrypted = encrypt_api_key(
            api_key, agent_id=agent.id, provider=provider
        )
    except CredentialError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from None
    agent.api_key_provider = provider
