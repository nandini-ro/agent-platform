"""Provider registry - the single lookup point for provider instances.

Adding a provider is one import plus one dict entry; no other module changes.
The agent form reads this catalogue from /api/agents/providers, so a new
provider appears in the UI without any frontend change.
"""

from __future__ import annotations

from functools import lru_cache

from app.config.settings import get_settings
from app.services.llm.base import BaseLLMProvider
from app.services.llm.gemini_provider import GeminiProvider
from app.services.llm.groq_provider import GroqProvider
from app.services.llm.mock_provider import MockProvider
from app.services.llm.openai_provider import OpenAIProvider


@lru_cache
def _providers() -> dict[str, BaseLLMProvider]:
    settings = get_settings()
    return {
        "openai": OpenAIProvider(api_key=settings.openai_api_key),
        "gemini": GeminiProvider(api_key=settings.gemini_api_key),
        "groq": GroqProvider(api_key=settings.groq_api_key),
        "mock": MockProvider(),
    }


def get_provider(name: str) -> BaseLLMProvider:
    providers = _providers()
    if name not in providers:
        raise KeyError(f"Unknown provider '{name}'. Known: {sorted(providers)}")
    return providers[name]


def list_providers() -> list[dict]:
    """Catalogue for the agent-creation form."""
    return [
        {
            "name": name,
            "available": p.is_available(),
            "supports_tools": p.supports_tools,
            "models": _MODELS.get(name, []),
        }
        for name, p in _providers().items()
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
