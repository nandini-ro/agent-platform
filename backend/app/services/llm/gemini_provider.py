"""Google Gemini adapter.

The only module in the codebase that imports the `google-genai` SDK. Note that
is `google-genai`, the current SDK - not the superseded `google-generativeai`.

Gemini differs from the other two providers in three ways that shape this file:

* The assistant role is called ``model``.
* A function result is matched to its call by **name**, not by an id, so the
  neutral tool-result block carries the tool name alongside its id.
* Tool schemas are an OpenAPI subset. Several ordinary JSON Schema keywords -
  ``title`` among them, which Pydantic puts on every property - are rejected
  with a 400, so schemas go through `sanitize_for_gemini` first.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from types import SimpleNamespace
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
from app.services.llm.schemas import sanitize_for_gemini

logger = logging.getLogger(__name__)


class GeminiProvider(BaseLLMProvider):
    name = "gemini"
    supports_tools = True

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
            from google import genai

            self._client = genai.Client(api_key=self._api_key)
        return self._client

    # -- translation ------------------------------------------------------

    def _to_contents(self, messages: list[LLMMessage]) -> list[Any]:
        from google.genai import types

        contents: list[Any] = []
        for m in messages:
            if m.raw is not None:
                # A model turn this provider produced (carries function calls
                # and any thought signatures); replay it untouched.
                contents.append(m.raw)
                continue

            role = "model" if m.role == "assistant" else "user"

            if isinstance(m.content, str):
                contents.append(
                    types.Content(role=role, parts=[types.Part.from_text(text=m.content)])
                )
                continue

            function_parts: list[Any] = []
            text_parts: list[str] = []
            for b in m.content:
                if b.get("type") == "tool_result":
                    function_parts.append(
                        types.Part.from_function_response(
                            # Matched by name, which is why the runtime records
                            # it on the block.
                            name=b.get("name") or b.get("tool_call_id", "tool"),
                            # The response must be an object, not a bare string.
                            response=(
                                {"error": b.get("content", "")}
                                if b.get("is_error")
                                else {"result": b.get("content", "")}
                            ),
                        )
                    )
                else:
                    text_parts.append(b.get("text", ""))

            if function_parts:
                contents.append(types.Content(role="tool", parts=function_parts))
            if text_parts:
                contents.append(
                    types.Content(
                        role=role, parts=[types.Part.from_text(text="".join(text_parts))]
                    )
                )

        return contents

    def _build_config(
        self,
        system: str,
        tools: list[ToolSpec] | None,
        temperature: float,
        max_tokens: int,
    ):
        from google.genai import types

        declarations = []
        for t in tools or []:
            parameters = sanitize_for_gemini(t.input_schema)
            declaration = types.FunctionDeclaration(
                name=t.name,
                description=t.description,
            )
            # A no-argument tool must omit `parameters` entirely; an empty
            # object is rejected.
            if parameters is not None:
                declaration.parameters_json_schema = parameters
            declarations.append(declaration)

        config = types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
            # We run the tool loop ourselves so the permission gate always
            # applies; the SDK must never call anything on our behalf. Set
            # unconditionally - the SDK treats an unset value as "AFC enabled"
            # and logs a warning on every request, tools or not.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True
            ),
        )
        if system:
            config.system_instruction = system
        if declarations:
            config.tools = [types.Tool(function_declarations=declarations)]
        return config

    @staticmethod
    def _parse(response: Any) -> LLMResponse:
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        raw_content = None
        finish_reason = None

        candidates = response.candidates or []
        if candidates:
            candidate = candidates[0]
            raw_content = candidate.content
            finish_reason = (
                candidate.finish_reason.value
                if hasattr(candidate.finish_reason, "value")
                else candidate.finish_reason
            )
            for index, part in enumerate(getattr(candidate.content, "parts", None) or []):
                if getattr(part, "thought", None):
                    continue  # reasoning parts are not user-visible output
                if part.text:
                    text_parts.append(part.text)
                if part.function_call:
                    call = part.function_call
                    tool_calls.append(
                        ToolCall(
                            # Gemini may omit an id; the runtime needs a stable
                            # one to pair the result with.
                            id=call.id or f"gemini_{call.name}_{index}",
                            name=call.name or "",
                            arguments=dict(call.args or {}),
                        )
                    )

        usage = response.usage_metadata
        return LLMResponse(
            text="".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=str(finish_reason) if finish_reason else None,
            usage={
                "input_tokens": getattr(usage, "prompt_token_count", 0) or 0,
                "output_tokens": getattr(usage, "candidates_token_count", 0) or 0,
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
        response = await client.aio.models.generate_content(
            model=model,
            contents=self._to_contents(messages),
            config=self._build_config(system, tools, temperature, max_tokens),
        )
        return self._parse(response)

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
        stream = await client.aio.models.generate_content_stream(
            model=model,
            contents=self._to_contents(messages),
            config=self._build_config(system, tools, temperature, max_tokens),
        )

        from google.genai import types

        last: Any = None
        text_parts: list[str] = []
        # Every non-thought part seen, in order, so the assistant turn can be
        # rebuilt for replay. Function calls can arrive in ANY chunk - reading
        # only the final one silently drops them.
        collected: list[Any] = []

        async for chunk in stream:
            last = chunk
            for candidate in chunk.candidates or []:
                for part in getattr(candidate.content, "parts", None) or []:
                    if getattr(part, "thought", None):
                        continue
                    if part.function_call:
                        collected.append(part)
                    if part.text:
                        text_parts.append(part.text)
                        collected.append(part)
                        yield StreamEvent(type="text", text=part.text)

        if last is None:
            yield StreamEvent(type="done", response=LLMResponse(text=""))
            return

        # Reparse against everything accumulated, not just the last chunk.
        merged = SimpleNamespace(
            candidates=[
                SimpleNamespace(
                    content=types.Content(role="model", parts=collected),
                    finish_reason=(
                        last.candidates[0].finish_reason if last.candidates else None
                    ),
                )
            ],
            usage_metadata=last.usage_metadata,
        )
        response = self._parse(merged)
        response.text = "".join(text_parts)
        yield StreamEvent(type="done", response=response)
