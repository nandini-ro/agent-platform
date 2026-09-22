"""User-defined HTTP tools.

This feature lets someone define, through a web form, a request the server will
then make. Most of these tests are about what it must refuse.
"""

import json
from types import SimpleNamespace

import pytest

from app.models.agent import Agent
from app.models.http_tool import HTTPTool
from app.services.agent_runtime import AgentRuntime
from app.services.http_tool import (
    HTTPToolError,
    UserHTTPTool,
    assert_safe_url,
    resolve_env,
    substitute,
    validate_definition,
)


@pytest.fixture
def no_dns(monkeypatch):
    """Skip the address check for tests that are not about SSRF.

    assert_safe_url resolves the hostname, which needs a working resolver;
    the SSRF tests below exercise that path deliberately.
    """
    from app.config.settings import get_settings

    monkeypatch.setattr(get_settings(), "http_tool_allow_private_networks", True)


def _row(**overrides) -> HTTPTool:
    defaults = dict(
        id="t1",
        name="weather",
        description="Get the weather for a city.",
        parameters={
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
            "additionalProperties": False,
        },
        method="GET",
        url_template="https://example.com/weather/{city}",
        headers={},
        query_template={},
        body_template=None,
        response_path=None,
        enabled=True,
        timeout_seconds=10,
    )
    return HTTPTool(**{**defaults, **overrides})


# ---------------------------------------------------------------------------
# Templating must not become code execution
# ---------------------------------------------------------------------------


def test_placeholders_are_substituted_and_url_encoded():
    assert substitute("/w/{city}", {"city": "São Paulo"}, encode=True) == "/w/S%C3%A3o%20Paulo"


def test_a_value_cannot_inject_extra_path_segments():
    """Slashes are encoded, so a value cannot climb the URL."""
    out = substitute("https://x.test/a/{p}/b", {"p": "../../admin"}, encode=True)
    assert "/../" not in out
    assert out == "https://x.test/a/..%2F..%2Fadmin/b"


@pytest.mark.parametrize(
    "template",
    [
        "{x.__class__}",
        "{x.__class__.__init__.__globals__}",
        "{0.__class__.__mro__}",
        "{x!r}",
    ],
)
def test_format_string_attacks_are_inert(template):
    """str.format on a user template would reach object internals; regex does not."""
    assert substitute(template, {"x": "value"}, encode=False) == template


def test_unknown_placeholders_are_left_alone():
    assert substitute("/a/{missing}", {"city": "x"}, encode=True) == "/a/{missing}"


# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------


def test_env_reference_resolves_from_the_backend_environment(monkeypatch):
    monkeypatch.setenv("MY_TOOL_TOKEN", "s3cret")
    assert resolve_env("Bearer {{env:MY_TOOL_TOKEN}}") == "Bearer s3cret"


def test_missing_env_reference_is_a_clear_error():
    with pytest.raises(HTTPToolError, match="NOT_SET_ANYWHERE"):
        resolve_env("Bearer {{env:NOT_SET_ANYWHERE}}")


def test_the_stored_definition_never_contains_the_secret(monkeypatch, no_dns):
    """Only the reference is persisted; resolution happens at call time."""
    monkeypatch.setenv("MY_TOOL_TOKEN", "s3cret")
    row = _row(headers={"Authorization": "Bearer {{env:MY_TOOL_TOKEN}}"})
    assert "s3cret" not in json.dumps(row.headers)
    request = UserHTTPTool(row)._build_request({"city": "Paris"})
    assert request["headers"]["Authorization"] == "Bearer s3cret"


# ---------------------------------------------------------------------------
# SSRF
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://localhost:8000/api/agents",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://[::1]/x",
        "http://0.0.0.0/x",
    ],
)
def test_non_public_addresses_are_refused(url):
    with pytest.raises(HTTPToolError):
        assert_safe_url(url)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "gopher://x/", "ftp://x/"])
def test_non_http_schemes_are_refused(url):
    with pytest.raises(HTTPToolError, match="scheme"):
        assert_safe_url(url)


def test_private_addresses_allowed_only_when_explicitly_enabled(monkeypatch):
    from app.config.settings import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "http_tool_allow_private_networks", True)
    assert_safe_url("http://127.0.0.1/x")  # no raise


def test_host_allowlist_is_enforced(monkeypatch):
    from app.config.settings import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "http_tool_allowed_hosts", "api.example.com")
    with pytest.raises(HTTPToolError, match="not in HTTP_TOOL_ALLOWED_HOSTS"):
        assert_safe_url("https://evil.test/x")


def test_forbidden_headers_are_rejected(no_dns):
    with pytest.raises(HTTPToolError, match="may not be set"):
        UserHTTPTool(_row(headers={"Host": "evil.test"}))._build_request({"city": "x"})


# ---------------------------------------------------------------------------
# Definition validation
# ---------------------------------------------------------------------------


def _valid(**overrides):
    base = dict(
        name="my_tool",
        method="GET",
        url_template="https://example.com/x/{q}",
        headers={},
        parameters={"type": "object", "properties": {"q": {"type": "string"}}},
    )
    return {**base, **overrides}


def test_a_valid_definition_passes():
    validate_definition(**_valid())


@pytest.mark.parametrize("name", ["has space", "has/slash", "", "a" * 65, "emoji🙂"])
def test_invalid_tool_names_are_rejected(name):
    with pytest.raises(HTTPToolError, match="Tool name"):
        validate_definition(**_valid(name=name))


def test_a_built_in_tool_name_cannot_be_reused():
    with pytest.raises(HTTPToolError, match="built-in"):
        validate_definition(**_valid(name="calculator"))


def test_undeclared_url_placeholders_are_rejected():
    """Otherwise the model can never fill them and every call fails."""
    with pytest.raises(HTTPToolError, match="undeclared parameter"):
        validate_definition(
            **_valid(url_template="https://example.com/{q}/{secret_id}")
        )


def test_unsupported_methods_are_rejected():
    with pytest.raises(HTTPToolError, match="Method must be"):
        validate_definition(**_valid(method="TRACE"))


def test_relative_urls_are_rejected():
    with pytest.raises(HTTPToolError, match="absolute"):
        validate_definition(**_valid(url_template="/just/a/path"))


# ---------------------------------------------------------------------------
# Response handling
# ---------------------------------------------------------------------------


def _response(text="", status_code=200, payload=None):
    return SimpleNamespace(
        text=text if payload is None else json.dumps(payload),
        status_code=status_code,
        json=lambda: payload,
    )


def test_response_path_extracts_a_nested_field():
    tool = UserHTTPTool(_row(response_path="data.items.0.title"))
    out = tool._format_response(
        _response(payload={"data": {"items": [{"title": "Sunny"}]}})
    )
    assert out == "Sunny"


def test_a_missing_response_path_is_reported():
    tool = UserHTTPTool(_row(response_path="data.nope"))
    with pytest.raises(HTTPToolError, match="No field 'nope'"):
        tool._format_response(_response(payload={"data": {}}))


def test_error_statuses_are_returned_to_the_model_not_raised():
    """The model should see the API's own error and be able to correct itself."""
    out = UserHTTPTool(_row())._format_response(_response("Bad city", status_code=400))
    assert out.startswith("HTTP 400")
    assert "Bad city" in out


def test_large_responses_are_truncated():
    tool = UserHTTPTool(_row())
    tool.settings.http_tool_max_response_chars = 50
    out = tool._format_response(_response("x" * 5000))
    assert len(out) < 200
    assert out.endswith("[truncated]")


# ---------------------------------------------------------------------------
# The permission gate applies exactly as it does to code-defined tools
# ---------------------------------------------------------------------------


def _agent(**overrides) -> Agent:
    defaults = dict(
        id="a1", name="A", description="", system_prompt="",
        provider="mock", model="mock-1", temperature=1.0, max_tokens=256,
        tools=[], mcp_server_ids=[],
    )
    return Agent(**{**defaults, **overrides})


@pytest.mark.asyncio
async def test_http_tool_is_offered_only_when_the_agent_lists_it(monkeypatch):
    runtime = AgentRuntime(_agent(tools=["weather"]))
    monkeypatch.setattr(
        runtime, "_permitted_http_tools", lambda: [UserHTTPTool(_row())]
    )
    specs = await runtime._load_tools()
    assert [s.name for s in specs] == ["weather"]


@pytest.mark.asyncio
async def test_an_unlisted_http_tool_is_refused(monkeypatch):
    runtime = AgentRuntime(_agent(tools=[]))
    monkeypatch.setattr(
        runtime, "_permitted_http_tools", lambda: [UserHTTPTool(_row())]
    )
    await runtime._load_tools()
    invocation = await runtime._execute_tool("weather", {"city": "Paris"})
    assert invocation.ok is False
    assert "not permitted" in invocation.error


@pytest.mark.asyncio
async def test_http_tool_arguments_are_schema_validated(monkeypatch):
    runtime = AgentRuntime(_agent(tools=["weather"]))
    monkeypatch.setattr(
        runtime, "_permitted_http_tools", lambda: [UserHTTPTool(_row())]
    )
    await runtime._load_tools()
    invocation = await runtime._execute_tool("weather", {"wrong": "field"})
    assert invocation.ok is False
    assert "Invalid arguments" in invocation.error


@pytest.mark.asyncio
async def test_http_tool_execution_is_recorded_with_its_source(monkeypatch):
    runtime = AgentRuntime(_agent(tools=["weather"]))
    tool = UserHTTPTool(_row())

    async def fake_execute(arguments):
        return "Sunny, 21C"

    tool.execute = fake_execute
    monkeypatch.setattr(runtime, "_permitted_http_tools", lambda: [tool])
    await runtime._load_tools()

    invocation = await runtime._execute_tool("weather", {"city": "Paris"})
    assert invocation.ok is True
    assert invocation.source == "http"
    assert invocation.result == "Sunny, 21C"


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def _definition(**overrides):
    base = {
        "name": "weather",
        "description": "Get the weather for a city.",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
            "additionalProperties": False,
        },
        "method": "GET",
        "url_template": "https://api.example.com/weather/{city}",
    }
    return {**base, **overrides}


def test_creating_a_tool_makes_it_available_to_assign(client):
    created = client.post("/api/tools/http", json=_definition())
    assert created.status_code == 201, created.text

    catalogue = {t["name"]: t for t in client.get("/api/tools").json()}
    assert catalogue["weather"]["source"] == "http"
    assert catalogue["calculator"]["source"] == "builtin"


def test_a_created_tool_is_not_granted_to_any_agent(client, agent_payload):
    """Creating a tool must never be the same as granting it."""
    client.post("/api/tools/http", json=_definition(name="grant_check"))
    agent = client.post("/api/agents", json=agent_payload).json()
    assert agent["tools"] == []


def test_duplicate_names_are_rejected(client):
    client.post("/api/tools/http", json=_definition(name="dupe"))
    again = client.post("/api/tools/http", json=_definition(name="dupe"))
    assert again.status_code == 409


def test_shadowing_a_builtin_is_rejected(client):
    response = client.post("/api/tools/http", json=_definition(name="calculator"))
    assert response.status_code == 422
    assert "built-in" in response.text


def test_invalid_definitions_are_rejected_with_a_reason(client):
    response = client.post(
        "/api/tools/http", json=_definition(url_template="https://x.test/{undeclared}")
    )
    assert response.status_code == 422
    assert "undeclared parameter" in response.text


def test_updating_and_deleting_a_tool(client):
    tool = client.post("/api/tools/http", json=_definition(name="edit_me")).json()
    patched = client.patch(
        f"/api/tools/http/{tool['id']}", json={"description": "Updated."}
    )
    assert patched.status_code == 200
    assert patched.json()["description"] == "Updated."

    assert client.delete(f"/api/tools/http/{tool['id']}").status_code == 204
    assert client.get(f"/api/tools/http/{tool['id']}").status_code == 404


def test_testing_a_tool_reports_the_failure_instead_of_500(client):
    """The try-it endpoint must surface SSRF refusals as a readable result."""
    tool = client.post(
        "/api/tools/http",
        json=_definition(name="local_probe", url_template="http://127.0.0.1/{city}"),
    ).json()
    result = client.post(
        f"/api/tools/http/{tool['id']}/test", json={"arguments": {"city": "x"}}
    )
    assert result.status_code == 200
    body = result.json()
    assert body["ok"] is False
    assert "non-public" in body["error"]


def test_testing_a_tool_validates_arguments_first(client):
    tool = client.post("/api/tools/http", json=_definition(name="arg_check")).json()
    body = client.post(
        f"/api/tools/http/{tool['id']}/test", json={"arguments": {}}
    ).json()
    assert body["ok"] is False
    assert "Invalid arguments" in body["error"]


def test_disabled_tools_are_not_offered(client):
    tool = client.post("/api/tools/http", json=_definition(name="off_tool")).json()
    client.patch(f"/api/tools/http/{tool['id']}", json={"enabled": False})
    names = [t["name"] for t in client.get("/api/tools").json()]
    assert "off_tool" not in names


def _response_with_headers(status_code, headers=None, text=""):
    return SimpleNamespace(
        text=text,
        status_code=status_code,
        headers=headers or {},
        json=lambda: json.loads(text),
    )


def test_a_redirect_is_reported_not_silently_returned():
    """Redirects are not followed - that would escape the SSRF check - so the
    caller must be told, rather than handed the redirect page as data."""
    tool = UserHTTPTool(_row())
    with pytest.raises(HTTPToolError, match="redirected"):
        tool._format_response(
            _response_with_headers(
                301, {"location": "https://elsewhere.test/x"}, "<html>moved</html>"
            )
        )


def test_redirect_message_names_the_target():
    tool = UserHTTPTool(_row())
    with pytest.raises(HTTPToolError, match="https://elsewhere.test/x"):
        tool._format_response(
            _response_with_headers(302, {"location": "https://elsewhere.test/x"})
        )


def test_status_is_checked_before_response_path():
    """A 404 HTML body must report the status, not 'the response is not JSON'."""
    tool = UserHTTPTool(_row(response_path="data.0"))
    out = tool._format_response(
        _response_with_headers(404, {}, "<html>Not Found</html>")
    )
    assert out.startswith("HTTP 404")
