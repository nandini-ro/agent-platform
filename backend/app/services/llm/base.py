"""Provider-neutral LLM interface.

Nothing above this module imports a vendor SDK. Adding a provider means adding
one file here and registering it - no changes to AgentRuntime or the API layer.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class LLMMessage:
    """One turn of conversation in provider-neutral form.

    `content` is either plain text or a list of neutral blocks. Tool results are
    carried as blocks so a provider can translate them into its own wire format.
    """

    role: Literal["user", "assistant"]
    content: str | list[dict[str, Any]]
    # Optional provider-native payload. When a provider produced this turn it
    # may stash its own block list here and replay it verbatim, preserving
    # details (tool-use block ids) that the neutral form would drop. Providers
    # that did not author it ignore it and fall back to `content`.
    raw: Any = None


@dataclass
class ToolSpec:
    """A tool offered to the model. `input_schema` is JSON Schema."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    # "end_turn" | "tool_use" | "max_tokens" | other provider-specific value
    stop_reason: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    # Provider-native assistant content, echoed back verbatim on the next turn
    # so multi-step tool loops keep provider-side fidelity (e.g. block ids).
    raw_content: Any = None


@dataclass
class StreamEvent:
    type: Literal["text", "tool_call", "done", "error"]
    text: str = ""
    response: LLMResponse | None = None
    error: str = ""


class ProviderNotConfigured(RuntimeError):
    """Raised when a provider is selected but its credentials are absent."""


class BaseLLMProvider(ABC):
    """Contract every provider adapter implements."""

    name: str = "base"
    supports_tools: bool = False

    @abstractmethod
    def is_available(self) -> bool:
        """True when the provider has everything it needs to make a call."""

    @abstractmethod
    async def generate(
        self,
        *,
        model: str,
        system: str,
        messages: list[LLMMessage],
        tools: list[ToolSpec] | None = None,
        temperature: float = 1.0,
        max_tokens: int = 2048,
    ) -> LLMResponse:
        """Single non-streaming completion."""

    @abstractmethod
    async def stream(
        self,
        *,
        model: str,
        system: str,
        messages: list[LLMMessage],
        tools: list[ToolSpec] | None = None,
        temperature: float = 1.0,
        max_tokens: int = 2048,
    ) -> AsyncIterator[StreamEvent]:
        """Token-by-token completion, terminated by a `done` event."""
