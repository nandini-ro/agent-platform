"""Execution of user-defined HTTP tools.

This module is the security boundary for a feature that lets someone define,
through a web form, a request the server will then make on their behalf. Three
things are enforced here:

1. **No code execution.** Templates are filled by explicit regex substitution,
   never `str.format`, which on an attacker-supplied template can reach object
   internals (`{x.__class__.__init__.__globals__}`).
2. **No SSRF.** Every resolved address is checked before the request is made,
   so a tool cannot be pointed at loopback, a private range, or a cloud
   metadata endpoint. Redirects are not followed, since a redirect would
   escape the check.
3. **No secret storage.** A header may reference `{{env:NAME}}`; the value is
   read from the backend's environment at call time and never persisted or
   returned through the API.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import socket
from typing import Any
from urllib.parse import quote, urlparse

from fastapi import params
from httpcore import request
from httpcore import request

from app.config.settings import get_settings
from app.models.http_tool import HTTPTool
from app.services.tool_registry import BaseTool, ToolError

logger = logging.getLogger(__name__)

#: Tool names the providers will accept.
TOOL_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

_PARAM_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
_ENV_RE = re.compile(r"\{\{env:([A-Z_][A-Z0-9_]*)\}\}")

ALLOWED_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")

# Headers a definition may not set - they are the transport's business, and
# letting a tool forge them invites request smuggling.
FORBIDDEN_HEADERS = frozenset(
    {"host", "content-length", "transfer-encoding", "connection", "upgrade"}
)


class HTTPToolError(ToolError):
    """A user-defined HTTP tool could not be run, or the request was refused."""


# ---------------------------------------------------------------------------
# URL safety
# ---------------------------------------------------------------------------


def assert_safe_url(url: str) -> None:
    """Reject anything that is not a plain call to a public host.

    Raises HTTPToolError with a reason the UI can show.
    """
    settings = get_settings()
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise HTTPToolError(
            f"URL scheme must be http or https, got {parsed.scheme or 'none'!r}."
        )
    host = parsed.hostname
    if not host:
        raise HTTPToolError("URL has no host.")

    allowed = settings.http_tool_allowed_host_list
    if allowed and host.lower() not in allowed:
        raise HTTPToolError(
            f"Host {host!r} is not in HTTP_TOOL_ALLOWED_HOSTS."
        )

    if settings.http_tool_allow_private_networks:
        return

    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise HTTPToolError(f"Could not resolve host {host!r}: {exc}") from exc

    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        ):
            raise HTTPToolError(
                f"Refusing to call {host!r}: it resolves to the non-public "
                f"address {address}. Set HTTP_TOOL_ALLOW_PRIVATE_NETWORKS=true "
                "only if you intend tools to reach internal services."
            )


# ---------------------------------------------------------------------------
# Templating
# ---------------------------------------------------------------------------


def substitute(template: str, values: dict[str, Any], *, encode: bool) -> str:
    """Replace `{param}` placeholders with supplied values.

    Regex substitution rather than `str.format`: a template is user-supplied,
    and `str.format` would let `{x.__class__}` walk into object internals.
    Unknown placeholders are left alone rather than raising, so a partially
    optional template still works.
    """

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            return match.group(0)
        raw = values[key]
        text = raw if isinstance(raw, str) else json.dumps(raw)
        return quote(text, safe="") if encode else text

    return _PARAM_RE.sub(replace, template)


def resolve_env(value: str) -> str:
    """Replace `{{env:NAME}}` with the backend's environment value."""

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        resolved = os.environ.get(name)
        if resolved is None:
            raise HTTPToolError(
                f"This tool needs the environment variable {name}, which is not set."
            )
        return resolved

    return _ENV_RE.sub(replace, value)


def redact_env_refs(value: str) -> str:
    """What the API returns: the reference, never the resolved secret."""
    return value


# ---------------------------------------------------------------------------
# The tool
# ---------------------------------------------------------------------------


class UserHTTPTool(BaseTool):
    """A BaseTool built from a database row.

    It goes through exactly the same runtime gate as a code-defined tool:
    permitted-for-this-agent, schema-validated, then executed under a timeout.
    """

    def __init__(self, row: HTTPTool) -> None:
        self.row = row
        self.name = row.name
        self.description = row.description or f"Call {row.url_template}"
        self.input_schema = row.parameters or {"type": "object", "properties": {}}
        self.settings = get_settings()

    def _build_request(self, arguments: dict[str, Any]) -> dict[str, Any]:
        row = self.row

        url = substitute(row.url_template, arguments, encode=True)
        assert_safe_url(url)

        headers: dict[str, str] = {}
        for key, value in (row.headers or {}).items():
            if key.lower() in FORBIDDEN_HEADERS:
                raise HTTPToolError(f"Header {key!r} may not be set by a tool.")
            filled = substitute(str(value), arguments, encode=False)
            headers[key] = resolve_env(filled)

        params = {
            key: substitute(str(value), arguments, encode=False)
            for key, value in (row.query_template or {}).items()
        }

        request: dict[str, Any] = {
                "method": row.method.upper(),
                "url": url,
                "headers": headers,}

        if params:
            request["params"] = params
        

        if row.body_template:
            body = resolve_env(substitute(row.body_template, arguments, encode=False))
            request["content"] = body.encode("utf-8")
            headers.setdefault("Content-Type", "application/json")
        return request

    async def execute(self, arguments: dict[str, Any]) -> str:
        import httpx

        request = self._build_request(arguments)
        timeout = min(self.row.timeout_seconds or 10, self.settings.http_tool_timeout_seconds)

        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                # A redirect would bypass the SSRF check performed above.
                follow_redirects=False,
            ) as client:
                response = await client.request(**request)
        except httpx.TimeoutException as exc:
            raise HTTPToolError(f"Request timed out after {timeout}s.") from exc
        except httpx.HTTPError as exc:
            raise HTTPToolError(f"Request failed: {exc}") from exc

        return self._format_response(response)

    def _format_response(self, response: Any) -> str:
        cap = self.settings.http_tool_max_response_chars
        text = response.text or ""

        # Status is checked before response_path: a non-2xx body is rarely the
        # JSON the path expects, and "not JSON" would hide the real failure.
        if 300 <= response.status_code < 400:
            # Redirects are deliberately not followed - doing so would escape
            # the SSRF check performed against the original URL. Say so, rather
            # than handing back a redirect page as though it were data.
            location = response.headers.get("location", "")
            raise HTTPToolError(
                f"The endpoint redirected (HTTP {response.status_code})"
                + (f" to {location}" if location else "")
                + ". Redirects are not followed; point the tool at the final "
                "URL instead."
            )

        if response.status_code >= 400:
            # Returned, not raised: the model should see the API's own error and
            # be able to correct its arguments.
            return f"HTTP {response.status_code}: {text[:cap]}"

        if self.row.response_path:
            try:
                extracted = _dig(response.json(), self.row.response_path)
            except (ValueError, json.JSONDecodeError):
                raise HTTPToolError(
                    f"response_path {self.row.response_path!r} was set but the "
                    "response is not JSON."
                ) from None
            text = extracted if isinstance(extracted, str) else json.dumps(extracted)

        return text[:cap] if len(text) <= cap else text[:cap] + "\n...[truncated]"


def _dig(data: Any, path: str) -> Any:
    """Walk a dotted path, e.g. "data.items.0.title"."""
    current = data
    for segment in path.split("."):
        if isinstance(current, list):
            try:
                current = current[int(segment)]
            except (ValueError, IndexError) as exc:
                raise HTTPToolError(f"No element {segment!r} in the response list.") from exc
        elif isinstance(current, dict):
            if segment not in current:
                raise HTTPToolError(f"No field {segment!r} in the response.")
            current = current[segment]
        else:
            raise HTTPToolError(f"Cannot read {segment!r} from the response.")
    return current


# ---------------------------------------------------------------------------
# Validation used by the API when a tool is created or edited
# ---------------------------------------------------------------------------


def validate_definition(
    *, name: str, method: str, url_template: str, headers: dict, parameters: dict
) -> None:
    """Reject a definition that could not work, or should not be allowed."""
    from app.services.tool_registry import registry

    if not TOOL_NAME_RE.match(name):
        raise HTTPToolError(
            "Tool name must be 1-64 characters of letters, digits, underscore "
            "or hyphen."
        )
    if registry.get(name) is not None:
        raise HTTPToolError(f"{name!r} is already a built-in tool name.")
    if method.upper() not in ALLOWED_METHODS:
        raise HTTPToolError(f"Method must be one of {', '.join(ALLOWED_METHODS)}.")
    for key in headers or {}:
        if key.lower() in FORBIDDEN_HEADERS:
            raise HTTPToolError(f"Header {key!r} may not be set by a tool.")
    if parameters and parameters.get("type") != "object":
        raise HTTPToolError("Parameters schema must be a JSON Schema object.")

    # Template placeholders must be declared, or the model can never fill them.
    declared = set((parameters or {}).get("properties", {}))
    used = set(_PARAM_RE.findall(url_template))
    unknown = used - declared
    if unknown:
        raise HTTPToolError(
            f"URL uses undeclared parameter(s): {', '.join(sorted(unknown))}. "
            "Add them to the parameters schema."
        )

    # Validate the URL shape now; the address check happens per call, since DNS
    # can change between definition and use.
    parsed = urlparse(_PARAM_RE.sub("x", url_template))
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise HTTPToolError("URL must be an absolute http:// or https:// address.")
