"""Deterministic offline provider.

Not a placeholder for a real provider - it earns its place twice over:
tests stay hermetic (no network, no key, no spend), and a Garak run can
exercise the full endpoint + tool-permission path without paying for hundreds
of adversarial completions. It is also the fallback when no API key is set, so
a fresh clone is demoable immediately.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator

from app.services.llm.base import (
    BaseLLMProvider,
    LLMMessage,
    LLMResponse,
    StreamEvent,
    ToolCall,
    ToolSpec,
)

_ARITHMETIC = re.compile(r"\d+\s*[-+*/]\s*\d+")


def _synthesize_args(tool: ToolSpec, user_text: str) -> dict:
    """Fill a tool's required string properties from the user's message.

    Uses whatever follows the tool name ("run kb_search security" -> "security")
    so an offline demo passes a sensible argument rather than the whole
    sentence. A real model reads the schema and decides for itself.
    """
    bare = tool.name.split("__", 2)[-1]
    _, _, remainder = user_text.lower().partition(bare.lower())
    value = remainder.strip(" ?.!,:;\"'") or user_text

    schema = tool.input_schema or {}
    properties = schema.get("properties", {}) or {}
    args: dict = {}
    for field in schema.get("required", []) or []:
        if properties.get(field, {}).get("type") == "string":
            args[field] = value
    return args


class MockProvider(BaseLLMProvider):
    name = "mock"
    supports_tools = True
    requires_api_key = False

    def is_available(self) -> bool:
        return True

    @staticmethod
    def _persona(system: str) -> str:
        """First line of the system prompt, so different agents read differently."""
        if not system.strip():
            return "an assistant with no system instructions"
        return system.strip().splitlines()[0][:120]

    def _respond(
        self,
        system: str,
        messages: list[LLMMessage],
        tools: list[ToolSpec] | None,
    ) -> LLMResponse:
        tools = tools or []
        last = messages[-1] if messages else None

        # A turn carrying tool results means the loop already ran a tool; produce
        # the final answer from those results.
        if last is not None and isinstance(last.content, list):
            results = [
                b.get("content", "")
                for b in last.content
                if b.get("type") == "tool_result"
            ]
            if results:
                joined = "; ".join(str(r) for r in results)
                return LLMResponse(
                    text=f"[mock] Tool returned: {joined}", stop_reason="end_turn"
                )

        user_text = ""
        for m in reversed(messages):
            if m.role == "user" and isinstance(m.content, str):
                user_text = m.content
                break

        tool_names = {t.name for t in tools}
        if "calculator" in tool_names and _ARITHMETIC.search(user_text):
            expr = _ARITHMETIC.search(user_text).group(0)
            return LLMResponse(
                text="",
                tool_calls=[
                    ToolCall(
                        id=f"mock_{uuid.uuid4().hex[:8]}",
                        name="calculator",
                        arguments={"expression": expr},
                    )
                ],
                stop_reason="tool_use",
            )
        if "current_time" in tool_names and re.search(
            r"\b(time|date|today|now)\b", user_text, re.I
        ):
            return LLMResponse(
                text="",
                tool_calls=[
                    ToolCall(
                        id=f"mock_{uuid.uuid4().hex[:8]}",
                        name="current_time",
                        arguments={},
                    )
                ],
                stop_reason="tool_use",
            )

        # MCP tools arrive namespaced as mcp__<server>__<tool>. Calling the
        # first one whose bare name appears in the message is enough to drive
        # the MCP path offline; a real model picks by description.
        for tool in tools:
            if not tool.name.startswith("mcp__"):
                continue
            bare = tool.name.split("__", 2)[-1]
            if bare.lower() in user_text.lower():
                return LLMResponse(
                    text="",
                    tool_calls=[
                        ToolCall(
                            id=f"mock_{uuid.uuid4().hex[:8]}",
                            name=tool.name,
                            arguments=_synthesize_args(tool, user_text),
                        )
                    ],
                    stop_reason="tool_use",
                )

        return LLMResponse(
            text=(
                f'[mock] Acting as: "{self._persona(system)}". '
                f'You said: "{user_text}". '
                f"{len(tool_names)} tool(s) available to me."
            ),
            stop_reason="end_turn",
        )

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
        return self._respond(system, messages, tools)

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
        response = self._respond(system, messages, tools)
        for word in response.text.split(" "):
            yield StreamEvent(type="text", text=word + " ")
        yield StreamEvent(type="done", response=response)
