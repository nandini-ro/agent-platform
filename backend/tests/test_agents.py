"""Agent CRUD."""


def test_create_and_read_agent(client, agent_payload):
    created = client.post("/api/agents", json=agent_payload)
    assert created.status_code == 201, created.text
    agent = created.json()
    assert agent["id"]
    assert agent["name"] == "Test Agent"
    assert agent["system_prompt"] == "You are a terse test assistant."

    fetched = client.get(f"/api/agents/{agent['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == agent["id"]


def test_list_agents_includes_created(client, agent_payload):
    agent_id = client.post("/api/agents", json=agent_payload).json()["id"]
    listed = client.get("/api/agents").json()
    assert agent_id in [a["id"] for a in listed]


def test_update_agent_is_partial(client, agent_payload):
    agent_id = client.post("/api/agents", json=agent_payload).json()["id"]
    updated = client.patch(
        f"/api/agents/{agent_id}", json={"system_prompt": "New instructions."}
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["system_prompt"] == "New instructions."
    assert body["name"] == "Test Agent"  # untouched


def test_delete_agent(client, agent_payload):
    agent_id = client.post("/api/agents", json=agent_payload).json()["id"]
    assert client.delete(f"/api/agents/{agent_id}").status_code == 204
    assert client.get(f"/api/agents/{agent_id}").status_code == 404


def test_unknown_agent_is_404(client):
    assert client.get("/api/agents/does-not-exist").status_code == 404


def test_unknown_provider_rejected(client, agent_payload):
    response = client.post(
        "/api/agents", json={**agent_payload, "provider": "not-a-provider"}
    )
    assert response.status_code == 422
    assert "Unknown provider" in response.text


def test_providers_endpoint_lists_mock(client):
    providers = {p["name"]: p for p in client.get("/api/agents/providers").json()}
    assert providers["mock"]["requires_api_key"] is False
    assert providers["groq"]["requires_api_key"] is True
    assert {"openai", "gemini", "groq"} <= set(providers)
