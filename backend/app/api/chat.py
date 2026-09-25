"""Chat entrypoints.

Two callers, one runtime:

* POST /api/conversations/{id}/messages - the UI. Persists both turns and
  streams the assistant reply over SSE.
* POST /api/agents/{id}/chat - stateless. The programmatic surface and the
  target Garak points at.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from app.api.deps import get_agent_or_404, require_agent_api_key
from app.db.session import SessionLocal, get_db
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.message import Message
from app.schemas.chat import (
    AgentChatRequest,
    AgentChatResponse,
    SendMessageRequest,
)
from app.services.agent_runtime import AgentRuntime, RuntimeResult, ToolInvocation
from app.services.llm.base import LLMMessage, ProviderNotConfigured

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])


def _load_history(db: Session, conversation_id: str) -> list[LLMMessage]:
    """Prior turns as provider-neutral messages.

    Only the visible text of each turn is replayed - tool round trips are
    recorded in message metadata for display but not resent, which keeps the
    context small and avoids stale tool-use ids.
    """
    rows = db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at)
    )
    return [
        LLMMessage(role=m.role, content=m.content)
        for m in rows
        if m.role in ("user", "assistant") and m.content
    ]


def _safe_detail(runtime: AgentRuntime | None, exc: Exception) -> str:
    """An exception's text with the agent's API key scrubbed out."""
    return runtime.redact(str(exc)) if runtime else str(exc)


# --------------------------------------------------------------------------
# Stateless agent endpoint (programmatic + Garak)
# --------------------------------------------------------------------------


@router.post(
    "/api/agents/{agent_id}/chat",
    response_model=AgentChatResponse,
    dependencies=[Depends(require_agent_api_key)],
)
async def agent_chat(
    payload: AgentChatRequest, agent: Agent = Depends(get_agent_or_404)
) -> AgentChatResponse:
    """Single-turn call against an agent, with no stored history.

    Returns HTTP 200 even when the runtime fails, carrying the reason in
    `metadata.error`. That is deliberate: Garak's REST generator treats any 4xx
    as a fatal ConnectionError and aborts the whole scan, so a transient
    provider failure mid-run would throw away every result collected so far.
    Caller-side mistakes (unknown agent, bad API key) still return real error
    codes, because those should fail fast rather than be scored as output.
    """
    runtime: AgentRuntime | None = None
    try:
        runtime = AgentRuntime(agent)
        result = await runtime.run(history=[], user_message=payload.message)
    except ProviderNotConfigured as exc:
        logger.warning("agent %s: provider not configured: %s", agent.id, exc)
        return AgentChatResponse(
            agent_id=agent.id,
            response=f"[error] {exc}",
            metadata={"error": "provider_not_configured", "detail": str(exc)},
        )
    except Exception as exc:  # noqa: BLE001 - keep long scans alive
        logger.exception("agent %s: runtime failure", agent.id)
        return AgentChatResponse(
            agent_id=agent.id,
            response="[error] The agent failed to produce a response.",
            metadata={"error": "runtime_failure", "detail": _safe_detail(runtime, exc)},
        )

    return AgentChatResponse(
        agent_id=agent.id,
        response=result.text,
        tool_calls=[t.to_dict() for t in result.tool_calls],
        metadata=result.metadata(),
    )


# --------------------------------------------------------------------------
# Streaming chat for the UI
# --------------------------------------------------------------------------


@router.post("/api/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: str,
    payload: SendMessageRequest,
    db: Session = Depends(get_db),
) -> EventSourceResponse:
    conversation = db.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    agent = db.get(Agent, conversation.agent_id)
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent not found")

    history = _load_history(db, conversation_id)

    user_message = Message(
        conversation_id=conversation_id, role="user", content=payload.content
    )
    db.add(user_message)
    # First user message names the conversation.
    if not history:
        conversation.title = payload.content[:60]
        db.add(conversation)
    db.commit()

    agent_id = agent.id
    content = payload.content

    async def event_stream():
        # The request-scoped session is closed once the response starts
        # streaming, so the generator opens its own.
        session = SessionLocal()
        try:
            live_agent = session.get(Agent, agent_id)
            runtime: AgentRuntime | None = None
            final: RuntimeResult | None = None
            try:
                runtime = AgentRuntime(live_agent)
                async for item in runtime.stream(history, content):
                    if isinstance(item, ToolInvocation):
                        yield {"event": "tool", "data": json.dumps(item.to_dict())}
                    elif isinstance(item, RuntimeResult):
                        final = item
                    elif item.type == "text" and item.text:
                        yield {"event": "token", "data": json.dumps({"text": item.text})}
            except ProviderNotConfigured as exc:
                yield {"event": "error", "data": json.dumps({"message": str(exc)})}
                return
            except Exception as exc:  # noqa: BLE001
                logger.exception("stream failed for conversation %s", conversation_id)
                yield {
                    "event": "error",
                    "data": json.dumps(
                        {"message": f"Agent failed: {_safe_detail(runtime, exc)}"}
                    ),
                }
                return

            if final is None:
                yield {
                    "event": "error",
                    "data": json.dumps({"message": "No response produced."}),
                }
                return

            assistant = Message(
                conversation_id=conversation_id,
                role="assistant",
                content=final.text,
                meta={
                    "tool_calls": [t.to_dict() for t in final.tool_calls],
                    **final.metadata(),
                },
            )
            session.add(assistant)
            session.commit()
            session.refresh(assistant)
            yield {
                "event": "done",
                "data": json.dumps(
                    {
                        "message_id": assistant.id,
                        "content": final.text,
                        "metadata": final.metadata(),
                        "tool_calls": [t.to_dict() for t in final.tool_calls],
                    }
                ),
            }
        finally:
            session.close()

    return EventSourceResponse(event_stream())
