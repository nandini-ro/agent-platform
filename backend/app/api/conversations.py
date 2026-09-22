"""Conversation and message history."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.message import Message
from app.schemas.conversation import (
    ConversationCreate,
    ConversationRead,
    MessageRead,
)

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


@router.get("", response_model=list[ConversationRead])
def list_conversations(
    agent_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[Conversation]:
    stmt = select(Conversation).order_by(Conversation.created_at.desc())
    if agent_id:
        stmt = stmt.where(Conversation.agent_id == agent_id)
    return list(db.scalars(stmt))


@router.post("", response_model=ConversationRead, status_code=status.HTTP_201_CREATED)
def create_conversation(
    payload: ConversationCreate, db: Session = Depends(get_db)
) -> Conversation:
    if db.get(Agent, payload.agent_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent not found")
    conversation = Conversation(
        agent_id=payload.agent_id, title=payload.title or "New conversation"
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


@router.get("/{conversation_id}", response_model=ConversationRead)
def read_conversation(
    conversation_id: str, db: Session = Depends(get_db)
) -> Conversation:
    return _get_or_404(db, conversation_id)


@router.get("/{conversation_id}/messages", response_model=list[MessageRead])
def list_messages(
    conversation_id: str, db: Session = Depends(get_db)
) -> list[Message]:
    _get_or_404(db, conversation_id)
    return list(
        db.scalars(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at)
        )
    )


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(conversation_id: str, db: Session = Depends(get_db)) -> Response:
    db.delete(_get_or_404(db, conversation_id))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _get_or_404(db: Session, conversation_id: str) -> Conversation:
    conversation = db.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    return conversation
