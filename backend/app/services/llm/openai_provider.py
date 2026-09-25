"""OpenAI adapter, and the base for OpenAI-compatible providers.

The only module in the codebase that imports the `openai` SDK.

Built on Chat Completions rather than the newer Responses API. Both are
supported; Chat Completions maps one-to-one onto this codebase's neutral
message format (messages in, `tool_calls` out, `role: "tool"` results back),
whereas Responses would need its item model translated in both directions for
no behavioural gain here.

Chat Completions is also the de-facto interchange format, so a vendor that
implements it needs only a different `base_url` rather than an adapter of its
own - see `groq_provider.py`.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from app.services.llm.base import (
    BaseLLMProvider,
    LLMMessage,
    LLMResponse,
    ProviderNotConfigured,
    StreamEvent,
    ToolCall,
    ToolSpec,
)
from app.services.llm.schemas import sanitize_for_openai

logger = logging.getLogger(__name__)

# Some reasoning models refuse function tools on Chat Completions unless
# reasoning is switched off:
#   "Function tools with reasoning_effort are not supported for <model> in
#    /v1/chat/completions. ... or set reasoning_effort to 'none'."
# Which models behave this way is not discoverable up front and changes as
# models ship, so rather than maintain a list we retry once on exactly this
# error. Models that support tools *and* reasoning keep reasoning.
_REASONING_TOOL_CONFLICT = "reasoning_effort"

# Reasoning models reject `temperature` with a 400. Agents still carry one
# (other models use it), so drop it for these rather than fail the request.
_NO_SAMPLING_PREFIXES = ("o1", "o3", "o4")


def _accepts_sampling(model: str) -> bool:
    return not model.startswith(_NO_SAMPLING_PREFIXES)


class OpenAIProvider(BaseLLMProvider):
    """Chat Completions provider.

    Subclass it for an OpenAI-compatible vendor by overriding the two class
    attributes below; everything else - message translation, tool definitions,
    streaming reassembly - is shared, so there is one code path to maintain and
    one to test.
    """

    name = "openai"
    supports_tools = True

    #: None means the SDK's own default endpoint.
    base_url: str | None = None

    def __init__(self, api_key: str | None) -> None:
        self._api_key = api_key
        self._client: Any = None

    def is_available(self) -> bool:
        return bool(self._api_key)

    def _get_client(self):
        if not self.is_available():
            raise ProviderNotConfigured(
                f"No API key is configured for provider '{self.name}'. Add one "
                "in the agent's configuration."
            )
        if self._client is None:
            from openai import AsyncOpenAI

            kwargs: dict[str, Any] = {"api_key": self._api_key}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = AsyncOpenAI(**kwargs)
        return self._client

    # -- translation ------------------------------------------------------

    def _to_openai_messages(self, system: str, messages: list[LLMMessage]) -> list[dict]:
        """Neutral messages to Chat Completions messages.

        Tool results become their own top-level `role: "tool"` messages rather
        than blocks inside the preceding user turn.
        """
        out: list[dict] = []
        if system:
            out.append({"role": "system", "content": system})

        for m in messages:
            if m.raw is not None:
                # An assistant turn this provider produced - replay verbatim so
                # tool_call ids line up with the results that follow.
                out.append(m.raw)
                continue

            if isinstance(m.content, str):
                out.append({"role": m.role, "content": m.content})
                continue

            text_parts: list[str] = []
            for b in m.content:
                if b.get("type") == "tool_result":
                    out.append(
                        {
                            "role": "tool",
                            "tool_call_id": b["tool_call_id"],
                            "content": str(b.get("content", "")),
                        }
                    )
                else:
                    text_parts.append(b.get("text", ""))
            if text_parts:
                out.append({"role": m.role, "content": "".join(text_parts)})

        return out

    def _build_kwargs(
        self,
        model: str,
        system: str,
        messages: list[LLMMessage],
        tools: list[ToolSpec] | None,
        temperature: float,
        max_tokens: int,
    ) -> dict:
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": self._to_openai_messages(system, messages),
            # `max_tokens` is deprecated on Chat Completions; the newer
            # parameter is accepted by current and recent models alike.
            "max_completion_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": sanitize_for_openai(t.input_schema),
                    },
                }
                for t in tools
            ]
        if _accepts_sampling(model):
            kwargs["temperature"] = temperature
        return kwargs

    async def _create(self, client, kwargs: dict, **extra):
        """Call the API, retrying once if tools and reasoning conflict."""
        try:
            return await client.chat.completions.create(**kwargs, **extra)
        except Exception as exc:
            message = str(exc)
            if (
                _REASONING_TOOL_CONFLICT not in message
                or "tools" not in kwargs
                or kwargs.get("reasoning_effort") == "none"
            ):
                raise
            logger.info(
                "openai: %s rejects tools with reasoning; retrying with "
                "reasoning_effort='none'",
                kwargs.get("model"),
            )
            return await client.chat.completions.create(
                **kwargs, **extra, reasoning_effort="none"
            )

    @staticmethod
    def _parse_arguments(raw: str | None) -> dict:
        """Tool arguments arrive as a JSON *string* and may be malformed."""
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("openai: could not parse tool arguments: %r", raw)
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @classmethod
    def _parse(cls, message: Any, usage: Any, finish_reason: str | None) -> LLMResponse:
        tool_calls = [
            ToolCall(
                id=tc.id,
                name=tc.function.name,
                arguments=cls._parse_arguments(tc.function.arguments),
            )
            for tc in (message.tool_calls or [])
        ]
        # Echo the assistant turn back verbatim next round; model_dump drops
        # nulls so the API does not reject unset fields.
        raw_content = message.model_dump(exclude_none=True)
        return LLMResponse(
            text=message.content or "",
            tool_calls=tool_calls,
            stop_reason=finish_reason,
            usage={
                "input_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                "output_tokens": getattr(usage, "completion_tokens", 0) or 0,
            }
            if usage
            else {},
            raw_content=raw_content,
        )

    # -- interface --------------------------------------------------------

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
        client = self._get_client()
        kwargs = self._build_kwargs(
            model, system, messages, tools, temperature, max_tokens
        )
        completion = await self._create(client, kwargs)
        choice = completion.choices[0]
        return self._parse(choice.message, completion.usage, choice.finish_reason)

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
        client = self._get_client()
        kwargs = self._build_kwargs(
            model, system, messages, tools, temperature, max_tokens
        )
        stream = await self._create(
            client, kwargs, stream=True, stream_options={"include_usage": True}
        )

        text_parts: list[str] = []
        # Tool calls stream in fragments keyed by index: the id and name arrive
        # once, then the arguments accumulate across chunks.
        partial: dict[int, dict[str, Any]] = {}
        finish_reason: str | None = None
        usage: Any = None

        async for chunk in stream:
            if chunk.usage is not None:
                usage = chunk.usage
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            if choice.finish_reason:
                finish_reason = choice.finish_reason

            delta = choice.delta
            if delta is None:
                continue

            if delta.content:
                text_parts.append(delta.content)
                yield StreamEvent(type="text", text=delta.content)

            for tc in delta.tool_calls or []:
                slot = partial.setdefault(
                    tc.index, {"id": None, "name": None, "arguments": ""}
                )
                if tc.id:
                    slot["id"] = tc.id
                if tc.function and tc.function.name:
                    slot["name"] = tc.function.name
                if tc.function and tc.function.arguments:
                    slot["arguments"] += tc.function.arguments

        text = "".join(text_parts)
        tool_calls = [
            ToolCall(
                id=slot["id"] or f"call_{index}",
                name=slot["name"] or "",
                arguments=self._parse_arguments(slot["arguments"]),
            )
            for index, slot in sorted(partial.items())
            if slot["name"]
        ]
        # Rebuild the assistant turn for replay, matching the non-streaming shape.
        raw_content: dict[str, Any] = {"role": "assistant"}
        if text:
            raw_content["content"] = text
        if tool_calls:
            raw_content["tool_calls"] = [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                }
                for c in tool_calls
            ]

        yield StreamEvent(
            type="done",
            response=LLMResponse(
                text=text,
                tool_calls=tool_calls,
                stop_reason=finish_reason,
                usage={
                    "input_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                    "output_tokens": getattr(usage, "completion_tokens", 0) or 0,
                }
                if usage
                else {},
                raw_content=raw_content,
            ),
        )
