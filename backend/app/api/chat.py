"""Chat entrypoints.

Two callers, one runtime:

* POST /api/conversations/{id}/messages - the UI. Persists both turns and
  streams the assistant reply over SSE.
* POST /api/agents/{id}/chat - stateless. The programmatic surface and the
  target Garak points at.

The public, per-agent-key variant of the stateless call is in app/api/public.py.
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
from app.models.attachment import Attachment
from app.models.conversation import Conversation
from app.models.message import Message
from app.schemas.chat import (
    AgentChatRequest,
    AgentChatResponse,
    SendMessageRequest,
)
from app.services.agent_runtime import AgentRuntime, RuntimeResult, ToolInvocation
from app.services.attachments import MAX_IMAGES_PER_REQUEST, message_blocks, summary
from app.services.llm.base import LLMMessage, ProviderNotConfigured

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])


def _load_history(
    db: Session, conversation_id: str, image_budget: int = MAX_IMAGES_PER_REQUEST
) -> list[LLMMessage]:
    """Prior turns as provider-neutral messages.

    Only answered questions are replayed, and only the visible text of each
    turn - tool round trips are recorded in message metadata for display but
    not resent, which keeps the context small and avoids stale tool-use ids.

    Attached documents are replayed as text. Attached images are replayed as
    images newest first, up to `image_budget`; older ones become a one-line
    placeholder, because providers cap the images in a single request.
    """
    rows = [
        m
        for m in db.scalars(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at)
        )
        if m.role in ("user", "assistant")
    ]
    # A question whose answer never arrived - the call failed, timed out, or
    # the client went away - is stored but not replayed. Left in, it sits
    # unanswered just before the next question, and models then answer it
    # instead of (or as well as) the new one.
    rows = [
        m
        for i, m in enumerate(rows)
        if m.role == "assistant"
        or (i + 1 < len(rows) and rows[i + 1].role == "assistant")
    ]
    files: dict[str, list[Attachment]] = {}
    for a in db.scalars(
        select(Attachment)
        .where(Attachment.message_id.in_([m.id for m in rows]))
        .order_by(Attachment.created_at)
    ):
        files.setdefault(a.message_id, []).append(a)

    shown: set[str] = set()
    for m in reversed(rows):
        for a in files.get(m.id, []):
            if a.kind == "image" and len(shown) < image_budget:
                shown.add(a.id)

    history = []
    for m in rows:
        attached = files.get(m.id, [])
        if not m.content and not attached:
            continue
        old = [a for a in attached if a.kind == "image" and a.id not in shown]
        current = [a for a in attached if a not in old]
        content = message_blocks(m.content, current)
        if old:
            placeholder = message_blocks("", old, include_images=False)
            content = (
                f"{content}\n\n{placeholder}".strip()
                if isinstance(content, str)
                else [*content, {"type": "text", "text": placeholder}]
            )
        history.append(LLMMessage(role=m.role, content=content))
    return history


def _claim(db: Session, ids: list[str]) -> list[Attachment]:
    """The unclaimed uploads with these ids, in the order given."""
    if not ids:
        return []
    found = {
        a.id: a
        for a in db.scalars(
            select(Attachment).where(
                Attachment.id.in_(ids), Attachment.message_id.is_(None)
            )
        )
    }
    missing = [i for i in ids if i not in found]
    if missing:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "An attachment was not found or was already sent. Attach it again.",
        )
    images = sum(found[i].kind == "image" for i in ids)
    if images > MAX_IMAGES_PER_REQUEST:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"At most {MAX_IMAGES_PER_REQUEST} images can be sent in one message.",
        )
    return [found[i] for i in dict.fromkeys(ids)]


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
    return await run_stateless(agent, payload.message)


async def run_stateless(agent: Agent, message: str) -> AgentChatResponse:
    """One turn with no stored history. Shared with the public endpoint
    (app/api/public.py), so both answer exactly as the UI's agent would."""
    runtime: AgentRuntime | None = None
    try:
        runtime = AgentRuntime(agent)
        result = await runtime.run(history=[], user_message=message)
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

    attachments = _claim(db, payload.attachment_ids)
    image_count = sum(a.kind == "image" for a in attachments)
    history = _load_history(
        db, conversation_id, image_budget=MAX_IMAGES_PER_REQUEST - image_count
    )

    text = payload.content.strip()
    user_message = Message(
        conversation_id=conversation_id,
        role="user",
        content=text,
        meta={"attachments": [summary(a) for a in attachments]} if attachments else {},
    )
    db.add(user_message)
    db.flush()
    for attachment in attachments:
        attachment.message_id = user_message.id
    # First user message names the conversation.
    if not history:
        conversation.title = (text or attachments[0].filename)[:60]
        db.add(conversation)
    db.commit()

    agent_id = agent.id
    # Built before streaming starts: the request's session closes once the
    # response begins.
    content = message_blocks(text, attachments)

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
                message = f"Agent failed: {_safe_detail(runtime, exc)}"
                if image_count and getattr(exc, "status_code", None) == 400:
                    message += (
                        " This agent's model may not accept images - choose a "
                        "vision model (on Groq: qwen/qwen3.8-27b), or attach "
                        "documents instead."
                    )
                yield {"event": "error", "data": json.dumps({"message": message})}
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
