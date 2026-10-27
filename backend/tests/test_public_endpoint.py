"""Per-agent public URLs: /v1/agents/{slug}/chat behind each agent's own key.

The property that matters is isolation. An access key opens one agent's chat
and nothing else - not another agent, and not any write under /api.
"""

import pytest

from app import main

ADMIN = "test-admin-key"


def _create(client, payload, **overrides):
    response = client.post("/api/agents", json={**payload, **overrides})
    assert response.status_code == 201, response.text
    return response.json()


def _issue(client, agent_id, headers=None):
    response = client.post(f"/api/agents/{agent_id}/access-key", headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["access_key"]


def _chat(client, ref, key=None, scheme="bearer"):
    headers = {}
    if key and scheme == "bearer":
        headers["Authorization"] = f"Bearer {key}"
    elif key:
        headers["X-API-Key"] = key
    return client.post(f"/v1/agents/{ref}/chat", json={"message": "hi"}, headers=headers)


# -- slugs ------------------------------------------------------------------


def test_a_new_agent_gets_a_slug_from_its_name(client, agent_payload):
    agent = _create(client, agent_payload, name="Pirate Poet!! v2")
    assert agent["slug"].startswith("pirate-poet-v2")


def test_slugs_stay_unique(client, agent_payload):
    first = _create(client, agent_payload, name="Twin Agent")
    second = _create(client, agent_payload, name="Twin Agent")
    assert first["slug"] != second["slug"]
    assert second["slug"].startswith(first["slug"])


def test_a_slug_can_be_chosen_and_changed(client, agent_payload):
    agent = _create(client, agent_payload, slug="chosen-name-1")
    assert agent["slug"] == "chosen-name-1"
    moved = client.patch(f"/api/agents/{agent['id']}", json={"slug": "chosen-name-2"})
    assert moved.status_code == 200
    assert moved.json()["slug"] == "chosen-name-2"


def test_a_taken_slug_is_a_conflict(client, agent_payload):
    _create(client, agent_payload, slug="taken-slug")
    response = client.post("/api/agents", json={**agent_payload, "slug": "taken-slug"})
    assert response.status_code == 409


@pytest.mark.parametrize(
    "bad",
    ["Upper", "has space", "-leading", "trailing-", "double--hyphen", "",
     "0b6ad3e8-5a40-4a7e-9a27-2b1f7b0c1d2e"],
)
def test_malformed_slugs_are_rejected(client, agent_payload, bad):
    response = client.post("/api/agents", json={**agent_payload, "slug": bad})
    assert response.status_code == 422


# -- access keys ------------------------------------------------------------


def test_the_key_is_shown_once_and_only_a_hint_after(client, agent_payload):
    agent = _create(client, agent_payload)
    assert agent["has_access_key"] is False

    key = _issue(client, agent["id"])
    assert key.startswith("agk_")

    read = client.get(f"/api/agents/{agent['id']}").json()
    assert read["has_access_key"] is True
    assert key.startswith(read["access_key_hint"])
    assert key not in str(read)


def test_the_right_key_reaches_the_agent_by_slug_or_id(client, agent_payload):
    agent = _create(client, agent_payload)
    key = _issue(client, agent["id"])

    by_slug = _chat(client, agent["slug"], key)
    assert by_slug.status_code == 200, by_slug.text
    assert by_slug.json()["agent_id"] == agent["id"]
    assert _chat(client, agent["id"], key, scheme="x-api-key").status_code == 200


def test_missing_or_wrong_key_is_refused(client, agent_payload):
    agent = _create(client, agent_payload)
    _issue(client, agent["id"])
    assert _chat(client, agent["slug"]).status_code == 401
    assert _chat(client, agent["slug"], "agk_wrong").status_code == 401


def test_an_agent_without_a_key_is_closed(client, agent_payload):
    """No open default here, even with no admin key configured."""
    agent = _create(client, agent_payload)
    assert _chat(client, agent["slug"]).status_code == 403


def test_one_agents_key_does_not_open_another(client, agent_payload):
    a = _create(client, agent_payload, name="Agent A")
    b = _create(client, agent_payload, name="Agent B")
    key_a = _issue(client, a["id"])
    _issue(client, b["id"])
    assert _chat(client, b["slug"], key_a).status_code == 401


def test_rotating_retires_the_old_key(client, agent_payload):
    agent = _create(client, agent_payload)
    old = _issue(client, agent["id"])
    new = _issue(client, agent["id"])
    assert _chat(client, agent["slug"], old).status_code == 401
    assert _chat(client, agent["slug"], new).status_code == 200


def test_revoking_closes_the_url(client, agent_payload):
    agent = _create(client, agent_payload)
    key = _issue(client, agent["id"])
    assert client.delete(f"/api/agents/{agent['id']}/access-key").status_code == 204
    assert _chat(client, agent["slug"], key).status_code == 403


def test_unknown_agent_is_404(client):
    assert _chat(client, "no-such-agent", "agk_x").status_code == 404


# -- isolation from the admin key -------------------------------------------


@pytest.fixture
def keyed(monkeypatch):
    monkeypatch.setattr(main.settings, "agent_api_key", ADMIN)


def test_issuing_a_key_needs_the_admin_key(client, keyed, agent_payload):
    created = client.post(
        "/api/agents", json=agent_payload, headers={"X-API-Key": ADMIN}
    ).json()
    assert client.post(f"/api/agents/{created['id']}/access-key").status_code == 401


def test_an_access_key_cannot_write_to_the_api(client, keyed, agent_payload):
    admin = {"X-API-Key": ADMIN}
    created = client.post("/api/agents", json=agent_payload, headers=admin).json()
    key = _issue(client, created["id"], headers=admin)

    assert _chat(client, created["slug"], key).status_code == 200
    as_key = {"X-API-Key": key}
    assert client.post("/api/agents", json=agent_payload, headers=as_key).status_code == 401
    assert client.delete(f"/api/agents/{created['id']}", headers=as_key).status_code == 401


def test_the_admin_key_does_not_open_the_public_url(client, keyed, agent_payload):
    admin = {"X-API-Key": ADMIN}
    created = client.post("/api/agents", json=agent_payload, headers=admin).json()
    _issue(client, created["id"], headers=admin)
    assert _chat(client, created["slug"], ADMIN).status_code == 401


def test_meta_reports_the_public_base_url(client, monkeypatch):
    monkeypatch.setattr(main.settings, "public_base_url", "https://agents.example.com/")
    assert client.get("/api/meta").json()["public_base_url"] == (
        "https://agents.example.com"
    )
