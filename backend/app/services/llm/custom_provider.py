"""Custom endpoint: any OpenAI-compatible Chat Completions server.

The agent supplies the base URL (LiteLLM, vLLM, Ollama, an internal gateway -
anything that serves `POST {base_url}/chat/completions`) and, optionally, the
key that endpoint expects. The system prompt, the conversation and the agent's
tools are sent exactly as they are to OpenAI, through the shared adapter.

Unlike the hosted vendors the key is optional: plenty of self-hosted endpoints
take none. When one is stored it is encrypted like any provider key.

The model is optional too. A gateway usually knows which model a key is for,
so a blank model is sent as-is and the gateway picks. And when a gateway
refuses the configured model for the key (LiteLLM's `key_model_access_denied`,
which lists the models the key may use), the call is retried with the
gateway's default and then each listed model; the first that answers becomes
`resolved_model`, which the runtime saves on the agent.
"""

from __future__ import annotations

import ast
import logging
import re
from typing import Any
from urllib.parse import urlsplit

from app.services.llm.base import ProviderNotConfigured
from app.services.llm.openai_provider import OpenAIProvider, client_options

# The SDK refuses to build a client without some key. Endpoints that need none
# ignore the resulting "Authorization: Bearer" header.
_NO_KEY = "not-required"

logger = logging.getLogger(__name__)

# LiteLLM's refusal: "... This key can only access models=['a', 'b']. Tried to
# access x". The list is the only place such a gateway says what the key may
# use; its /models route may itself be closed to the key.
_ACCESS_DENIED = "key_model_access_denied"
_ALLOWED_MODELS = re.compile(r"models=(\[[^\]]*\])")


def allowed_models(exc: Exception) -> list[str] | None:
    """The models a gateway says the key may use, or None if `exc` is not a
    model-access refusal."""
    if getattr(exc, "status_code", None) != 403 or _ACCESS_DENIED not in str(exc):
        return None
    match = _ALLOWED_MODELS.search(str(exc))
    if not match:
        return []
    try:
        listed = ast.literal_eval(match.group(1))
    except (ValueError, SyntaxError):
        return []
    return [m for m in listed if isinstance(m, str)]


def normalize_base_url(raw: str) -> str:
    """Accept the base URL however it was pasted.

    People copy the full completions URL as often as the base; both resolve to
    the same thing. Raises ValueError for anything that is not http(s).
    """
    url = raw.strip().rstrip("/")
    for suffix in ("/chat/completions", "/completions"):
        if url.endswith(suffix):
            url = url[: -len(suffix)].rstrip("/")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("The base URL must start with http:// or https://.")
    return url


class CustomProvider(OpenAIProvider):
    name = "custom"
    requires_api_key = False
    accepts_api_key = True
    requires_base_url = True
    model_optional = True

    def __init__(self, api_key: str | None = None, base_url: str | None = None) -> None:
        super().__init__(api_key)
        self.base_url = base_url

    def is_available(self) -> bool:
        return bool(self.base_url)

    def _get_client(self):
        if not self.base_url:
            raise ProviderNotConfigured(
                "No base URL is configured for this agent's custom endpoint. Add "
                "one in the agent's configuration."
            )
        if self._client is None:
            from openai import AsyncOpenAI

            kwargs: dict[str, Any] = {
                "api_key": self._api_key or _NO_KEY,
                "base_url": self.base_url,
                **client_options(),
            }
            self._client = AsyncOpenAI(**kwargs)
        return self._client

    async def _create(self, client, kwargs: dict, **extra):
        """Call the endpoint, falling back to a model the gateway allows."""
        if self.resolved_model is not None:
            kwargs = {**kwargs, "model": self.resolved_model}
        try:
            return await super()._create(client, kwargs, **extra)
        except Exception as exc:
            listed = allowed_models(exc)
            if listed is None:
                raise
            refusal = exc

        # The gateway's own default first (a blank model), then what it listed.
        # A listed name can still be unservable - an alias the router does not
        # know - so each candidate gets one try.
        for candidate in dict.fromkeys(["", *listed]):
            if candidate == kwargs["model"]:
                continue
            try:
                result = await super()._create(
                    client, {**kwargs, "model": candidate}, **extra
                )
            except Exception as exc:  # noqa: BLE001 - try the next candidate
                if getattr(exc, "status_code", None) not in (400, 403, 404):
                    raise
                continue
            logger.info(
                "custom endpoint refused model %r; using %r instead",
                kwargs["model"],
                candidate or "(endpoint default)",
            )
            self.resolved_model = candidate
            return result
        raise refusal
