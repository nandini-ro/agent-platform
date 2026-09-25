"""Provider registry - the single lookup point for provider instances.

Adding a provider is one import plus one dict entry; no other module changes.
Providers carry no credentials of their own - each agent's key is decrypted
and passed in by AgentRuntime (see app/services/credentials.py).
The agent form reads this catalogue from /api/agents/providers, so a new
provider appears in the UI without any frontend change.
"""

from __future__ import annotations

from app.services.llm.base import BaseLLMProvider
from app.services.llm.gemini_provider import GeminiProvider
from app.services.llm.groq_provider import GroqProvider
from app.services.llm.mock_provider import MockProvider
from app.services.llm.openai_provider import OpenAIProvider

_PROVIDERS: dict[str, type[BaseLLMProvider]] = {
    "openai": OpenAIProvider,
    "gemini": GeminiProvider,
    "groq": GroqProvider,
    "mock": MockProvider,
}


def provider_class(name: str) -> type[BaseLLMProvider]:
    if name not in _PROVIDERS:
        raise KeyError(f"Unknown provider '{name}'. Known: {sorted(_PROVIDERS)}")
    return _PROVIDERS[name]


def get_provider(name: str, api_key: str | None = None) -> BaseLLMProvider:
    """A new provider instance carrying the caller's credential.

    Built per call, never shared: credentials belong to agents, so a cached
    instance would let one agent's key serve another agent's requests.
    """
    cls = provider_class(name)
    if not cls.requires_api_key:
        return cls()
    return cls(api_key=api_key)


def list_providers() -> list[dict]:
    """Catalogue for the agent-creation form."""
    return [
        {
            "name": name,
            "requires_api_key": cls.requires_api_key,
            "supports_tools": cls.supports_tools,
            "models": _MODELS.get(name, []),
        }
        for name, cls in _PROVIDERS.items()
    ]


# Suggestions for the UI dropdown, not a whitelist - the API accepts any model
# string, so a model released after this list was written still works by typing
# its id. Kept short on purpose; each provider publishes the full list.
_MODELS = {
    "openai": [
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "gpt-5.5",
    ],
    "gemini": [
        "gemini-3.5-flash",
        "gemini-3.8-flash",
        "gemini-3.5-flash-lite",
    ],
    "groq": [
        "openai/gpt-oss-120b",
        "openai/gpt-oss-20b",
        "qwen/qwen3.8-27b",
        "groq/compound-mini",
    ],
    "mock": ["mock-1"],
}
