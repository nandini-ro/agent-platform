"""Provider conformance and per-adapter wire-format translation.

Two layers:

* A parametrized contract suite every provider must satisfy, so the adapters
  cannot quietly drift apart.
* Per-adapter translation tests that assert the exact shape each vendor API
  expects, driven through pure functions so the suite needs no network, no API
  keys and no recorded fixtures.
"""

from types import SimpleNamespace

import pytest

from app.services.llm.base import (
    BaseLLMProvider,
    LLMMessage,
    ProviderNotConfigured,
    ToolSpec,
)
from app.services.llm.gemini_provider import GeminiProvider
from app.services.llm.groq_provider import GroqProvider
from app.services.llm.mock_provider import MockProvider
from app.services.llm.openai_provider import OpenAIProvider
from app.services.llm.registry import get_provider, list_providers
from app.services.llm.schemas import sanitize_for_gemini, sanitize_for_openai

CALCULATOR = ToolSpec(
    name="calculator",
    description="Evaluate arithmetic.",
    input_schema={
        "type": "object",
        "properties": {"expression": {"type": "string", "maxLength": 200}},
        "required": ["expression"],
        "additionalProperties": False,
    },
)
NO_ARG_TOOL = ToolSpec(
    name="current_time",
    description="Current UTC time.",
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
)

# Keyed provider classes that talk to a real vendor API.
REMOTE_PROVIDERS = {
    "openai": OpenAIProvider,
    "gemini": GeminiProvider,
    "groq": GroqProvider,
}


# ---------------------------------------------------------------------------
# Contract every provider must satisfy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["openai", "gemini", "groq", "mock"])
def test_registry_exposes_provider(name):
    provider = get_provider(name)
    assert isinstance(provider, BaseLLMProvider)
    # The registry key and the adapter's own name must agree, or the agent form
    # would offer a provider string the runtime cannot look up.
    assert provider.name == name


@pytest.mark.parametrize("name", ["openai", "gemini", "groq", "mock"])
def test_every_provider_declares_tool_support(name):
    assert get_provider(name).supports_tools is True


def test_catalogue_lists_every_provider_with_model_suggestions():
    catalogue = {p["name"]: p for p in list_providers()}
    assert set(catalogue) == {"openai", "gemini", "groq", "mock"}
    for name, entry in catalogue.items():
        assert entry["models"], f"{name} has no suggested models"
        assert entry["requires_api_key"] is (name != "mock")


@pytest.mark.parametrize("name", sorted(REMOTE_PROVIDERS))
def test_provider_without_a_key_is_unavailable(name):
    assert REMOTE_PROVIDERS[name](api_key=None).is_available() is False


@pytest.mark.parametrize("name", sorted(REMOTE_PROVIDERS))
@pytest.mark.asyncio
async def test_provider_without_a_key_raises_a_named_error(name):
    """The message must name the provider and point at the agent's config."""
    provider = REMOTE_PROVIDERS[name](api_key=None)
    with pytest.raises(
        ProviderNotConfigured,
        match=f"No API key is configured for provider '{name}'",
    ):
        await provider.generate(
            model="whatever", system="", messages=[LLMMessage("user", "hi")]
        )


@pytest.mark.parametrize("name", sorted(REMOTE_PROVIDERS))
def test_provider_with_a_key_is_available(name):
    assert REMOTE_PROVIDERS[name](api_key="test-key").is_available() is True


def test_only_mock_is_available_without_credentials():
    """Guards the offline path the tests and the Garak demo rely on."""
    assert MockProvider().is_available() is True


@pytest.mark.parametrize("name", sorted(REMOTE_PROVIDERS))
def test_providers_hold_no_credential_unless_given_one(name):
    """Keys come from the agent, never the environment - so a developer with
    old provider keys in .env cannot have the suite build live clients."""
    assert get_provider(name).is_available() is False


@pytest.mark.parametrize("name", sorted(REMOTE_PROVIDERS))
def test_registry_builds_a_separate_instance_per_credential(name):
    """A shared instance would let one agent's key serve another's calls."""
    first = get_provider(name, api_key="key-one")
    second = get_provider(name, api_key="key-two")
    assert first is not second
    assert first._api_key == "key-one"
    assert second._api_key == "key-two"


# ---------------------------------------------------------------------------
# Schema dialects
# ---------------------------------------------------------------------------


def test_gemini_schema_strips_keywords_the_api_rejects():
    cleaned = sanitize_for_gemini(CALCULATOR.input_schema)
    assert "additionalProperties" not in cleaned
    assert cleaned["properties"]["expression"]["type"] == "string"
    assert cleaned["required"] == ["expression"]


def test_gemini_schema_strips_pydantic_titles_from_mcp_tools():
    """MCP servers emit `title` on every property; Gemini 400s on it."""
    mcp_schema = {
        "type": "object",
        "title": "kb_searchArguments",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "properties": {"query": {"title": "Query", "type": "string"}},
        "required": ["query"],
    }
    cleaned = sanitize_for_gemini(mcp_schema)
    assert cleaned == {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    }


def test_gemini_schema_returns_none_for_a_tool_with_no_arguments():
    """Gemini wants `parameters` omitted, not set to an empty object."""
    assert sanitize_for_gemini(NO_ARG_TOOL.input_schema) is None
    assert sanitize_for_gemini(None) is None


def test_gemini_schema_recurses_into_nested_structures():
    cleaned = sanitize_for_gemini(
        {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "title": "Item",
                        "properties": {"id": {"type": "string", "title": "Id"}},
                    },
                },
                "choice": {"anyOf": [{"type": "string", "title": "A"}]},
            },
        }
    )
    item = cleaned["properties"]["items"]["items"]
    assert "title" not in item
    assert "title" not in item["properties"]["id"]
    assert "title" not in cleaned["properties"]["choice"]["anyOf"][0]


def test_gemini_schema_keeps_const_meaning_as_an_enum():
    cleaned = sanitize_for_gemini(
        {"type": "object", "properties": {"mode": {"type": "string", "const": "fast"}}}
    )
    assert cleaned["properties"]["mode"]["enum"] == ["fast"]


def test_gemini_schema_drops_unsupported_string_formats():
    cleaned = sanitize_for_gemini(
        {
            "type": "object",
            "properties": {
                "site": {"type": "string", "format": "uri"},
                "when": {"type": "string", "format": "date-time"},
            },
        }
    )
    assert "format" not in cleaned["properties"]["site"]
    assert cleaned["properties"]["when"]["format"] == "date-time"


def test_openai_schema_passes_json_schema_through():
    assert sanitize_for_openai(CALCULATOR.input_schema) == CALCULATOR.input_schema
    assert sanitize_for_openai(None) == {"type": "object", "properties": {}}


# ---------------------------------------------------------------------------
# OpenAI translation
# ---------------------------------------------------------------------------


@pytest.fixture
def openai_provider():
    return OpenAIProvider(api_key="test-key")


def test_openai_puts_the_system_prompt_in_a_system_message(openai_provider):
    messages = openai_provider._to_openai_messages(
        "You are terse.", [LLMMessage("user", "hi")]
    )
    assert messages[0] == {"role": "system", "content": "You are terse."}
    assert messages[1] == {"role": "user", "content": "hi"}


def test_openai_emits_tool_results_as_their_own_tool_messages(openai_provider):
    """Results are top-level messages, not blocks inside the preceding turn."""
    messages = openai_provider._to_openai_messages(
        "",
        [
            LLMMessage("user", "what is 2+2"),
            LLMMessage("assistant", "", raw={"role": "assistant", "tool_calls": []}),
            LLMMessage(
                "user",
                [
                    {
                        "type": "tool_result",
                        "tool_call_id": "call_1",
                        "name": "calculator",
                        "content": "4",
                        "is_error": False,
                    }
                ],
            ),
        ],
    )
    assert messages[-1] == {"role": "tool", "tool_call_id": "call_1", "content": "4"}


def test_openai_replays_its_own_assistant_turn_verbatim(openai_provider):
    raw = {"role": "assistant", "tool_calls": [{"id": "call_1"}]}
    messages = openai_provider._to_openai_messages("", [LLMMessage("assistant", "", raw=raw)])
    assert messages == [raw]


def test_openai_tool_definitions_use_the_function_envelope(openai_provider):
    kwargs = openai_provider._build_kwargs(
        "gpt-5.6-sol", "", [LLMMessage("user", "hi")], [CALCULATOR], 0.5, 100
    )
    assert kwargs["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "calculator",
                "description": "Evaluate arithmetic.",
                "parameters": CALCULATOR.input_schema,
            },
        }
    ]


def test_openai_uses_max_completion_tokens(openai_provider):
    kwargs = openai_provider._build_kwargs(
        "gpt-5.6-sol", "", [LLMMessage("user", "hi")], None, 0.5, 321
    )
    assert kwargs["max_completion_tokens"] == 321
    assert "max_tokens" not in kwargs


def test_openai_drops_temperature_for_reasoning_models(openai_provider):
    messages = [LLMMessage("user", "hi")]
    assert "temperature" not in openai_provider._build_kwargs(
        "o3-mini", "", messages, None, 0.5, 100
    )
    assert openai_provider._build_kwargs(
        "gpt-5.6-sol", "", messages, None, 0.5, 100
    )["temperature"] == 0.5


def test_openai_parses_tool_arguments_from_a_json_string(openai_provider):
    assert openai_provider._parse_arguments('{"expression": "2+2"}') == {
        "expression": "2+2"
    }


@pytest.mark.parametrize("bad", [None, "", "not json", "[1,2]", "null"])
def test_openai_tolerates_unparseable_tool_arguments(openai_provider, bad):
    """A malformed arguments string must not blow up the turn."""
    assert openai_provider._parse_arguments(bad) == {}


def _openai_message(content=None, tool_calls=None):
    """Minimal stand-in for a ChatCompletionMessage."""
    calls = [
        SimpleNamespace(
            id=tc["id"],
            function=SimpleNamespace(name=tc["name"], arguments=tc["arguments"]),
        )
        for tc in (tool_calls or [])
    ]
    return SimpleNamespace(
        content=content,
        tool_calls=calls,
        model_dump=lambda exclude_none=False: {
            "role": "assistant",
            "content": content,
            "tool_calls": tool_calls or [],
        },
    )


def test_openai_parse_extracts_text_and_usage(openai_provider):
    response = openai_provider._parse(
        _openai_message(content="4"),
        SimpleNamespace(prompt_tokens=11, completion_tokens=3),
        "stop",
    )
    assert response.text == "4"
    assert response.tool_calls == []
    assert response.usage == {"input_tokens": 11, "output_tokens": 3}
    assert response.stop_reason == "stop"


def test_openai_parse_extracts_tool_calls(openai_provider):
    response = openai_provider._parse(
        _openai_message(
            tool_calls=[
                {"id": "call_1", "name": "calculator", "arguments": '{"expression":"2+2"}'}
            ]
        ),
        None,
        "tool_calls",
    )
    assert len(response.tool_calls) == 1
    call = response.tool_calls[0]
    assert (call.id, call.name, call.arguments) == (
        "call_1",
        "calculator",
        {"expression": "2+2"},
    )


# ---------------------------------------------------------------------------
# Gemini translation
# ---------------------------------------------------------------------------


@pytest.fixture
def gemini_provider():
    return GeminiProvider(api_key="test-key")


def test_gemini_renames_the_assistant_role_to_model(gemini_provider):
    contents = gemini_provider._to_contents(
        [LLMMessage("user", "hi"), LLMMessage("assistant", "hello")]
    )
    assert [c.role for c in contents] == ["user", "model"]
    assert contents[0].parts[0].text == "hi"


def test_gemini_emits_tool_results_as_function_responses(gemini_provider):
    contents = gemini_provider._to_contents(
        [
            LLMMessage(
                "user",
                [
                    {
                        "type": "tool_result",
                        "tool_call_id": "gemini_calculator_0",
                        "name": "calculator",
                        "content": "4",
                        "is_error": False,
                    }
                ],
            )
        ]
    )
    part = contents[0].parts[0]
    assert contents[0].role == "tool"
    # Matched by name, not by id - the reason the runtime records the name.
    assert part.function_response.name == "calculator"
    assert part.function_response.response == {"result": "4"}


def test_gemini_marks_failed_tool_results_as_errors(gemini_provider):
    contents = gemini_provider._to_contents(
        [
            LLMMessage(
                "user",
                [
                    {
                        "type": "tool_result",
                        "tool_call_id": "x",
                        "name": "calculator",
                        "content": "Error: not permitted",
                        "is_error": True,
                    }
                ],
            )
        ]
    )
    assert contents[0].parts[0].function_response.response == {
        "error": "Error: not permitted"
    }


def test_gemini_replays_its_own_model_turn_verbatim(gemini_provider):
    sentinel = object()
    assert gemini_provider._to_contents([LLMMessage("assistant", "", raw=sentinel)]) == [
        sentinel
    ]


def test_gemini_sanitizes_tool_schemas_and_disables_sdk_tool_calling(gemini_provider):
    config = gemini_provider._build_config("be terse", [CALCULATOR], 0.4, 256)
    declaration = config.tools[0].function_declarations[0]
    assert declaration.name == "calculator"
    assert "additionalProperties" not in declaration.parameters_json_schema
    assert config.system_instruction == "be terse"
    assert config.temperature == 0.4
    assert config.max_output_tokens == 256
    # The runtime owns the tool loop, so the permission gate always applies.
    assert config.automatic_function_calling.disable is True


def test_gemini_omits_parameters_for_a_no_argument_tool(gemini_provider):
    config = gemini_provider._build_config("", [NO_ARG_TOOL], 1.0, 100)
    declaration = config.tools[0].function_declarations[0]
    assert declaration.parameters_json_schema is None
    assert declaration.parameters is None


def test_gemini_sends_no_tool_config_when_the_agent_has_none(gemini_provider):
    config = gemini_provider._build_config("", [], 1.0, 100)
    assert config.tools is None


def _gemini_response(parts, finish_reason="STOP"):
    content = SimpleNamespace(parts=parts, role="model")
    return SimpleNamespace(
        candidates=[SimpleNamespace(content=content, finish_reason=finish_reason)],
        usage_metadata=SimpleNamespace(prompt_token_count=7, candidates_token_count=2),
    )


def _part(text=None, function_call=None, thought=None):
    return SimpleNamespace(text=text, function_call=function_call, thought=thought)


def test_gemini_parse_extracts_text_and_usage(gemini_provider):
    response = gemini_provider._parse(_gemini_response([_part(text="4")]))
    assert response.text == "4"
    assert response.usage == {"input_tokens": 7, "output_tokens": 2}


def test_gemini_parse_synthesizes_an_id_when_the_call_has_none(gemini_provider):
    """Gemini may omit ids, but the runtime pairs results to calls by id."""
    call = SimpleNamespace(id=None, name="calculator", args={"expression": "2+2"})
    response = gemini_provider._parse(_gemini_response([_part(function_call=call)]))
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].id == "gemini_calculator_0"
    assert response.tool_calls[0].arguments == {"expression": "2+2"}


def test_gemini_parse_prefers_a_real_call_id(gemini_provider):
    call = SimpleNamespace(id="fc_9", name="calculator", args={})
    response = gemini_provider._parse(_gemini_response([_part(function_call=call)]))
    assert response.tool_calls[0].id == "fc_9"


def test_gemini_parse_skips_reasoning_parts(gemini_provider):
    """Thought parts are not user-visible output."""
    response = gemini_provider._parse(
        _gemini_response([_part(text="internal", thought=True), _part(text="answer")])
    )
    assert response.text == "answer"


def test_gemini_parse_handles_a_response_with_no_candidates(gemini_provider):
    empty = SimpleNamespace(candidates=[], usage_metadata=None)
    response = gemini_provider._parse(empty)
    assert response.text == ""
    assert response.tool_calls == []


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------


def _chunk(content=None, tool_calls=None, finish_reason=None, usage=None):
    """Minimal stand-in for a ChatCompletionChunk."""
    deltas = [
        SimpleNamespace(
            index=tc["index"],
            id=tc.get("id"),
            function=SimpleNamespace(
                name=tc.get("name"), arguments=tc.get("arguments")
            ),
        )
        for tc in (tool_calls or [])
    ]
    return SimpleNamespace(
        usage=usage,
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(content=content, tool_calls=deltas or None),
                finish_reason=finish_reason,
            )
        ],
    )


class _FakeOpenAIStream:
    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        async def gen():
            for chunk in self._chunks:
                yield chunk

        return gen()


def _stub_openai_client(provider, chunks):
    async def create(**kwargs):
        return _FakeOpenAIStream(chunks)

    provider._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )


@pytest.mark.asyncio
async def test_openai_streams_text_incrementally(openai_provider):
    _stub_openai_client(
        openai_provider,
        [
            _chunk(content="Hello"),
            _chunk(content=" there"),
            _chunk(finish_reason="stop", usage=SimpleNamespace(prompt_tokens=5, completion_tokens=2)),
        ],
    )
    events = [
        e
        async for e in openai_provider.stream(
            model="gpt-5.6-sol", system="", messages=[LLMMessage("user", "hi")]
        )
    ]
    assert [e.text for e in events if e.type == "text"] == ["Hello", " there"]
    final = events[-1]
    assert final.type == "done"
    assert final.response.text == "Hello there"
    assert final.response.usage == {"input_tokens": 5, "output_tokens": 2}


@pytest.mark.asyncio
async def test_openai_reassembles_tool_calls_split_across_chunks(openai_provider):
    """Id and name arrive once; arguments accumulate fragment by fragment."""
    _stub_openai_client(
        openai_provider,
        [
            _chunk(tool_calls=[{"index": 0, "id": "call_1", "name": "calculator", "arguments": ""}]),
            _chunk(tool_calls=[{"index": 0, "arguments": '{"expre'}]),
            _chunk(tool_calls=[{"index": 0, "arguments": 'ssion":"2+2"}'}]),
            _chunk(finish_reason="tool_calls"),
        ],
    )
    events = [
        e
        async for e in openai_provider.stream(
            model="gpt-5.6-sol", system="", messages=[LLMMessage("user", "2+2?")], tools=[CALCULATOR]
        )
    ]
    response = events[-1].response
    assert len(response.tool_calls) == 1
    call = response.tool_calls[0]
    assert (call.id, call.name, call.arguments) == (
        "call_1",
        "calculator",
        {"expression": "2+2"},
    )
    # The replayed assistant turn must carry the call back in wire format.
    assert response.raw_content["tool_calls"][0]["function"]["name"] == "calculator"


@pytest.mark.asyncio
async def test_openai_reassembles_parallel_tool_calls_by_index(openai_provider):
    _stub_openai_client(
        openai_provider,
        [
            _chunk(tool_calls=[{"index": 0, "id": "a", "name": "calculator", "arguments": '{"expression":"1+1"}'}]),
            _chunk(tool_calls=[{"index": 1, "id": "b", "name": "current_time", "arguments": "{}"}]),
            _chunk(finish_reason="tool_calls"),
        ],
    )
    events = [
        e
        async for e in openai_provider.stream(
            model="gpt-5.6-sol", system="", messages=[LLMMessage("user", "go")]
        )
    ]
    calls = events[-1].response.tool_calls
    assert [(c.id, c.name) for c in calls] == [("a", "calculator"), ("b", "current_time")]


class _FakeGeminiStream:
    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        async def gen():
            for chunk in self._chunks:
                yield chunk

        return gen()


@pytest.mark.asyncio
async def test_gemini_streams_text_and_keeps_the_assembled_text(gemini_provider):
    """The last chunk carries only its own fragment, so text must accumulate."""
    chunks = [
        _gemini_response([_part(text="Hel")]),
        _gemini_response([_part(text="lo")]),
    ]

    async def generate_content_stream(**kwargs):
        return _FakeGeminiStream(chunks)

    gemini_provider._client = SimpleNamespace(
        aio=SimpleNamespace(
            models=SimpleNamespace(generate_content_stream=generate_content_stream)
        )
    )

    events = [
        e
        async for e in gemini_provider.stream(
            model="gemini-3.8-flash", system="", messages=[LLMMessage("user", "hi")]
        )
    ]
    assert [e.text for e in events if e.type == "text"] == ["Hel", "lo"]
    assert events[-1].response.text == "Hello"


# ---------------------------------------------------------------------------
# Groq - an OpenAI-compatible endpoint, not a second adapter
# ---------------------------------------------------------------------------


def test_groq_points_at_its_own_endpoint():
    assert GroqProvider.base_url == "https://api.groq.com/openai/v1"
    # OpenAI itself must keep using the SDK default.
    assert OpenAIProvider.base_url is None


def test_groq_names_itself_when_unconfigured():
    """Inherited plumbing must not report the error as OpenAI's."""
    with pytest.raises(ProviderNotConfigured, match="provider 'groq'"):
        GroqProvider(api_key=None)._get_client()


def test_groq_builds_a_client_against_the_groq_base_url():
    client = GroqProvider(api_key="test-key")._get_client()
    assert "api.groq.com" in str(client.base_url)


def test_openai_client_keeps_the_default_endpoint():
    client = OpenAIProvider(api_key="test-key")._get_client()
    assert "api.groq.com" not in str(client.base_url)


def test_groq_inherits_the_shared_wire_translation():
    """The point of subclassing: one translation path, one set of tests."""
    groq = GroqProvider(api_key="test-key")
    kwargs = groq._build_kwargs(
        "openai/gpt-oss-120b", "be terse", [LLMMessage("user", "hi")], [CALCULATOR], 0.5, 100
    )
    assert kwargs["messages"][0] == {"role": "system", "content": "be terse"}
    assert kwargs["tools"][0]["function"]["name"] == "calculator"
    # Groq deprecates max_tokens in favour of max_completion_tokens, which the
    # shared adapter already sends.
    assert kwargs["max_completion_tokens"] == 100
    assert "max_tokens" not in kwargs
    assert kwargs["temperature"] == 0.5


def test_groq_never_sends_fields_groq_does_not_support():
    """logprobs, logit_bias, top_logprobs, n and messages[].name are unsupported."""
    groq = GroqProvider(api_key="test-key")
    kwargs = groq._build_kwargs(
        "openai/gpt-oss-120b",
        "sys",
        [
            LLMMessage("user", "hi"),
            LLMMessage(
                "user",
                [{"type": "tool_result", "tool_call_id": "c1", "name": "calculator", "content": "4"}],
            ),
        ],
        [CALCULATOR],
        0.5,
        100,
    )
    for unsupported in ("logprobs", "logit_bias", "top_logprobs", "n"):
        assert unsupported not in kwargs
    assert all("name" not in m for m in kwargs["messages"])


@pytest.mark.asyncio
async def test_gemini_streaming_keeps_tool_calls_from_earlier_chunks(gemini_provider):
    """Regression: a function call can arrive in any chunk, not just the last.

    Reading only the final chunk silently dropped it - streaming returned no
    tool calls while the non-streaming path returned them.
    """
    call = SimpleNamespace(id=None, name="calculator", args={"expression": "2+2"})
    chunks = [
        _gemini_response([_part(function_call=call)], finish_reason=None),
        _gemini_response([], finish_reason="STOP"),  # trailing usage-only chunk
    ]

    async def generate_content_stream(**kwargs):
        return _FakeGeminiStream(chunks)

    gemini_provider._client = SimpleNamespace(
        aio=SimpleNamespace(
            models=SimpleNamespace(generate_content_stream=generate_content_stream)
        )
    )

    final = [
        e
        async for e in gemini_provider.stream(
            model="gemini-3.5-flash", system="", messages=[LLMMessage("user", "2+2?")]
        )
    ][-1]
    assert [c.name for c in final.response.tool_calls] == ["calculator"]
    assert final.response.tool_calls[0].arguments == {"expression": "2+2"}


def test_gemini_always_disables_sdk_automatic_function_calling(gemini_provider):
    """The runtime owns the tool loop, so AFC must be off even with no tools.

    The SDK treats an unset value as enabled and warns on every request.
    """
    from google.genai import _extra_utils

    for tools in ([CALCULATOR], []):
        config = gemini_provider._build_config("", tools, 1.0, 100)
        assert config.automatic_function_calling.disable is True
        assert _extra_utils.should_disable_afc(config) is True
