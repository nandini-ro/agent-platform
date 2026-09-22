"""Tool registry, the safe calculator, and the runtime's permission gate."""

import pytest

from app.models.agent import Agent
from app.services.agent_runtime import AgentRuntime
from app.services.tool_registry import CalculatorTool, ToolError, registry


def _agent(**overrides) -> Agent:
    defaults = dict(
        id="a1",
        name="A",
        description="",
        system_prompt="You are a calculator bot.",
        provider="mock",
        model="mock-1",
        temperature=1.0,
        max_tokens=256,
        tools=[],
        mcp_server_ids=[],
    )
    return Agent(**{**defaults, **overrides})


# -- calculator safety ------------------------------------------------------


@pytest.mark.asyncio
async def test_calculator_evaluates_arithmetic():
    result = await CalculatorTool().execute({"expression": "((3 + 4) * 12) / 2"})
    assert float(result) == 42.0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "expression",
    [
        '__import__("os").system("echo pwned")',
        "open('/etc/passwd').read()",
        "().__class__.__bases__[0].__subclasses__()",
        "exec('x=1')",
        "lambda: 1",
        "[1,2,3]",
        "'a' * 10",
    ],
)
async def test_calculator_refuses_anything_that_is_not_arithmetic(expression):
    """Code-shaped input is rejected at the AST node level, never executed."""
    with pytest.raises(ToolError):
        await CalculatorTool().execute({"expression": expression})


@pytest.mark.asyncio
async def test_calculator_rejects_division_by_zero():
    with pytest.raises(ToolError, match="Division by zero"):
        await CalculatorTool().execute({"expression": "1/0"})


@pytest.mark.asyncio
async def test_calculator_bounds_exponent_to_avoid_hanging():
    with pytest.raises(ToolError, match="Exponent too large"):
        await CalculatorTool().execute({"expression": "9**99999"})


def test_calculator_rejects_arguments_that_violate_schema():
    tool = CalculatorTool()
    with pytest.raises(ToolError, match="Invalid arguments"):
        tool.validate({})  # 'expression' is required
    with pytest.raises(ToolError, match="Invalid arguments"):
        tool.validate({"expression": "1+1", "extra": "nope"})


# -- registry ---------------------------------------------------------------


def test_registry_exposes_demo_tools():
    assert registry.names() == ["calculator", "current_time"]


def test_specs_for_returns_only_named_tools():
    specs = registry.specs_for(["calculator", "not_a_tool"])
    assert [s.name for s in specs] == ["calculator"]


# -- the permission gate ----------------------------------------------------


@pytest.mark.asyncio
async def test_agent_with_no_tools_is_offered_none():
    assert await AgentRuntime(_agent(tools=[]))._load_tools() == []


@pytest.mark.asyncio
async def test_agent_is_offered_only_its_allowlisted_tools():
    specs = await AgentRuntime(_agent(tools=["calculator"]))._load_tools()
    assert [s.name for s in specs] == ["calculator"]


@pytest.mark.asyncio
async def test_unpermitted_tool_is_refused_not_executed():
    """The gate keys off the agent row, so a model naming another tool fails."""
    runtime = AgentRuntime(_agent(tools=["current_time"]))
    invocation = await runtime._execute_tool("calculator", {"expression": "2+2"})
    assert invocation.ok is False
    assert "not permitted" in invocation.error
    assert "4" not in invocation.result


@pytest.mark.asyncio
async def test_tool_unknown_to_the_registry_is_refused():
    runtime = AgentRuntime(_agent(tools=["definitely_not_real"]))
    invocation = await runtime._execute_tool("definitely_not_real", {})
    assert invocation.ok is False
    assert "not available" in invocation.error


@pytest.mark.asyncio
async def test_permitted_tool_executes_and_is_recorded():
    runtime = AgentRuntime(_agent(tools=["calculator"]))
    invocation = await runtime._execute_tool("calculator", {"expression": "6*7"})
    assert invocation.ok is True
    assert invocation.result == "42"
    assert invocation.source == "local"


@pytest.mark.asyncio
async def test_bad_arguments_are_rejected_before_execution():
    runtime = AgentRuntime(_agent(tools=["calculator"]))
    invocation = await runtime._execute_tool("calculator", {"wrong": "field"})
    assert invocation.ok is False
    assert "Invalid arguments" in invocation.error


@pytest.mark.asyncio
async def test_slow_tool_hits_the_timeout():
    import asyncio

    from app.services.tool_registry import BaseTool, ToolRegistry

    class SlowTool(BaseTool):
        name = "slow"
        description = "Sleeps forever."
        input_schema = {"type": "object", "properties": {}}

        async def execute(self, arguments):
            await asyncio.sleep(30)
            return "never"

    runtime = AgentRuntime(_agent(tools=["slow"]), tool_registry=ToolRegistry([SlowTool()]))
    runtime.settings.tool_timeout_seconds = 0.05
    invocation = await runtime._execute_tool("slow", {})
    assert invocation.ok is False
    assert "timed out" in invocation.error


# -- full loop --------------------------------------------------------------


@pytest.mark.asyncio
async def test_end_to_end_user_agent_tool_agent_response():
    """User -> Agent -> Tool -> Agent -> Response, with the tool result used."""
    runtime = AgentRuntime(_agent(tools=["calculator"]))
    result = await runtime.run(history=[], user_message="What is 12 * 12?")
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "calculator"
    assert result.tool_calls[0].ok is True
    assert "144" in result.tool_calls[0].result
    assert "144" in result.text
    assert result.iterations == 2


@pytest.mark.asyncio
async def test_agent_without_the_tool_never_calls_it():
    runtime = AgentRuntime(_agent(tools=[]))
    result = await runtime.run(history=[], user_message="What is 12 * 12?")
    assert result.tool_calls == []
