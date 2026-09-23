"""AgentRuntime and the provider abstraction."""

import pytest

from app.models.agent import Agent
from app.services.agent_runtime import AgentRuntime
from app.services.llm.base import (
    BaseLLMProvider,
    LLMMessage,
    LLMResponse,
    StreamEvent,
    ToolCall,
)
from app.services.llm.registry import get_provider


def _agent(**overrides) -> Agent:
    defaults = dict(
        id="a1",
        name="A",
        description="",
        system_prompt="You are a helpful bot.",
        provider="mock",
        model="mock-1",
        temperature=1.0,
        max_tokens=256,
        tools=[],
        mcp_server_ids=[],
    )
    return Agent(**{**defaults, **overrides})


@pytest.mark.asyncio
async def test_runtime_returns_text_and_metadata():
    result = await AgentRuntime(_agent()).run(history=[], user_message="ping")
    assert "ping" in result.text
    assert result.provider == "mock"
    assert result.model == "mock-1"
    assert result.iterations == 1


@pytest.mark.asyncio
async def test_runtime_passes_system_prompt_through():
    agent = _agent(system_prompt="You only speak in haiku.")
    result = await AgentRuntime(agent).run(history=[], user_message="hi")
    assert "haiku" in result.text


@pytest.mark.asyncio
async def test_runtime_trims_history_to_the_configured_limit():
    runtime = AgentRuntime(_agent())
    runtime.settings.max_history_messages = 4
    history = [LLMMessage(role="user", content=f"m{i}") for i in range(10)]
    assert len(runtime._trim(history)) == 4
    assert runtime._trim(history)[0].content == "m6"


@pytest.mark.asyncio
async def test_runtime_streaming_ends_with_a_result():
    from app.services.agent_runtime import RuntimeResult

    events = [e async for e in AgentRuntime(_agent()).stream([], "hello")]
    assert isinstance(events[-1], RuntimeResult)
    assert events[-1].text
    streamed = "".join(e.text for e in events[:-1] if hasattr(e, "text"))
    assert streamed.strip() == events[-1].text.strip()


def test_unknown_provider_lookup_raises():
    with pytest.raises(KeyError):
        get_provider("nonexistent")


class _NeverStopsProvider(BaseLLMProvider):
    """Asks for a tool on every turn, so the loop always hits its budget.

    Answers in text only when offered no tools - which is exactly what the
    runtime does on its wrap-up call.
    """

    name = "never-stops"
    supports_tools = True
    final_text = "Ran out of budget; here is what I have."

    def __init__(self) -> None:
        self.tool_less_calls = 0

    def is_available(self) -> bool:
        return True

    def _respond(self, tools) -> LLMResponse:
        if not tools:
            self.tool_less_calls += 1
            return LLMResponse(text=self.final_text, stop_reason="end_turn")
        return LLMResponse(
            text="",
            tool_calls=[ToolCall(id="c1", name="current_time", arguments={})],
            stop_reason="tool_use",
        )

    async def generate(self, *, model, system, messages, tools=None, **kwargs):
        return self._respond(tools)

    async def stream(self, *, model, system, messages, tools=None, **kwargs):
        response = self._respond(tools)
        if response.text:
            yield StreamEvent(type="text", text=response.text)
        yield StreamEvent(type="done", response=response)


def _budgeted_runtime(monkeypatch, limit: int) -> AgentRuntime:
    runtime = AgentRuntime(_agent(tools=["current_time"]))
    runtime.provider = _NeverStopsProvider()
    # Settings are a cached singleton; monkeypatch puts the limit back after.
    monkeypatch.setattr(runtime.settings, "max_tool_iterations", limit)
    return runtime


@pytest.mark.asyncio
async def test_run_answers_instead_of_going_silent_when_the_budget_runs_out(
    monkeypatch,
):
    runtime = _budgeted_runtime(monkeypatch, 3)

    result = await runtime.run(history=[], user_message="what time is it?")

    # Without the wrap-up call this is "" - the empty text of a tool-call turn.
    assert result.text == _NeverStopsProvider.final_text
    assert result.budget_exhausted is True
    assert result.iterations == 3
    assert len(result.tool_calls) == 3
    assert runtime.provider.tool_less_calls == 1


@pytest.mark.asyncio
async def test_stream_answers_when_the_budget_runs_out(monkeypatch):
    from app.services.agent_runtime import RuntimeResult

    runtime = _budgeted_runtime(monkeypatch, 2)

    events = [e async for e in runtime.stream([], "what time is it?")]
    result = events[-1]

    assert isinstance(result, RuntimeResult)
    assert result.budget_exhausted is True
    assert result.text == _NeverStopsProvider.final_text
    streamed = "".join(
        e.text for e in events if isinstance(e, StreamEvent) and e.type == "text"
    )
    assert streamed == result.text


@pytest.mark.asyncio
async def test_a_turn_that_finishes_on_its_own_is_not_marked_exhausted():
    result = await AgentRuntime(_agent()).run(history=[], user_message="ping")
    assert result.budget_exhausted is False
    assert result.metadata()["budget_exhausted"] is False
