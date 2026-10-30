"""Gateway probe: one raw request to an agent's custom endpoint.

POST /api/agents/{id}/probe returns the status, headers and body exactly as
the gateway sent them (agent key scrubbed). It exists to capture how Agios
refuses a model or a tool, so the test platform can recognise it. Like every
POST under /api it sits behind the admin key when that is enabled.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_agent_or_404
from app.models.agent import Agent
from app.schemas.probe import ProbeRequest, ProbeResponse
from app.services.credentials import CredentialError
from app.services.gateway_probe import probe
from app.services.llm.registry import provider_class

router = APIRouter(prefix="/api/agents", tags=["probe"])


@router.post("/{agent_id}/probe", response_model=ProbeResponse)
async def probe_gateway(
    payload: ProbeRequest, agent: Agent = Depends(get_agent_or_404)
) -> ProbeResponse:
    if not provider_class(agent.provider).requires_base_url or not agent.base_url:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Probing is for agents on a custom endpoint (a LiteLLM base URL).",
        )
    try:
        return await probe(agent, payload)
    except CredentialError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from None
