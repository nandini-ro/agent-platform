"""Agent CRUD.

Each agent carries its own provider API key. It is accepted on create and
update, encrypted before it is stored, and never returned: responses expose
only `has_api_key`.

Each agent also has a public address and an access key for it; see
app/api/public.py. Issuing and revoking that key live here, behind the admin
key like every other write.
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
from app.schemas.agent import (
    AccessKeyIssued,
    AgentCreate,
    AgentRead,
    AgentUpdate,
    ProviderInfo,
)
from app.services.agent_access import generate_access_key, slug_problem
from app.services.credentials import (
    CredentialError,
    clean_api_key,
    decrypt_api_key,
    encrypt_api_key,
)
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
    _require_base_url(payload.provider, payload.base_url)
    payload.model = _check_model(payload.provider, payload.model)
    api_key = _clean(payload.api_key)
    if api_key is None:
        _require_key(payload.provider)
    if payload.slug is not None:
        _check_slug(db, payload.slug)
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
    if changes.get("slug") is None:
        changes.pop("slug", None)  # an agent always keeps an address
    elif changes["slug"] != agent.slug:
        _check_slug(db, changes["slug"], exclude_id=agent.id)
    provider = changes.get("provider", agent.provider)
    _require_base_url(provider, changes.get("base_url", agent.base_url))
    model = _check_model(provider, changes.get("model", agent.model))
    if "model" in changes or model != agent.model:
        changes["model"] = model
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


@router.post(
    "/{agent_id}/access-key",
    response_model=AccessKeyIssued,
    status_code=status.HTTP_201_CREATED,
)
def issue_access_key(
    agent: Agent = Depends(get_agent_or_404), db: Session = Depends(get_db)
) -> AccessKeyIssued:
    """Generate the key for this agent's public URL, replacing any old one."""
    key, digest, hint = generate_access_key()
    agent.access_key_hash = digest
    agent.access_key_hint = hint
    try:
        agent.access_key_encrypted = encrypt_api_key(
            key, agent_id=agent.id, provider=_ACCESS_KEY_SCOPE
        )
    except CredentialError:
        # No encryption key configured: the key still works, it just cannot
        # be shown again after this response.
        agent.access_key_encrypted = None
    db.add(agent)
    db.commit()
    return AccessKeyIssued(access_key=key, access_key_hint=hint)


@router.delete("/{agent_id}/access-key", status_code=status.HTTP_204_NO_CONTENT)
def revoke_access_key(
    agent: Agent = Depends(get_agent_or_404), db: Session = Depends(get_db)
) -> Response:
    """Close the public URL. It refuses every call until a new key is issued."""
    agent.access_key_hash = None
    agent.access_key_hint = None
    agent.access_key_encrypted = None
    db.add(agent)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# Bound into the ciphertext, so an access key can never be decrypted as a
# provider key or the other way round.
_ACCESS_KEY_SCOPE = "public-access-key"


# A POST rather than a GET on purpose: the admin key gate covers writes only,
# and showing a secret must be gated whenever writes are.
@router.post("/{agent_id}/access-key/reveal", response_model=AccessKeyIssued)
def reveal_access_key(agent: Agent = Depends(get_agent_or_404)) -> AccessKeyIssued:
    """The current access key, for showing in the agent's settings."""
    if not agent.has_access_key:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This agent has no access key.")
    if not agent.access_key_encrypted:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This key was issued before keys could be shown again. Rotate it "
            "to get one that can be.",
        )
    try:
        key = decrypt_api_key(
            agent.access_key_encrypted, agent_id=agent.id, provider=_ACCESS_KEY_SCOPE
        )
    except CredentialError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from None
    return AccessKeyIssued(access_key=key, access_key_hint=agent.access_key_hint or "")


def _check_slug(db: Session, slug: str, exclude_id: str | None = None) -> None:
    problem = slug_problem(slug)
    if problem:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, problem)
    holder = db.scalar(select(Agent.id).where(Agent.slug == slug))
    if holder is not None and holder != exclude_id:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Another agent already uses '{slug}'."
        )


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


def _require_base_url(provider: str, base_url: str | None) -> None:
    if provider_class(provider).requires_base_url and not base_url:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "A base URL is required for a custom endpoint.",
        )


def _check_model(provider: str, model: str | None) -> str:
    """The model, trimmed. Blank only where the endpoint picks one."""
    model = (model or "").strip()
    if not model and not provider_class(provider).model_optional:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"A model is required for '{provider}'.",
        )
    return model


def _require_key(provider: str) -> None:
    if provider_class(provider).requires_api_key:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"An API key is required before this agent can use '{provider}'.",
        )


def _store_key(agent: Agent, api_key: str, provider: str) -> None:
    """Encrypt and attach a key, recording which provider it belongs to."""
    if not provider_class(provider).accepts_api_key:
        return  # e.g. mock: nothing to store, and nothing to leak
    try:
        agent.api_key_encrypted = encrypt_api_key(
            api_key, agent_id=agent.id, provider=provider
        )
    except CredentialError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from None
    agent.api_key_provider = provider
