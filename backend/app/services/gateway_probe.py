"""One raw request to an agent's gateway, reported exactly as it came back.

For finding out how a governance layer in front of LiteLLM (Agios) answers -
the status, headers and body of a refusal - so the test platform can be taught
to recognise it. Agios checks a tool on the request that carries the model's
call to it and the tool's result, so the probe can replay that round trip.

Deliberately not the OpenAI SDK: the SDK turns an error status into an
exception and hides headers. This sends one request with httpx, makes no
retry, and keeps everything the gateway returned, with the agent's key
scrubbed out.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx

from app.models.agent import Agent
from app.schemas.probe import ProbeRequest, ProbeResponse
from app.services.credentials import resolve_api_key

MAX_BODY_CHARS = 16_000

# Response headers never worth returning.
_DROPPED_HEADERS = {"set-cookie"}

_TOOL_CALL_ID = "call_probe_1"


def _client(timeout: float) -> httpx.AsyncClient:
    """The HTTP client for one probe. Tests replace this."""
    return httpx.AsyncClient(timeout=timeout)


def build_body(agent: Agent, request: ProbeRequest) -> dict[str, Any]:
    """The Chat Completions body the probe sends."""
    messages: list[dict[str, Any]] = []
    if agent.system_prompt:
        messages.append({"role": "system", "content": agent.system_prompt})
    messages.append({"role": "user", "content": request.prompt})
    call = request.tool_call
    if call is not None:
        messages.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": _TOOL_CALL_ID,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments),
                        },
                    }
                ],
            }
        )
        messages.append(
            {"role": "tool", "tool_call_id": _TOOL_CALL_ID, "content": call.result}
        )

    body: dict[str, Any] = {"model": agent.model, "messages": messages}
    names = request.tool_names()
    if names:
        body["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": f"Probe tool '{name}'.",
                    "parameters": {
                        "type": "object",
                        "properties": {"input": {"type": "string"}},
                    },
                },
            }
            for name in names
        ]
    if request.stream:
        body["stream"] = True
    if request.bypass_cache:
        body["cache"] = {"no-cache": True}
    return body


async def probe(agent: Agent, request: ProbeRequest) -> ProbeResponse:
    """Send one request to the agent's base URL and report the reply.

    Never raises for what the gateway does - a refusal, a timeout or a dropped
    connection is the finding, so it is returned in `status` or `error`.
    """
    api_key = resolve_api_key(agent)
    url = f"{agent.base_url}/chat/completions"
    body = build_body(agent, request)
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    def scrub(text: str) -> str:
        return text.replace(api_key, "[redacted]") if api_key else text

    result = ProbeResponse(url=url, sent=body)
    chunks: list[str] = []
    size = 0
    started = time.perf_counter()

    def elapsed() -> int:
        return int((time.perf_counter() - started) * 1000)

    try:
        async with asyncio.timeout(request.timeout_s):
            async with _client(request.timeout_s) as client:
                async with client.stream(
                    "POST", url, json=body, headers=headers
                ) as response:
                    result.headers_ms = elapsed()
                    result.status = response.status_code
                    result.headers = {
                        k: scrub(v)
                        for k, v in response.headers.items()
                        if k.lower() not in _DROPPED_HEADERS
                    }
                    async for chunk in response.aiter_text():
                        if result.first_chunk_ms is None:
                            result.first_chunk_ms = elapsed()
                        chunks.append(chunk)
                        size += len(chunk)
                        if size >= MAX_BODY_CHARS:
                            result.truncated = True
                            break
    except TimeoutError:
        result.error = f"No complete response within {request.timeout_s:g} s."
    except httpx.HTTPError as exc:
        result.error = scrub(f"{type(exc).__name__}: {exc}")
    result.total_ms = elapsed()

    text = "".join(chunks)
    if len(text) > MAX_BODY_CHARS:
        text, result.truncated = text[:MAX_BODY_CHARS], True
    result.body = scrub(text)
    if not request.stream and not result.truncated:
        try:
            result.json_body = json.loads(result.body)
        except ValueError:
            pass
    return result
