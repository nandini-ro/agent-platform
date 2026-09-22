"""MCP manager, discovery, and the agent-level server allowlist.

These run the real demo MCP server as a subprocess over stdio - they exercise
the actual protocol, not a stub.
"""

import os
import sys

import pytest

from app.models.agent import Agent
from app.models.mcp_server import MCPServer
from app.services.agent_runtime import AgentRuntime
from app.services.mcp_manager import MCPError, MCPManager, qualify, slugify

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _demo_server(**overrides) -> MCPServer:
    defaults = dict(
        id="s1",
        name="Demo KB",
        slug="demo-kb",
        description="",
        transport="stdio",
        command=sys.executable,
        args=[os.path.join(BACKEND_DIR, "mcp_servers", "demo_server.py")],
        cwd=BACKEND_DIR,
        env_keys=[],
        enabled=True,
    )
    return MCPServer(**{**defaults, **overrides})


def _agent(**overrides) -> Agent:
    defaults = dict(
        id="a1",
        name="A",
        description="",
        system_prompt="You look things up.",
        provider="mock",
        model="mock-1",
        temperature=1.0,
        max_tokens=256,
        tools=[],
        mcp_server_ids=[],
    )
    return Agent(**{**defaults, **overrides})


# -- naming -----------------------------------------------------------------


def test_slugify_produces_a_safe_identifier():
    assert slugify("Demo KB Server!") == "demo-kb-server"
    assert slugify("   ") == "server"


def test_qualified_names_namespace_the_server():
    assert qualify("demo-kb", "kb_search") == "mcp__demo-kb__kb_search"


# -- discovery --------------------------------------------------------------


@pytest.mark.asyncio
async def test_discovery_lists_the_demo_servers_tools():
    tools = await MCPManager().discover(_demo_server(), use_cache=False)
    names = sorted(t.tool_name for t in tools)
    assert names == ["kb_search", "word_count"]
    search = next(t for t in tools if t.tool_name == "kb_search")
    assert search.qualified_name == "mcp__demo-kb__kb_search"
    assert "query" in search.input_schema["properties"]


@pytest.mark.asyncio
async def test_discovery_caches_results():
    mcp = MCPManager()
    server = _demo_server()
    first = await mcp.discover(server)
    second = await mcp.discover(server)
    assert first is second  # served from cache, no second subprocess
    mcp.invalidate(server.id)
    assert await mcp.discover(server) is not first


@pytest.mark.asyncio
async def test_unreachable_server_raises_a_clear_error():
    with pytest.raises(MCPError, match="Command not found"):
        await MCPManager().discover(
            _demo_server(command="definitely-not-a-real-binary"), use_cache=False
        )


@pytest.mark.asyncio
async def test_unsupported_transport_is_rejected():
    with pytest.raises(MCPError, match="not supported"):
        await MCPManager().discover(_demo_server(transport="websocket"), use_cache=False)


# -- environment isolation --------------------------------------------------


def test_subprocess_env_excludes_unlisted_variables():
    """A secret in the backend's environment must not reach an MCP server."""
    os.environ["A_BACKEND_SECRET"] = "do-not-leak"
    os.environ["ALLOWED_VAR"] = "fine"
    try:
        env = MCPManager()._build_env(_demo_server(env_keys=["ALLOWED_VAR"]))
        assert "A_BACKEND_SECRET" not in env
        assert env["ALLOWED_VAR"] == "fine"
        assert "PATH" in env  # still launchable
    finally:
        del os.environ["A_BACKEND_SECRET"], os.environ["ALLOWED_VAR"]


def test_missing_env_var_is_skipped_not_fatal():
    env = MCPManager()._build_env(_demo_server(env_keys=["NOT_SET_ANYWHERE"]))
    assert "NOT_SET_ANYWHERE" not in env


# -- invocation -------------------------------------------------------------


@pytest.mark.asyncio
async def test_calling_an_mcp_tool_returns_its_text():
    result = await MCPManager().call_tool(
        _demo_server(), "kb_search", {"query": "deployment"}
    )
    assert "main branch" in result


@pytest.mark.asyncio
async def test_calling_an_unknown_mcp_tool_errors():
    with pytest.raises(MCPError):
        await MCPManager().call_tool(_demo_server(), "no_such_tool", {})


# -- runtime integration ----------------------------------------------------


@pytest.mark.asyncio
async def test_agent_without_the_server_gets_no_mcp_tools(monkeypatch):
    runtime = AgentRuntime(_agent(mcp_server_ids=[]))
    assert await runtime._load_tools() == []
    assert runtime._mcp_index == {}


@pytest.mark.asyncio
async def test_agent_with_the_server_is_offered_its_tools(monkeypatch):
    server = _demo_server()
    runtime = AgentRuntime(_agent(mcp_server_ids=[server.id]))
    monkeypatch.setattr(runtime, "_permitted_mcp_servers", lambda: [server])

    specs = await runtime._load_tools()
    assert sorted(s.name for s in specs) == [
        "mcp__demo-kb__kb_search",
        "mcp__demo-kb__word_count",
    ]


@pytest.mark.asyncio
async def test_mcp_tool_executes_through_the_runtime(monkeypatch):
    server = _demo_server()
    runtime = AgentRuntime(_agent(mcp_server_ids=[server.id]))
    monkeypatch.setattr(runtime, "_permitted_mcp_servers", lambda: [server])
    await runtime._load_tools()

    invocation = await runtime._execute_tool(
        "mcp__demo-kb__kb_search", {"query": "onboarding"}
    )
    assert invocation.ok is True
    assert invocation.source == "mcp"
    assert "architecture doc" in invocation.result


@pytest.mark.asyncio
async def test_mcp_arguments_are_validated_before_the_call(monkeypatch):
    server = _demo_server()
    runtime = AgentRuntime(_agent(mcp_server_ids=[server.id]))
    monkeypatch.setattr(runtime, "_permitted_mcp_servers", lambda: [server])
    await runtime._load_tools()

    invocation = await runtime._execute_tool("mcp__demo-kb__kb_search", {})
    assert invocation.ok is False
    assert "Invalid arguments" in invocation.error


@pytest.mark.asyncio
async def test_mcp_tool_from_an_unpermitted_server_is_refused():
    """Not in _mcp_index means it falls through to the local gate and is denied."""
    runtime = AgentRuntime(_agent(mcp_server_ids=[]))
    await runtime._load_tools()
    invocation = await runtime._execute_tool(
        "mcp__demo-kb__kb_search", {"query": "security"}
    )
    assert invocation.ok is False
    assert "not permitted" in invocation.error


@pytest.mark.asyncio
async def test_unreachable_mcp_server_degrades_rather_than_breaking(monkeypatch):
    """Other tools stay usable when one MCP server is down."""
    broken = _demo_server(id="s2", slug="broken", command="not-a-real-binary")
    runtime = AgentRuntime(_agent(tools=["calculator"], mcp_server_ids=[broken.id]))
    monkeypatch.setattr(runtime, "_permitted_mcp_servers", lambda: [broken])

    specs = await runtime._load_tools()
    assert [s.name for s in specs] == ["calculator"]


@pytest.mark.asyncio
async def test_end_to_end_user_agent_mcp_tool_agent_response(monkeypatch):
    """User -> Agent -> MCP tool -> Agent -> Response."""
    server = _demo_server()
    runtime = AgentRuntime(_agent(mcp_server_ids=[server.id]))
    monkeypatch.setattr(runtime, "_permitted_mcp_servers", lambda: [server])

    result = await runtime.run(history=[], user_message="Please run kb_search security")
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].source == "mcp"
    assert result.tool_calls[0].ok is True
    assert "allowlisted per agent" in result.text


# -- remote servers over streamable HTTP ------------------------------------
#
# The stdio tests above launch the demo server as a subprocess. These launch
# the *HTTP* demo server and connect to it by URL, so the remote path is
# exercised for real rather than stubbed.


@pytest.fixture
def allow_loopback(monkeypatch):
    """Let the SSRF guard through for tests that are not about SSRF.

    A loopback URL is exactly what assert_safe_url is meant to refuse; the
    guard tests below rely on that and must not use this fixture.
    """
    from app.config.settings import get_settings

    monkeypatch.setattr(get_settings(), "http_tool_allow_private_networks", True)


@pytest.fixture(scope="module")
def http_mcp_server():
    """Run mcp_servers/demo_http_server.py and yield its URL."""
    import socket
    import subprocess
    import time
    import urllib.error
    import urllib.request

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    process = subprocess.Popen(
        [sys.executable, os.path.join(BACKEND_DIR, "mcp_servers", "demo_http_server.py")],
        env={**os.environ, "DEMO_MCP_PORT": str(port)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}/mcp"
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(url, timeout=0.2)
            except urllib.error.HTTPError:
                break  # responding, just not to a bare GET
            except (urllib.error.URLError, OSError):
                time.sleep(0.1)
        else:
            pytest.fail("demo HTTP MCP server did not start")
        yield url
    finally:
        process.terminate()
        process.wait(timeout=10)


def _remote_server(url: str, **overrides) -> MCPServer:
    defaults = dict(
        id="s2",
        name="Remote KB",
        slug="remote-kb",
        description="",
        transport="http",
        url=url,
        headers={},
        command="",
        args=[],
        cwd=None,
        env_keys=[],
        enabled=True,
    )
    return MCPServer(**{**defaults, **overrides})


@pytest.mark.asyncio
async def test_remote_server_discovery_lists_its_tools(http_mcp_server, allow_loopback):
    tools = await MCPManager().discover(
        _remote_server(http_mcp_server), use_cache=False
    )
    assert {t.tool_name for t in tools} == {"kb_search", "word_count"}
    assert {t.qualified_name for t in tools} == {
        "mcp__remote-kb__kb_search",
        "mcp__remote-kb__word_count",
    }


@pytest.mark.asyncio
async def test_remote_tool_call_returns_its_text(http_mcp_server, allow_loopback):
    result = await MCPManager().call_tool(
        _remote_server(http_mcp_server), "kb_search", {"query": "security"}
    )
    assert "environment variables" in result


@pytest.mark.asyncio
async def test_remote_and_stdio_tools_are_namespaced_apart(
    http_mcp_server, allow_loopback
):
    """The same tool name on two servers must not collide."""
    manager = MCPManager()
    local = await manager.discover(_demo_server(), use_cache=False)
    remote = await manager.discover(_remote_server(http_mcp_server), use_cache=False)
    assert not {t.qualified_name for t in local} & {t.qualified_name for t in remote}


@pytest.mark.asyncio
async def test_remote_server_without_a_url_is_rejected(allow_loopback):
    with pytest.raises(MCPError, match="no URL configured"):
        await MCPManager().discover(_remote_server(""), use_cache=False)


@pytest.mark.asyncio
async def test_agent_can_use_a_remote_servers_tool(
    http_mcp_server, allow_loopback, monkeypatch
):
    """End to end: the runtime offers and executes a remote MCP tool."""
    server = _remote_server(http_mcp_server)
    runtime = AgentRuntime(_agent(mcp_server_ids=[server.id]))
    monkeypatch.setattr(runtime, "_permitted_mcp_servers", lambda: [server])

    specs = await runtime._load_tools()
    assert "mcp__remote-kb__kb_search" in {s.name for s in specs}

    invocation = await runtime._execute_tool(
        "mcp__remote-kb__kb_search", {"query": "deployment"}
    )
    assert invocation.ok is True
    assert invocation.source == "mcp"
    assert "main branch" in invocation.result


@pytest.mark.asyncio
async def test_remote_tool_from_an_unpermitted_server_is_refused(
    http_mcp_server, allow_loopback
):
    """The allowlist is transport-blind: remote tools go through the same gate."""
    runtime = AgentRuntime(_agent(mcp_server_ids=[]))
    await runtime._load_tools()
    invocation = await runtime._execute_tool(
        "mcp__remote-kb__kb_search", {"query": "security"}
    )
    assert invocation.ok is False
    assert "not permitted" in invocation.error


# -- the SSRF guard applies to MCP URLs too ---------------------------------


@pytest.mark.parametrize(
    "url, reason",
    [
        ("http://127.0.0.1:9/mcp", "non-public"),
        ("http://169.254.169.254/mcp", "non-public"),  # cloud metadata
        ("http://10.0.0.5/mcp", "non-public"),
        ("file:///etc/passwd", "scheme"),
    ],
)
@pytest.mark.asyncio
async def test_unsafe_mcp_urls_are_refused(url, reason):
    """An MCP URL typed into the UI is no more trusted than a tool URL."""
    with pytest.raises(MCPError, match=reason):
        await MCPManager().discover(_remote_server(url), use_cache=False)


# -- header handling --------------------------------------------------------


def test_header_secrets_are_referenced_not_stored(monkeypatch):
    monkeypatch.setenv("MY_MCP_TOKEN", "s3cret")
    server = _remote_server(
        "https://example.com/mcp",
        headers={"Authorization": "Bearer {{env:MY_MCP_TOKEN}}"},
    )
    assert "s3cret" not in str(server.headers)
    resolved = MCPManager()._build_headers(server)
    assert resolved["Authorization"] == "Bearer s3cret"


def test_hop_by_hop_headers_are_dropped():
    """Letting a user set these is how request smuggling starts."""
    server = _remote_server(
        "https://example.com/mcp",
        headers={"X-Trace": "keep", "Content-Length": "0", "Connection": "upgrade"},
    )
    assert MCPManager()._build_headers(server) == {"X-Trace": "keep"}


# -- the API's transport validation -----------------------------------------


def test_creating_a_remote_server_needs_a_url(client):
    response = client.post(
        "/api/mcp/servers", json={"name": "Remote", "transport": "http"}
    )
    assert response.status_code == 422
    assert "needs a URL" in response.text


def test_creating_a_local_server_needs_a_command(client):
    response = client.post(
        "/api/mcp/servers", json={"name": "Local", "transport": "stdio"}
    )
    assert response.status_code == 422
    assert "needs a command" in response.text


def test_an_unknown_transport_is_rejected(client):
    response = client.post(
        "/api/mcp/servers",
        json={"name": "Odd", "transport": "carrier-pigeon", "url": "https://x/mcp"},
    )
    assert response.status_code == 422


def test_a_remote_server_round_trips_through_the_api(client):
    created = client.post(
        "/api/mcp/servers",
        json={
            "name": "Hosted KB",
            "transport": "http",
            "url": "https://example.com/mcp",
            "headers": {"Authorization": "Bearer {{env:SOME_TOKEN}}"},
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["transport"] == "http"
    assert body["url"] == "https://example.com/mcp"
    assert body["slug"] == "hosted-kb"
    # The reference is stored; no value was ever supplied to store.
    assert body["headers"]["Authorization"] == "Bearer {{env:SOME_TOKEN}}"

    client.delete(f"/api/mcp/servers/{body['id']}")


def test_clearing_the_url_of_a_remote_server_is_rejected(client):
    """A PATCH is only checkable against the stored row, not on its own."""
    created = client.post(
        "/api/mcp/servers",
        json={"name": "Patchable", "transport": "http", "url": "https://example.com/mcp"},
    ).json()

    response = client.patch(f"/api/mcp/servers/{created['id']}", json={"url": "  "})
    assert response.status_code == 422
    assert "needs a url" in response.text.lower()

    client.delete(f"/api/mcp/servers/{created['id']}")
