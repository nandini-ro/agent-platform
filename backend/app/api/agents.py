"""Agent CRUD."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_agent_or_404
from app.db.session import get_db
from app.models.agent import Agent
from app.schemas.agent import AgentCreate, AgentRead, AgentUpdate, ProviderInfo
from app.services.llm.registry import list_providers

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
    agent = Agent(**payload.model_dump())
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
    changes = payload.model_dump(exclude_unset=True)
    if "provider" in changes:
        _validate_provider(changes["provider"])
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
