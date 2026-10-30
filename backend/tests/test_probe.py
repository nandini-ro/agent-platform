"""The gateway probe: one raw request, reported as the gateway answered it."""

import asyncio
import json

import httpx
import pytest

from app.services import gateway_probe

KEY = "sk-litellm-probe-key-0123456789abcdef"

# Shape is illustrative: the real Agios body is what the probe is for.
_BLOCKED = {
    "error": {
        "message": f"Tool 'terminal_exec' is not allowed (key {KEY})",
        "type": "policy_violation",
        "code": "403",
    }
}


@pytest.fixture
def gateway(monkeypatch):
    """Route probes through a scripted handler. Yields the requests seen."""
    state = {"seen": [], "handler": None}

    def handler(request: httpx.Request) -> httpx.Response:
        state["seen"].append(request)
        return state["handler"](request)

    def client(timeout):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=timeout)

    monkeypatch.setattr(gateway_probe, "_client", client)
    return state


@pytest.fixture
def agent(client):
    return client.post(
        "/api/agents",
        json={
            "name": "Governed",
            "system_prompt": "Be brief.",
            "provider": "custom",
            "base_url": "https://litellm.example.com",
            "api_key": KEY,
            "model": "gemini/gemini-3.5-flash-lite",
        },
    ).json()


def _probe(client, agent, **body):
    return client.post(f"/api/agents/{agent['id']}/probe", json=body)


def test_a_tool_round_trip_is_replayed_and_the_refusal_kept(client, agent, gateway):
    gateway["handler"] = lambda r: httpx.Response(
        403, json=_BLOCKED, headers={"x-litellm-call-id": "abc", "set-cookie": "s=1"}
    )
    response = _probe(
        client,
        agent,
        prompt="List the files",
        tool_call={"name": "terminal_exec", "arguments": {"input": "ls"}},
    )
    assert response.status_code == 200, response.text
    report = response.json()

    # What was sent: the round trip Agios checks, with the key only in a header.
    [request] = gateway["seen"]  # exactly one attempt
    assert request.url == "https://litellm.example.com/chat/completions"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    sent = json.loads(request.content)
    assert [m["role"] for m in sent["messages"]] == ["system", "user", "assistant", "tool"]
    assert sent["messages"][2]["tool_calls"][0]["function"]["name"] == "terminal_exec"
    assert [t["function"]["name"] for t in sent["tools"]] == ["terminal_exec"]
    assert sent["cache"] == {"no-cache": True}
    assert report["sent"] == sent

    # What came back, intact except for the key.
    assert report["status"] == 403
    assert report["headers"]["x-litellm-call-id"] == "abc"
    assert "set-cookie" not in report["headers"]
    assert report["json_body"]["error"]["type"] == "policy_violation"
    assert KEY not in response.text
    assert "[redacted]" in report["body"]


def test_a_stream_is_returned_raw(client, agent, gateway):
    sse = 'data: {"choices":[{"delta":{"content":"hi"}}]}\n\ndata: [DONE]\n\n'
    gateway["handler"] = lambda r: httpx.Response(
        200, text=sse, headers={"content-type": "text/event-stream"}
    )
    report = _probe(client, agent, stream=True).json()
    assert json.loads(gateway["seen"][0].content)["stream"] is True
    assert report["status"] == 200
    assert report["body"] == sse
    assert report["json_body"] is None
    assert report["first_chunk_ms"] is not None


def test_a_gateway_that_never_answers_is_reported_not_raised(client, agent, gateway):
    def slow(request):
        raise httpx.ReadTimeout("read timed out", request=request)

    gateway["handler"] = slow
    report = _probe(client, agent, timeout_s=1).json()
    assert report["status"] is None
    assert "ReadTimeout" in report["error"]
    assert len(gateway["seen"]) == 1


@pytest.mark.asyncio
async def test_the_overall_deadline_holds(monkeypatch):
    class Hanging(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            await asyncio.sleep(10)

    monkeypatch.setattr(
        gateway_probe,
        "_client",
        lambda timeout: httpx.AsyncClient(transport=Hanging()),
    )

    class _Agent:
        system_prompt = ""
        model = "m"
        base_url = "https://litellm.example.com"
        api_key_encrypted = None
        provider = "custom"

    from app.schemas.probe import ProbeRequest

    report = await gateway_probe.probe(_Agent(), ProbeRequest(timeout_s=0.2))
    assert report.error == "No complete response within 0.2 s."
    assert report.total_ms < 2000


def test_only_custom_endpoint_agents_can_be_probed(client, agent_payload):
    mock_agent = client.post("/api/agents", json=agent_payload).json()
    response = _probe(client, mock_agent)
    assert response.status_code == 422


def test_tool_names_are_checked(client, agent, gateway):
    response = _probe(client, agent, tool_call={"name": "rm -rf /"})
    assert response.status_code == 422
    assert gateway["seen"] == []
