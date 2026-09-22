"""AgentRuntime and the provider abstraction."""

import pytest

from app.models.agent import Agent
from app.services.agent_runtime import AgentRuntime
from app.services.llm.base import LLMMessage
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
