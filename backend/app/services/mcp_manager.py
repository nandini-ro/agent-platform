"""MCP manager.

Owns everything protocol-specific: launching configured MCP servers, listing
what they offer, and invoking a tool on behalf of an agent. AgentRuntime talks
to this module through plain ToolSpecs and strings and never imports the MCP
SDK itself, so MCP stays an optional, swappable subsystem.

Two transports are supported, and the difference is confined to `_target`:

  stdio - a local subprocess (`command` + `args`), launched per operation.
  http  - a remote endpoint (`url`) over streamable HTTP, no local process.

Connection model: one short-lived connection per operation. A persistent
session pool would be faster, but it means owning subprocess lifecycles across
the web server's task scopes - real complexity for a POC. Discovery results are
cached so the cost lands on tool calls only, not on every chat turn.

A remote server is reached through the same SSRF guard the user-defined HTTP
tools use: an MCP URL typed into the UI is no more trusted than a tool URL.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import time
from dataclasses import dataclass
from typing import Any
from xmlrpc import server

from app.config.settings import get_settings
from app.models.mcp_server import MCPServer
from app.services.llm.base import ToolSpec

logger = logging.getLogger(__name__)

_TOOL_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")
# Environment variables every subprocess needs to start at all. Anything else
# must be named explicitly in the server's env_keys.
_BASE_ENV_KEYS = ("PATH", "HOME", "LANG", "LC_ALL", "SYSTEMROOT", "TMPDIR")


class MCPError(Exception):
    """An MCP server could not be reached, or a call to it failed."""


@dataclass
class DiscoveredTool:
    server_id: str
    server_slug: str
    tool_name: str  # name as the MCP server knows it
    qualified_name: str  # namespaced name exposed to the model
    description: str
    input_schema: dict[str, Any]

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.qualified_name,
            description=self.description,
            input_schema=self.input_schema,
        )


def qualify(slug: str, tool_name: str) -> str:
    """Namespace an MCP tool so two servers can both expose `search`.

    The double underscore is a separator the model never has to reason about;
    it is stripped again before the call reaches the server.
    """
    return f"mcp__{slug}__{tool_name}"


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip().lower()).strip("-")
    return slug[:60] or "server"


class MCPManager:
    def __init__(self) -> None:
        self.settings = get_settings()
        # server_id -> (expires_at, tools)
        self._cache: dict[str, tuple[float, list[DiscoveredTool]]] = {}
        self._cache_ttl = 300.0

    # -- process configuration -------------------------------------------

    def _build_env(self, server: MCPServer) -> dict[str, str]:
        """Minimal environment for the subprocess.

        The parent environment is *not* inherited wholesale. Only a handful of
        variables needed to start a process, plus the ones this server config
        names explicitly, are passed through - so an MCP server cannot read
        unrelated secrets that happen to live in the backend's environment.
        """
        env = {
            key: os.environ[key] for key in _BASE_ENV_KEYS if key in os.environ
        }
        for key in server.env_keys or []:
            value = os.environ.get(key)
            if value is None:
                logger.warning(
                    "MCP server %s requests env var %s which is not set", server.slug, key
                )
                continue
            env[key] = value
        return env

    def _build_headers(self, server: MCPServer) -> dict[str, str]:
        """Resolve the configured headers, expanding {{env:NAME}} references.

        Resolution happens here, at connection time, so a secret exists only
        for the duration of the call - never in the database, never in an API
        response. Hop-by-hop headers are dropped: httpx owns those, and letting
        a user set them is how request smuggling starts.
        """
        from app.services.http_tool import FORBIDDEN_HEADERS, resolve_env

        headers: dict[str, str] = {}
        for key, value in (server.headers or {}).items():
            if key.lower() in FORBIDDEN_HEADERS:
                logger.warning(
                    "MCP server %s: ignoring forbidden header %s", server.slug, key
                )
                continue
            headers[key] = resolve_env(str(value))
        return headers

    def _target(self, server: MCPServer):
        """What `mcp.Client` should connect to for this server.

        Returns a StdioServerParameters for a local subprocess, or a
        streamable-HTTP transport for a remote endpoint. Both are single-use;
        a fresh one is built per operation.
        """
        if server.transport == "stdio":
            return self._stdio_target(server)
        if server.transport == "http":
            return self._http_target(server)
        raise MCPError(
            f"Transport '{server.transport}' is not supported; use 'stdio' or 'http'."
        )

    def _stdio_target(self, server: MCPServer):
        from mcp import StdioServerParameters

        if not server.command:
            raise MCPError(f"MCP server '{server.name}' has no command configured.")
        if shutil.which(server.command) is None and not os.path.exists(server.command):
            raise MCPError(f"Command not found: {server.command}")
        return StdioServerParameters(
            command=server.command,
            args=list(server.args or []),
            env=self._build_env(server),
            cwd=server.cwd or None,
        )

    def _http_target(self, server: MCPServer):
        from mcp.client.streamable_http import (
            create_mcp_http_client,
            streamable_http_client,
        )

        from app.services.http_tool import HTTPToolError, assert_safe_url

        if not server.url:
            raise MCPError(f"MCP server '{server.name}' has no URL configured.")
        try:
            assert_safe_url(server.url)
        except HTTPToolError as exc:
            raise MCPError(str(exc)) from exc

        headers = self._build_headers(server)
        http_client = create_mcp_http_client(headers=headers or None)
        # Already the SDK's default; pinned because the whole point of
        # assert_safe_url is undone by a redirect to an address it never saw.
        http_client.follow_redirects = False
        return streamable_http_client(server.url, http_client=http_client)

    # -- discovery --------------------------------------------------------

    async def discover(
        self, server: MCPServer, *, use_cache: bool = True
    ) -> list[DiscoveredTool]:
        """List the tools a server offers, caching the result."""
        if use_cache:
            cached = self._cache.get(server.id)
            if cached and cached[0] > time.monotonic():
                return cached[1]

        from mcp import Client

        target = self._target(server)
        try:
            async with Client(
                target, read_timeout_seconds=self.settings.mcp_timeout_seconds
            ) as client:
                result = await client.list_tools()
        except MCPError:
            raise
        except Exception as exc:  # noqa: BLE001 - surface any transport failure uniformly
            raise MCPError(f"Could not connect to '{server.name}': {exc}") from exc

        tools: list[DiscoveredTool] = []
        for tool in result.tools:
            qualified = qualify(server.slug, tool.name)
            if not _TOOL_NAME_RE.match(qualified):
                logger.warning(
                    "skipping MCP tool with unusable name: %s", qualified
                )
                continue
            tools.append(
                DiscoveredTool(
                    server_id=server.id,
                    server_slug=server.slug,
                    tool_name=tool.name,
                    qualified_name=qualified,
                    description=tool.description or f"MCP tool {tool.name}",
                    input_schema=tool.input_schema or {"type": "object", "properties": {}},
                )
            )

        self._cache[server.id] = (time.monotonic() + self._cache_ttl, tools)
        return tools

    def invalidate(self, server_id: str) -> None:
        self._cache.pop(server_id, None)

    # -- invocation -------------------------------------------------------

    async def call_tool(
        self, server: MCPServer, tool_name: str, arguments: dict[str, Any]
    ) -> str:
        """Invoke one tool and flatten the result to text for the model."""
        from mcp import Client

    # Financial Intelligence tools use a strict allowlist of arguments.
    #  Reject unexpected fields instead of silently ignoring them.
        if server.slug == "financial-intelligence":
            allowed_arguments = {
            "financial_server_status": set(),
            "get_my_holdings": set(),
            "get_my_watchlist": set(),
            "get_my_transactions": {"symbol"},
        }

        allowed = allowed_arguments.get(tool_name)

        if allowed is not None:
            unexpected = set(arguments) - allowed

            if unexpected:
                raise MCPError(
                    f"Unexpected argument(s) for '{tool_name}': "
                    f"{', '.join(sorted(unexpected))}"
                )

        target = self._target(server)
        try:
            async with Client(
                target, read_timeout_seconds=self.settings.mcp_timeout_seconds
            ) as client:
                result = await client.call_tool(
                    tool_name,
                    arguments,
                    read_timeout_seconds=self.settings.mcp_timeout_seconds,
                )
        except MCPError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise MCPError(f"MCP call '{tool_name}' failed: {exc}") from exc

        text = _flatten(result)
        if result.is_error:
            raise MCPError(text or f"MCP tool '{tool_name}' reported an error.")
        return text


def _flatten(result: Any) -> str:
    """Reduce a CallToolResult to a plain string."""
    parts: list[str] = []
    for block in result.content or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
        else:
            parts.append(f"[{getattr(block, 'type', 'content')}]")
    if not parts and result.structured_content is not None:
        return str(result.structured_content)
    return "\n".join(parts)


manager = MCPManager()
