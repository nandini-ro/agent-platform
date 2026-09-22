"""Chat: the stateless agent endpoint, streaming, and history isolation."""

import json


def _create_agent(client, payload, **overrides):
    return client.post("/api/agents", json={**payload, **overrides}).json()


def test_agent_chat_endpoint_returns_response(client, agent_payload):
    agent = _create_agent(client, agent_payload)
    response = client.post(f"/api/agents/{agent['id']}/chat", json={"message": "Hello"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["agent_id"] == agent["id"]
    assert "Hello" in body["response"]
    assert body["tool_calls"] == []
    assert body["metadata"]["provider"] == "mock"


def test_agent_chat_reflects_that_agents_system_prompt(client, agent_payload):
    """Two agents, same message, different behaviour - config drives the runtime."""
    pirate = _create_agent(
        client, agent_payload, name="Pirate", system_prompt="You are a pirate."
    )
    lawyer = _create_agent(
        client, agent_payload, name="Lawyer", system_prompt="You are a lawyer."
    )
    ask = lambda a: client.post(  # noqa: E731
        f"/api/agents/{a['id']}/chat", json={"message": "Who are you?"}
    ).json()["response"]

    assert "pirate" in ask(pirate).lower()
    assert "lawyer" in ask(lawyer).lower()


def test_agent_chat_requires_known_agent(client):
    response = client.post("/api/agents/nope/chat", json={"message": "hi"})
    assert response.status_code == 404


def test_agent_chat_rejects_empty_message(client, agent_payload):
    agent = _create_agent(client, agent_payload)
    response = client.post(f"/api/agents/{agent['id']}/chat", json={"message": ""})
    assert response.status_code == 422


def _stream_events(client, conversation_id, content):
    """Collect SSE events from a streaming send."""
    events = []
    with client.stream(
        "POST",
        f"/api/conversations/{conversation_id}/messages",
        json={"content": content},
    ) as response:
        assert response.status_code == 200
        name = None
        for line in response.iter_lines():
            if line.startswith("event:"):
                name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                events.append((name, json.loads(line.split(":", 1)[1].strip())))
    return events


def test_streaming_chat_persists_both_turns(client, agent_payload):
    agent = _create_agent(client, agent_payload)
    conversation = client.post(
        "/api/conversations", json={"agent_id": agent["id"]}
    ).json()

    events = _stream_events(client, conversation["id"], "Hello there")
    assert any(name == "token" for name, _ in events)
    done = [data for name, data in events if name == "done"]
    assert len(done) == 1
    assert done[0]["content"]

    messages = client.get(f"/api/conversations/{conversation['id']}/messages").json()
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "Hello there"
    assert messages[1]["content"] == done[0]["content"]


def test_conversation_title_taken_from_first_message(client, agent_payload):
    agent = _create_agent(client, agent_payload)
    conversation = client.post(
        "/api/conversations", json={"agent_id": agent["id"]}
    ).json()
    _stream_events(client, conversation["id"], "Explain SSE briefly")
    refreshed = client.get(f"/api/conversations/{conversation['id']}").json()
    assert refreshed["title"] == "Explain SSE briefly"


def test_history_does_not_leak_between_agents(client, agent_payload):
    """Each agent's conversation list is its own."""
    a = _create_agent(client, agent_payload, name="A")
    b = _create_agent(client, agent_payload, name="B")
    conversation_a = client.post("/api/conversations", json={"agent_id": a["id"]}).json()
    _stream_events(client, conversation_a["id"], "secret for A")

    b_conversations = client.get(f"/api/conversations?agent_id={b['id']}").json()
    assert b_conversations == []

    a_conversations = client.get(f"/api/conversations?agent_id={a['id']}").json()
    assert [c["id"] for c in a_conversations] == [conversation_a["id"]]


def test_conversation_requires_existing_agent(client):
    response = client.post("/api/conversations", json={"agent_id": "missing"})
    assert response.status_code == 404
