"""The custom endpoint provider: an agent's own base URL and optional key.

The wire tests swap the OpenAI SDK's HTTP transport for an in-process one, so
they see exactly what would be sent - URL, headers, body - with no network.
"""

import json

import httpx
import openai
import pytest

from app.services.llm.base import LLMMessage, ProviderNotConfigured
from app.services.llm.custom_provider import (
    CustomProvider,
    allowed_models,
    normalize_base_url,
)

BASE = "https://llm.example.com/v1"


def _completion(text="Hello from the endpoint."):
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 0,
        "model": "my-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 4, "total_tokens": 9},
    }


@pytest.fixture
def endpoint(monkeypatch):
    """Route every AsyncOpenAI client through a recorder. Yields the requests."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_completion())

    real = openai.AsyncOpenAI

    def patched(**kwargs):
        transport = httpx.MockTransport(handler)
        return real(**kwargs, http_client=httpx.AsyncClient(transport=transport))

    monkeypatch.setattr(openai, "AsyncOpenAI", patched)
    return seen


# -- the provider -----------------------------------------------------------


@pytest.mark.asyncio
async def test_sends_system_prompt_and_query_to_the_base_url(endpoint):
    provider = CustomProvider(api_key="sk-custom", base_url=BASE)
    result = await provider.generate(
        model="my-model",
        system="You are a financial analyst.",
        messages=[LLMMessage("user", "How is AAPL doing?")],
    )

    assert result.text == "Hello from the endpoint."
    (request,) = endpoint
    assert str(request.url) == f"{BASE}/chat/completions"
    assert request.headers["authorization"] == "Bearer sk-custom"
    body = json.loads(request.content)
    assert body["model"] == "my-model"
    assert body["messages"] == [
        {"role": "system", "content": "You are a financial analyst."},
        {"role": "user", "content": "How is AAPL doing?"},
    ]


@pytest.mark.asyncio
async def test_works_without_a_key(endpoint):
    provider = CustomProvider(api_key=None, base_url=BASE)
    assert provider.is_available()
    await provider.generate(model="m", system="", messages=[LLMMessage("user", "hi")])
    assert len(endpoint) == 1


@pytest.mark.asyncio
async def test_without_a_base_url_it_names_the_problem():
    provider = CustomProvider(api_key="k", base_url=None)
    with pytest.raises(ProviderNotConfigured, match="base URL"):
        await provider.generate(model="m", system="", messages=[LLMMessage("user", "hi")])


@pytest.mark.parametrize(
    "pasted",
    [
        "https://llm.example.com/v1",
        "https://llm.example.com/v1/",
        "  https://llm.example.com/v1/chat/completions ",
    ],
)
def test_base_url_is_accepted_however_it_is_pasted(pasted):
    assert normalize_base_url(pasted) == BASE


@pytest.mark.parametrize("bad", ["llm.example.com/v1", "ftp://llm.example.com", "https://"])
def test_non_http_base_urls_are_rejected(bad):
    with pytest.raises(ValueError):
        normalize_base_url(bad)


# -- through the API --------------------------------------------------------


@pytest.fixture
def custom_payload(agent_payload):
    return {**agent_payload, "provider": "custom", "model": "my-model", "base_url": BASE}


def test_a_custom_agent_needs_a_base_url(client, custom_payload):
    response = client.post("/api/agents", json={**custom_payload, "base_url": None})
    assert response.status_code == 422
    assert "base URL" in response.text


def test_a_custom_agent_needs_no_key(client, custom_payload):
    response = client.post("/api/agents", json=custom_payload)
    assert response.status_code == 201, response.text
    agent = response.json()
    assert agent["base_url"] == BASE
    assert agent["has_api_key"] is False


def test_a_custom_agents_key_is_stored_but_never_returned(client, custom_payload):
    response = client.post("/api/agents", json={**custom_payload, "api_key": "sk-secret"})
    assert response.status_code == 201, response.text
    assert response.json()["has_api_key"] is True
    assert "sk-secret" not in response.text
    assert "sk-secret" not in client.get(f"/api/agents/{response.json()['id']}").text


def test_switching_to_custom_requires_a_base_url(client, agent_payload):
    agent_id = client.post("/api/agents", json=agent_payload).json()["id"]
    response = client.patch(
        f"/api/agents/{agent_id}", json={"provider": "custom", "model": "m"}
    )
    assert response.status_code == 422


def test_the_agent_answers_through_its_endpoint(client, endpoint, custom_payload):
    created = client.post(
        "/api/agents", json={**custom_payload, "api_key": "sk-secret"}
    ).json()
    response = client.post(f"/api/agents/{created['id']}/chat", json={"message": "hi"})

    assert response.status_code == 200
    assert response.json()["response"] == "Hello from the endpoint."
    (request,) = endpoint
    assert str(request.url) == f"{BASE}/chat/completions"
    assert request.headers["authorization"] == "Bearer sk-secret"
    body = json.loads(request.content)
    assert body["messages"][0] == {
        "role": "system",
        "content": custom_payload["system_prompt"],
    }


# -- model independence -----------------------------------------------------

# What the gateway in production says when a key asks for a model it may not use.
_DENIED = {
    "error": {
        "message": "key not allowed to access model. This key can only access "
        "models=['Learning Agent', 'gemini/gemini-3.5-flash-lite', "
        "'gemini-3.5-flash-lite']. Tried to access gemini",
        "type": "key_model_access_denied",
        "param": "model",
        "code": "403",
    }
}


@pytest.fixture
def gateway(monkeypatch):
    """A LiteLLM-style gateway: refuses models outside the key's list, rejects
    the alias it lists but cannot route, and serves the rest. `serves` decides
    whether a blank model (the gateway's default) is accepted."""
    state = {"seen": [], "serves_default": True}
    allowed = {"Learning Agent", "gemini/gemini-3.5-flash-lite", "gemini-3.5-flash-lite"}

    def handler(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        state["seen"].append(model)
        if model == "" and state["serves_default"]:
            return httpx.Response(200, json=_completion())
        if model not in allowed:
            return httpx.Response(403, json=_DENIED)
        if model == "Learning Agent":
            return httpx.Response(400, json={"error": {"message": "Invalid model name"}})
        return httpx.Response(200, json=_completion())

    real = openai.AsyncOpenAI

    def patched(**kwargs):
        transport = httpx.MockTransport(handler)
        return real(
            **kwargs, http_client=httpx.AsyncClient(transport=transport)
        )

    monkeypatch.setattr(openai, "AsyncOpenAI", patched)
    return state


def test_allowed_models_are_read_from_the_refusal():
    exc = openai.PermissionDeniedError(
        json.dumps(_DENIED["error"]),
        response=httpx.Response(403, request=httpx.Request("POST", BASE)),
        body=_DENIED["error"],
    )
    assert allowed_models(exc) == [
        "Learning Agent",
        "gemini/gemini-3.5-flash-lite",
        "gemini-3.5-flash-lite",
    ]
    assert allowed_models(ValueError("models=['x']")) is None


@pytest.mark.asyncio
async def test_a_blank_model_lets_the_endpoint_choose(gateway):
    provider = CustomProvider(api_key="sk", base_url=BASE)
    result = await provider.generate(
        model="", system="", messages=[LLMMessage("user", "hi")]
    )
    assert result.text == "Hello from the endpoint."
    assert gateway["seen"] == [""]
    assert provider.resolved_model is None


@pytest.mark.asyncio
async def test_a_refused_model_falls_back_to_the_endpoint_default(gateway):
    provider = CustomProvider(api_key="sk", base_url=BASE)
    await provider.generate(
        model="gemini", system="", messages=[LLMMessage("user", "hi")]
    )
    assert gateway["seen"] == ["gemini", ""]
    assert provider.resolved_model == ""


@pytest.mark.asyncio
async def test_without_a_default_the_first_servable_listed_model_is_used(gateway):
    gateway["serves_default"] = False
    provider = CustomProvider(api_key="sk", base_url=BASE)
    await provider.generate(
        model="gemini", system="", messages=[LLMMessage("user", "hi")]
    )
    # The alias is listed but unroutable, so it is skipped.
    assert gateway["seen"] == ["gemini", "", "Learning Agent", "gemini/gemini-3.5-flash-lite"]
    assert provider.resolved_model == "gemini/gemini-3.5-flash-lite"

    # The rest of the turn goes straight to the model that worked.
    await provider.generate(
        model="gemini", system="", messages=[LLMMessage("user", "again")]
    )
    assert gateway["seen"][-1] == "gemini/gemini-3.5-flash-lite"


@pytest.mark.asyncio
async def test_other_errors_are_not_retried(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    real = openai.AsyncOpenAI
    monkeypatch.setattr(
        openai,
        "AsyncOpenAI",
        lambda **kw: real(
            **kw, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        ),
    )
    provider = CustomProvider(api_key="sk", base_url=BASE)
    with pytest.raises(openai.AuthenticationError):
        await provider.generate(model="m", system="", messages=[LLMMessage("user", "hi")])
    assert len(seen) == 1


def test_a_custom_agent_may_leave_the_model_blank(client, custom_payload):
    response = client.post("/api/agents", json={**custom_payload, "model": "  "})
    assert response.status_code == 201, response.text
    assert response.json()["model"] == ""


def test_other_providers_still_need_a_model(client, agent_payload):
    response = client.post("/api/agents", json={**agent_payload, "model": ""})
    assert response.status_code == 422
    assert "model is required" in response.text


def test_a_working_model_is_saved_on_the_agent(client, gateway, custom_payload):
    created = client.post(
        "/api/agents", json={**custom_payload, "model": "gemini"}
    ).json()
    response = client.post(f"/api/agents/{created['id']}/chat", json={"message": "hi"})

    assert response.status_code == 200, response.text
    assert response.json()["response"] == "Hello from the endpoint."
    assert response.json()["metadata"]["model"] == ""
    assert client.get(f"/api/agents/{created['id']}").json()["model"] == ""

    # The next turn is not refused first.
    gateway["seen"].clear()
    client.post(f"/api/agents/{created['id']}/chat", json={"message": "again"})
    assert gateway["seen"] == [""]


ENDPOINT_KEY = "sk-litellm-virtual-key-0123456789ab"


# The access key goes to outside callers (a scanner's E2E settings), so it must
# never be the endpoint key: that would let them call the gateway directly.


def test_the_endpoint_key_cannot_become_the_access_key(client, custom_payload):
    agent = client.post(
        "/api/agents", json={**custom_payload, "api_key": ENDPOINT_KEY}
    ).json()
    url = f"/api/agents/{agent['id']}/access-key"
    for body in ({"use_api_key": True}, {"access_key": ENDPOINT_KEY}):
        response = client.post(url, json=body)
        assert response.status_code == 422, response.text
        assert ENDPOINT_KEY not in response.text
    assert client.get(f"/api/agents/{agent['id']}").json()["has_access_key"] is False


def test_an_agent_cannot_be_created_with_its_endpoint_key_as_access_key(
    client, custom_payload
):
    response = client.post(
        "/api/agents",
        json={**custom_payload, "api_key": ENDPOINT_KEY, "access_key": ENDPOINT_KEY},
    )
    assert response.status_code == 422
    assert ENDPOINT_KEY not in response.text


def test_an_endpoint_key_set_as_access_key_earlier_is_not_revealed(
    client, custom_payload
):
    from app.api.agents import _set_access_key
    from app.db.session import SessionLocal
    from app.models.agent import Agent

    agent = client.post(
        "/api/agents", json={**custom_payload, "api_key": ENDPOINT_KEY}
    ).json()
    with SessionLocal() as session:  # as the removed reuse option left it
        row = session.get(Agent, agent["id"])
        _set_access_key(row, ENDPOINT_KEY)
        session.commit()

    url = f"/api/agents/{agent['id']}/access-key"
    reveal = client.post(f"{url}/reveal")
    assert reveal.status_code == 409
    assert ENDPOINT_KEY not in reveal.text
    # Rotating replaces it with a separate key, which can be shown.
    rotated = client.post(url).json()["access_key"]
    assert rotated.startswith("agk_")
    assert client.post(f"{url}/reveal").json()["access_key"] == rotated


# -- no hidden retries ---------------------------------------------------------


@pytest.mark.asyncio
async def test_a_failed_call_is_sent_once(monkeypatch):
    """The SDK retries twice by default. A retry resends a question the user
    may have given up on, and its late answer is what lands on the next one."""
    from app.config.settings import get_settings

    seen: list[httpx.Request] = []
    options: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(503, json={"error": {"message": "upstream down"}})

    real = openai.AsyncOpenAI

    def patched(**kwargs):
        options.update(kwargs)
        transport = httpx.MockTransport(handler)
        return real(**kwargs, http_client=httpx.AsyncClient(transport=transport))

    monkeypatch.setattr(openai, "AsyncOpenAI", patched)
    provider = CustomProvider(api_key="k", base_url=BASE)
    with pytest.raises(openai.APIStatusError):
        await provider.generate(
            model="m", system="", messages=[LLMMessage(role="user", content="hi")]
        )
    assert len(seen) == 1
    assert options["max_retries"] == 0
    assert options["timeout"] == get_settings().llm_timeout_seconds
