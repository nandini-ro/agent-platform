"""Per-agent provider API keys: storage, exposure, update semantics, runtime use."""

import pytest

from app.db.session import SessionLocal
from app.models.agent import Agent
from app.services import agent_runtime as runtime_module
from app.services.agent_runtime import AgentRuntime
from app.services.credentials import (
    CredentialError,
    decrypt_api_key,
    encrypt_api_key,
    generate_master_key,
)
from app.services.llm.base import LLMResponse, ProviderNotConfigured, StreamEvent
from app.services.llm.gemini_provider import GeminiProvider
from app.services.llm.groq_provider import GroqProvider
from app.services.llm.registry import get_provider

GROQ_KEY = "gsk_test_groq_key_0123456789abcdef"
GEMINI_KEY = "AIzaSy_test_gemini_key_0123456789"


@pytest.fixture
def groq_payload(agent_payload):
    return {
        **agent_payload,
        "provider": "groq",
        "model": "openai/gpt-oss-120b",
        "api_key": GROQ_KEY,
    }


def _row(agent_id: str) -> Agent:
    with SessionLocal() as session:
        return session.get(Agent, agent_id)


def _create(client, payload) -> dict:
    response = client.post("/api/agents", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


# -- create -------------------------------------------------------------------


def test_create_with_key_reports_it_configured_without_returning_it(client, groq_payload):
    response = client.post("/api/agents", json=groq_payload)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["has_api_key"] is True
    assert body["provider"] == "groq"
    assert GROQ_KEY not in response.text
    assert "api_key" not in body
    assert "api_key_encrypted" not in body


def test_key_is_encrypted_in_the_database(client, groq_payload):
    agent_id = _create(client, groq_payload)["id"]
    row = _row(agent_id)
    assert row.api_key_encrypted
    assert GROQ_KEY not in row.api_key_encrypted
    assert row.api_key_encrypted.startswith("v1:")
    assert row.api_key_provider == "groq"
    assert decrypt_api_key(row.api_key_encrypted, agent_id=agent_id, provider="groq") == GROQ_KEY


def test_same_key_encrypts_differently_each_time():
    a = encrypt_api_key(GROQ_KEY, agent_id="a", provider="groq")
    b = encrypt_api_key(GROQ_KEY, agent_id="a", provider="groq")
    assert a != b  # random nonce


def test_get_and_list_never_expose_the_key_or_ciphertext(client, groq_payload):
    agent_id = _create(client, groq_payload)["id"]
    ciphertext = _row(agent_id).api_key_encrypted
    for response in (client.get(f"/api/agents/{agent_id}"), client.get("/api/agents")):
        assert response.status_code == 200
        assert GROQ_KEY not in response.text
        assert ciphertext not in response.text
        assert "api_key_encrypted" not in response.text
    assert client.get(f"/api/agents/{agent_id}").json()["has_api_key"] is True


def test_keyed_provider_without_a_key_is_rejected(client, groq_payload):
    for missing in ({}, {"api_key": ""}, {"api_key": "   "}):
        payload = {k: v for k, v in groq_payload.items() if k != "api_key"} | missing
        response = client.post("/api/agents", json=payload)
        assert response.status_code == 422
        assert "An API key is required before this agent can use 'groq'" in response.text


def test_mock_needs_no_key(client, agent_payload):
    body = _create(client, agent_payload)
    assert body["has_api_key"] is False


def test_malformed_key_is_rejected_without_echoing_it(client, groq_payload):
    bad = "gsk_secret with spaces"
    response = client.post("/api/agents", json={**groq_payload, "api_key": bad})
    assert response.status_code == 422
    assert bad not in response.text
    assert "gsk_secret" not in response.text


def test_missing_master_key_fails_safely(client, groq_payload, monkeypatch):
    from app.config.settings import get_settings

    monkeypatch.setattr(get_settings(), "credential_encryption_key", None)
    response = client.post("/api/agents", json=groq_payload)
    assert response.status_code == 503
    assert "CREDENTIAL_ENCRYPTION_KEY" in response.text
    assert GROQ_KEY not in response.text


def test_generated_master_key_is_accepted(monkeypatch):
    from app.config.settings import get_settings

    monkeypatch.setattr(get_settings(), "credential_encryption_key", generate_master_key())
    token = encrypt_api_key(GROQ_KEY, agent_id="x", provider="groq")
    assert decrypt_api_key(token, agent_id="x", provider="groq") == GROQ_KEY


# -- update -------------------------------------------------------------------


@pytest.mark.parametrize("patch", [{}, {"api_key": ""}, {"api_key": None}, {"api_key": "  "}])
def test_update_without_a_new_key_keeps_the_old_one(client, groq_payload, patch):
    agent_id = _create(client, groq_payload)["id"]
    before = _row(agent_id).api_key_encrypted

    response = client.patch(
        f"/api/agents/{agent_id}", json={"name": "Renamed", "provider": "groq", **patch}
    )
    assert response.status_code == 200, response.text
    assert response.json()["has_api_key"] is True
    assert _row(agent_id).api_key_encrypted == before


def test_update_with_a_new_key_replaces_it(client, groq_payload):
    agent_id = _create(client, groq_payload)["id"]
    before = _row(agent_id).api_key_encrypted

    response = client.patch(f"/api/agents/{agent_id}", json={"api_key": "gsk_replacement"})
    assert response.status_code == 200
    assert "gsk_replacement" not in response.text
    row = _row(agent_id)
    assert row.api_key_encrypted != before
    assert decrypt_api_key(row.api_key_encrypted, agent_id=agent_id, provider="groq") == (
        "gsk_replacement"
    )


def test_switching_provider_without_a_key_is_refused(client, groq_payload):
    agent_id = _create(client, groq_payload)["id"]
    response = client.patch(
        f"/api/agents/{agent_id}", json={"provider": "gemini", "model": "gemini-3.5-flash"}
    )
    assert response.status_code == 422
    assert "An API key is required before this agent can use 'gemini'" in response.text
    row = _row(agent_id)
    assert row.provider == "groq"  # nothing was applied
    assert row.api_key_provider == "groq"


def test_switching_provider_with_a_key_binds_it_to_the_new_provider(client, groq_payload):
    agent_id = _create(client, groq_payload)["id"]
    response = client.patch(
        f"/api/agents/{agent_id}",
        json={"provider": "gemini", "model": "gemini-3.5-flash", "api_key": GEMINI_KEY},
    )
    assert response.status_code == 200, response.text
    assert response.json()["has_api_key"] is True
    row = _row(agent_id)
    assert row.api_key_provider == "gemini"
    assert decrypt_api_key(row.api_key_encrypted, agent_id=agent_id, provider="gemini") == (
        GEMINI_KEY
    )
    # The Groq key is gone, not merely hidden.
    with pytest.raises(CredentialError):
        decrypt_api_key(row.api_key_encrypted, agent_id=agent_id, provider="groq")


def test_a_key_for_another_provider_does_not_count(client, groq_payload):
    """groq -> mock is allowed; the Groq key then reads as not configured."""
    agent_id = _create(client, groq_payload)["id"]
    body = client.patch(
        f"/api/agents/{agent_id}", json={"provider": "mock", "model": "mock-1"}
    ).json()
    assert body["has_api_key"] is False
    # ...and switching back to groq finds its own key again.
    body = client.patch(
        f"/api/agents/{agent_id}", json={"provider": "groq", "model": "openai/gpt-oss-120b"}
    ).json()
    assert body["has_api_key"] is True


def test_legacy_agent_without_a_key_stays_editable(client):
    """Rows from before per-agent keys have NULL credentials."""
    with SessionLocal() as session:
        legacy = Agent(name="Legacy", provider="groq", model="openai/gpt-oss-120b")
        session.add(legacy)
        session.commit()
        agent_id = legacy.id

    assert client.get(f"/api/agents/{agent_id}").json()["has_api_key"] is False
    response = client.patch(f"/api/agents/{agent_id}", json={"name": "Legacy 2", "provider": "groq"})
    assert response.status_code == 200
    assert response.json()["has_api_key"] is False


def test_key_update_leaves_tools_and_mcp_config_intact(client, groq_payload):
    config = {
        "tools": ["calculator", "current_time", "stock_quote"],
        "mcp_server_ids": ["server-a", "server-b"],
    }
    agent_id = _create(client, {**groq_payload, **config})["id"]
    body = client.patch(f"/api/agents/{agent_id}", json={"api_key": "gsk_rotated"}).json()
    assert body["tools"] == config["tools"]
    assert body["mcp_server_ids"] == config["mcp_server_ids"]


# -- runtime ------------------------------------------------------------------


def test_runtime_gives_the_provider_the_decrypted_key(client, groq_payload):
    agent_id = _create(client, groq_payload)["id"]
    runtime = AgentRuntime(_row(agent_id))
    assert isinstance(runtime.provider, GroqProvider)
    assert runtime.provider._api_key == GROQ_KEY


def test_each_agent_gets_its_own_provider_and_key(client, groq_payload, agent_payload):
    groq_id = _create(client, groq_payload)["id"]
    gemini_id = _create(
        client,
        {**agent_payload, "provider": "gemini", "model": "gemini-3.5-flash", "api_key": GEMINI_KEY},
    )["id"]
    groq_runtime = AgentRuntime(_row(groq_id))
    gemini_runtime = AgentRuntime(_row(gemini_id))
    assert isinstance(gemini_runtime.provider, GeminiProvider)
    assert gemini_runtime.provider._api_key == GEMINI_KEY
    assert groq_runtime.provider._api_key == GROQ_KEY


class _CapturingProvider:
    """Records everything the runtime sends to the model."""

    name = "groq"
    supports_tools = True
    requires_api_key = True

    def __init__(self, api_key):
        self.api_key = api_key
        self.calls: list[dict] = []

    def is_available(self):
        return True

    async def generate(self, **kwargs):
        self.calls.append(kwargs)
        return LLMResponse(text="ok", stop_reason="end_turn")

    async def stream(self, **kwargs):
        self.calls.append(kwargs)
        yield StreamEvent(type="done", response=LLMResponse(text="ok", stop_reason="end_turn"))


@pytest.mark.asyncio
async def test_key_never_reaches_prompts_messages_or_tools(client, groq_payload, monkeypatch):
    created = {}

    def factory(name, api_key=None):
        created["provider"] = _CapturingProvider(api_key)
        return created["provider"]

    monkeypatch.setattr(runtime_module, "get_provider", factory)
    agent_id = _create(client, {**groq_payload, "tools": ["calculator", "current_time"]})["id"]
    runtime = AgentRuntime(_row(agent_id))
    await runtime.run(history=[], user_message="What is 2+2?")

    provider = created["provider"]
    assert provider.api_key == GROQ_KEY
    assert provider.calls
    assert GROQ_KEY not in repr(provider.calls)
    tool_names = {spec.name for spec in provider.calls[0]["tools"]}
    assert {"calculator", "current_time"} <= tool_names


@pytest.mark.asyncio
async def test_keyed_agent_still_loads_its_tools(client, groq_payload):
    agent_id = _create(client, {**groq_payload, "tools": ["calculator", "current_time"]})["id"]
    specs = await AgentRuntime(_row(agent_id))._load_tools()
    assert {s.name for s in specs} == {"calculator", "current_time"}


def test_missing_credential_produces_a_safe_chat_error(client):
    with SessionLocal() as session:
        legacy = Agent(name="No key", provider="groq", model="openai/gpt-oss-120b")
        session.add(legacy)
        session.commit()
        agent_id = legacy.id

    response = client.post(f"/api/agents/{agent_id}/chat", json={"message": "hi"})
    assert response.status_code == 200
    body = response.json()
    assert body["metadata"]["error"] == "provider_not_configured"
    assert "No API key is configured for provider 'groq'" in body["response"]
    assert "Traceback" not in response.text


def test_one_agent_cannot_use_another_agents_ciphertext(client, groq_payload):
    """Copying the stored value onto another row must not yield a usable key."""
    victim_id = _create(client, groq_payload)["id"]
    thief_id = _create(client, {**groq_payload, "api_key": "gsk_thief_own_key"})["id"]
    with SessionLocal() as session:
        thief = session.get(Agent, thief_id)
        thief.api_key_encrypted = session.get(Agent, victim_id).api_key_encrypted
        session.commit()

    with pytest.raises(ProviderNotConfigured, match="could not be decrypted"):
        AgentRuntime(_row(thief_id))

    response = client.post(f"/api/agents/{thief_id}/chat", json={"message": "hi"})
    assert response.json()["metadata"]["error"] == "provider_not_configured"
    assert GROQ_KEY not in response.text


def test_ciphertext_is_bound_to_its_provider(client, groq_payload):
    """Relabelling a Groq key as a Gemini key must not decrypt."""
    agent_id = _create(client, groq_payload)["id"]
    with SessionLocal() as session:
        row = session.get(Agent, agent_id)
        row.provider = "gemini"
        row.api_key_provider = "gemini"
        session.commit()
    with pytest.raises(ProviderNotConfigured):
        AgentRuntime(_row(agent_id))


def test_runtime_redacts_its_key_from_error_text(client, groq_payload):
    agent_id = _create(client, groq_payload)["id"]
    runtime = AgentRuntime(_row(agent_id))
    assert runtime.redact(f"401: bad key {GROQ_KEY}") == "401: bad key [redacted]"


def test_registry_never_falls_back_to_an_environment_key(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_from_env")
    assert get_provider("groq").is_available() is False
