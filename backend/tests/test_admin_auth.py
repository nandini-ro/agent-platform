"""The admin key gate on state-changing requests.

The endpoint this protects is `POST /api/mcp/servers`: an MCP server row
carries a command that the backend later executes as a subprocess, so an open
one is remote code execution on whatever host this runs on. These tests exist
to keep that shut.
"""

import pytest

from app import main

KEY = "test-admin-key"


@pytest.fixture
def keyed(monkeypatch):
    """Configure a key. Settings are a cached singleton; monkeypatch restores."""
    monkeypatch.setattr(main.settings, "agent_api_key", KEY)


@pytest.fixture
def stdio_server_payload():
    """The dangerous shape: a command the backend would go on to execute."""
    return {
        "name": "Injected Server",
        "description": "",
        "transport": "stdio",
        "command": "/bin/sh",
        "args": ["-c", "id"],
        "env_keys": [],
    }


def test_creating_an_mcp_server_is_rejected_without_the_key(
    client, keyed, stdio_server_payload
):
    response = client.post("/api/mcp/servers", json=stdio_server_payload)
    assert response.status_code == 401
    assert "API key" in response.json()["detail"]


def test_creating_an_agent_is_rejected_without_the_key(client, keyed, agent_payload):
    assert client.post("/api/agents", json=agent_payload).status_code == 401


def test_a_wrong_key_is_rejected(client, keyed, agent_payload):
    response = client.post(
        "/api/agents", json=agent_payload, headers={"X-API-Key": "not-it"}
    )
    assert response.status_code == 401


def test_the_right_key_is_accepted(client, keyed, agent_payload):
    response = client.post(
        "/api/agents", json=agent_payload, headers={"X-API-Key": KEY}
    )
    assert response.status_code == 201


def test_deletes_are_guarded_too(client, keyed, agent_payload):
    created = client.post(
        "/api/agents", json=agent_payload, headers={"X-API-Key": KEY}
    )
    agent_id = created.json()["id"]

    assert client.delete(f"/api/agents/{agent_id}").status_code == 401
    assert (
        client.delete(
            f"/api/agents/{agent_id}", headers={"X-API-Key": KEY}
        ).status_code
        == 204
    )


def test_reads_stay_open(client, keyed):
    """Reads expose configuration but cannot spawn a process or fetch a URL."""
    assert client.get("/api/agents").status_code == 200
    assert client.get("/api/mcp/servers").status_code == 200
    assert client.get("/health").status_code == 200


def test_an_unset_key_leaves_writes_open(client, agent_payload):
    """The local-checkout default. conftest blanks AGENT_API_KEY."""
    assert main.settings.agent_api_key == ""
    assert client.post("/api/agents", json=agent_payload).status_code == 201
