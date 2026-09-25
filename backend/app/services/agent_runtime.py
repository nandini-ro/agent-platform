"""AgentRuntime - one generic execution loop for every agent.

There is deliberately no per-agent branching anywhere: an agent is a row of
configuration that this loop reads. The UI (streaming), the programmatic
endpoint, and Garak all enter through here, so their behaviour cannot drift.

Every tool call the model requests passes through the same gate before it can
run: permitted-for-this-agent, then schema-validated, then executed under a
timeout. That gate lives here rather than in the tools because it must hold for
local tools and MCP tools alike - and because it is exactly what an adversarial
prompt tries to talk its way around.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from app.config.settings import get_settings
from app.db.session import SessionLocal
from app.models.agent import Agent
from app.models.http_tool import HTTPTool
from app.models.mcp_server import MCPServer
from app.services.credentials import resolve_api_key
from app.services.mcp_manager import DiscoveredTool, MCPError, MCPManager
from app.services.mcp_manager import manager as default_mcp_manager
from app.services.llm.base import (
    LLMMessage,
    LLMResponse,
    StreamEvent,
    ToolSpec,
)
from app.services.llm.registry import get_provider
from app.services.tool_registry import ToolError, ToolRegistry
from app.services.tool_registry import registry as default_tool_registry

logger = logging.getLogger(__name__)

# Sent on the final, tool-less call when the iteration budget runs out. The
# model is told why its tools vanished, so it reports what it could not do
# instead of answering from memory as though the calls had succeeded.
_BUDGET_NOTICE = (
    "The tool-call budget for this turn is exhausted; no further tools can "
    "run. Answer now from the tool results above. Do not invent results for "
    "tools that never ran - say plainly which ones were not completed."
)


@dataclass
class ToolInvocation:
    """Audit record of one tool attempt - surfaced in the UI and stored."""

    name: str
    arguments: dict[str, Any]
    result: str = ""
    source: str = "local"  # local | http | mcp
    ok: bool = True
    error: str | None = None
    duration_ms: int = 0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "arguments": self.arguments,
            "result": self.result,
            "source": self.source,
            "ok": self.ok,
            "error": self.error,
            "duration_ms": self.duration_ms,
        }


@dataclass
class RuntimeResult:
    text: str
    tool_calls: list[ToolInvocation] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    provider: str = ""
    model: str = ""
    iterations: int = 1
    # True when the loop hit max_tool_iterations with the model still asking
    # for tools: the answer was forced early rather than volunteered.
    budget_exhausted: bool = False

    def metadata(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "usage": self.usage,
            "iterations": self.iterations,
            "budget_exhausted": self.budget_exhausted,
        }


class AgentRuntime:
    """Executes a configured agent against a conversation history."""

    def __init__(
        self,
        agent: Agent,
        tool_registry: ToolRegistry | None = None,
        mcp_manager: MCPManager | None = None,
    ) -> None:
        self.agent = agent
        self.settings = get_settings()
        # The agent's own credential, decrypted server-side and handed only to
        # the provider client. It never enters prompts, messages or tool calls.
        # Raises ProviderNotConfigured when it cannot be decrypted.
        self._api_key = resolve_api_key(agent)
        self.provider = get_provider(agent.provider, api_key=self._api_key)
        self.tools = tool_registry or default_tool_registry
        self.mcp = mcp_manager or default_mcp_manager
        # qualified tool name -> (server, discovered tool), filled by _load_tools
        self._mcp_index: dict[str, tuple[MCPServer, DiscoveredTool]] = {}
        # name -> user-defined HTTP tool, also filled by _load_tools
        self._http_index: dict[str, Any] = {}

    def redact(self, text: str) -> str:
        """Scrub this agent's API key from text bound for a client, in case a
        provider SDK echoes it in an exception."""
        return text.replace(self._api_key, "[redacted]") if self._api_key else text

    # -- configuration loading -------------------------------------------

    def _system_prompt(self) -> str:
        return self.agent.system_prompt or ""

    def _permitted_tool_names(self) -> set[str]:
        """The agent's allowlist. Empty config means no tools, never all tools."""
        return set(self.agent.tools or [])

    def _permitted_mcp_servers(self) -> list[MCPServer]:
        """The MCP servers this agent is allowed to reach, loaded fresh."""
        ids = list(self.agent.mcp_server_ids or [])
        if not ids:
            return []
        session = SessionLocal()
        try:
            servers = [session.get(MCPServer, sid) for sid in ids]
            return [s for s in servers if s is not None and s.enabled]
        finally:
            session.close()

    def _permitted_http_tools(self) -> list[Any]:
        """User-defined HTTP tools this agent is allowed to use.

        They share the agent's single `tools` allowlist with code-defined
        tools, so there is one permission surface rather than two.
        """
        from app.services.http_tool import UserHTTPTool

        names = self._permitted_tool_names()
        if not names:
            return []
        session = SessionLocal()
        try:
            rows = (
                session.query(HTTPTool)
                .filter(HTTPTool.name.in_(sorted(names)), HTTPTool.enabled.is_(True))
                .all()
            )
            return [UserHTTPTool(row) for row in rows]
        finally:
            session.close()

    async def _load_tools(self) -> list[ToolSpec]:
        """Local, user-defined and MCP tools, all restricted to this agent."""
        if not self.provider.supports_tools:
            return []

        specs = self.tools.specs_for(sorted(self._permitted_tool_names()))

        self._http_index = {}
        for tool in self._permitted_http_tools():
            # A code-defined tool always wins a name clash; the API refuses to
            # create one that collides, but a built-in added later could.
            if self.tools.get(tool.name) is not None:
                logger.warning("HTTP tool %r shadowed by a built-in", tool.name)
                continue
            self._http_index[tool.name] = tool
            specs.append(tool.spec())

        self._mcp_index = {}
        for server in self._permitted_mcp_servers():
            try:
                discovered = await self.mcp.discover(server)
            except MCPError as exc:
                # An unreachable MCP server degrades the agent, it does not
                # break the turn - the other tools stay usable.
                logger.warning("MCP discovery failed for %s: %s", server.name, exc)
                continue
            for tool in discovered:
                self._mcp_index[tool.qualified_name] = (server, tool)
                specs.append(tool.spec())
        return specs

    async def _execute_tool(self, name: str, arguments: dict) -> ToolInvocation:
        """Gate, validate, then run a single tool call.

        Returns a failed ToolInvocation rather than raising: the model is told
        the call was refused and can recover, while the refusal is recorded.
        """
        started = time.perf_counter()

        def fail(error: str) -> ToolInvocation:
            return ToolInvocation(
                name=name,
                arguments=arguments,
                ok=False,
                error=error,
                result=f"Error: {error}",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )

        # MCP tools take the same three steps, against the server allowlist.
        if name in self._mcp_index:
            return await self._execute_mcp_tool(name, arguments, started)

        # 1. Permission. Checked against the agent row, not against what the
        #    model claims, and before the tool is even looked up.
        if name not in self._permitted_tool_names():
            logger.warning(
                "agent %s attempted unpermitted tool %r", self.agent.id, name
            )
            return fail(f"Tool '{name}' is not permitted for this agent.")

        tool = self.tools.get(name) or self._http_index.get(name)
        if tool is None:
            return fail(f"Tool '{name}' is not available.")

        # 2. Argument validation against the tool's own schema.
        try:
            tool.validate(arguments)
        except ToolError as exc:
            return fail(str(exc))

        # 3. Execution under a timeout.
        try:
            result = await asyncio.wait_for(
                tool.execute(arguments), timeout=self.settings.tool_timeout_seconds
            )
        except asyncio.TimeoutError:
            return fail(
                f"Tool '{name}' timed out after "
                f"{self.settings.tool_timeout_seconds}s."
            )
        except ToolError as exc:
            return fail(str(exc))
        except Exception as exc:  # noqa: BLE001 - a broken tool must not kill the turn
            logger.exception("tool %s raised", name)
            return fail(f"Tool '{name}' failed: {exc}")

        return ToolInvocation(
            name=name,
            arguments=arguments,
            result=str(result),
            source="http" if name in self._http_index else "local",
            ok=True,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    async def _execute_mcp_tool(
        self, name: str, arguments: dict, started: float
    ) -> ToolInvocation:
        """Run an MCP tool. Same gate: permitted, validated, timed out.

        `_mcp_index` only ever contains tools discovered from servers on this
        agent's allowlist, so membership in it *is* the permission check.
        """

        def fail(error: str) -> ToolInvocation:
            return ToolInvocation(
                name=name,
                arguments=arguments,
                source="mcp",
                ok=False,
                error=error,
                result=f"Error: {error}",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )

        server, tool = self._mcp_index[name]
        remote_name = tool.tool_name

        if not isinstance(arguments, dict):
            return fail("MCP tool arguments must be an object.")

        # Validate against the schema the server advertised, before anything
        # crosses the process boundary. The server will validate too; doing it
        # here means malformed model output never reaches the subprocess.
        try:
            Draft202012Validator(tool.input_schema).validate(arguments)
        except SchemaError:
            logger.warning("MCP tool %s advertised an invalid schema", name)
        except ValidationError as exc:
            return fail(f"Invalid arguments: {exc.message}")

        try:
            result = await asyncio.wait_for(
                self.mcp.call_tool(server, remote_name, arguments),
                timeout=self.settings.mcp_timeout_seconds,
            )
        except asyncio.TimeoutError:
            return fail(
                f"MCP tool '{remote_name}' timed out after "
                f"{self.settings.mcp_timeout_seconds}s."
            )
        except MCPError as exc:
            return fail(str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.exception("MCP tool %s raised", name)
            return fail(f"MCP tool '{remote_name}' failed: {exc}")

        return ToolInvocation(
            name=name,
            arguments=arguments,
            result=str(result),
            source="mcp",
            ok=True,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    def _trim(self, history: list[LLMMessage]) -> list[LLMMessage]:
        """Bound the context window we resend. Keeps the most recent turns."""
        limit = self.settings.max_history_messages
        return history[-limit:] if len(history) > limit else history

    def _wrap_up_messages(self, messages: list[LLMMessage]) -> list[LLMMessage]:
        """History plus the notice that ends a budget-exhausted turn."""
        return messages + [LLMMessage(role="user", content=_BUDGET_NOTICE)]

    # -- execution --------------------------------------------------------

    async def run(
        self, history: list[LLMMessage], user_message: str
    ) -> RuntimeResult:
        """Non-streaming execution. Used by the programmatic/Garak endpoint."""
        messages = self._trim(list(history)) + [
            LLMMessage(role="user", content=user_message)
        ]
        tools = await self._load_tools()
        invocations: list[ToolInvocation] = []
        usage: dict[str, Any] = {}
        iterations = 0
        response: LLMResponse | None = None

        while iterations < self.settings.max_tool_iterations:
            iterations += 1
            response = await self.provider.generate(
                model=self.agent.model,
                system=self._system_prompt(),
                messages=messages,
                tools=tools or None,
                temperature=self.agent.temperature,
                max_tokens=self.agent.max_tokens,
            )
            usage = _merge_usage(usage, response.usage)

            if not response.tool_calls:
                break

            messages, step = await self._apply_tool_calls(messages, response)
            invocations.extend(step)

        text = response.text if response else ""

        # Leaving the loop with tool calls still pending means the budget ran
        # out mid-plan: those results are in `messages` but nothing has been
        # said about them. Spend one more call, with no tools offered, so the
        # turn ends with an answer rather than the empty text of a tool-call
        # response.
        budget_exhausted = bool(response and response.tool_calls)
        if budget_exhausted:
            logger.warning(
                "agent %s hit max_tool_iterations (%d); forcing a final answer",
                self.agent.id,
                self.settings.max_tool_iterations,
            )
            final = await self.provider.generate(
                model=self.agent.model,
                system=self._system_prompt(),
                messages=self._wrap_up_messages(messages),
                tools=None,
                temperature=self.agent.temperature,
                max_tokens=self.agent.max_tokens,
            )
            usage = _merge_usage(usage, final.usage)
            text = final.text

        return RuntimeResult(
            text=text,
            tool_calls=invocations,
            usage=usage,
            provider=self.agent.provider,
            model=self.agent.model,
            iterations=iterations,
            budget_exhausted=budget_exhausted,
        )

    async def stream(
        self, history: list[LLMMessage], user_message: str
    ) -> AsyncIterator[StreamEvent | ToolInvocation | RuntimeResult]:
        """Streaming execution for the chat UI.

        Yields text StreamEvents as they arrive, ToolInvocation records as tools
        run, and finally a single RuntimeResult.
        """
        messages = self._trim(list(history)) + [
            LLMMessage(role="user", content=user_message)
        ]
        tools = await self._load_tools()
        invocations: list[ToolInvocation] = []
        usage: dict[str, Any] = {}
        iterations = 0
        final_text = ""
        response: LLMResponse | None = None

        while iterations < self.settings.max_tool_iterations:
            iterations += 1
            response = None
            async for event in self.provider.stream(
                model=self.agent.model,
                system=self._system_prompt(),
                messages=messages,
                tools=tools or None,
                temperature=self.agent.temperature,
                max_tokens=self.agent.max_tokens,
            ):
                if event.type == "text":
                    yield event
                elif event.type == "done":
                    response = event.response

            if response is None:
                break
            usage = _merge_usage(usage, response.usage)
            final_text = response.text

            if not response.tool_calls:
                break

            messages, step = await self._apply_tool_calls(messages, response)
            for inv in step:
                yield inv
            invocations.extend(step)

        # Same wrap-up as `run`, streamed: without it a turn that exhausts its
        # budget ends on a tool badge and no words at all.
        budget_exhausted = bool(response and response.tool_calls)
        if budget_exhausted:
            logger.warning(
                "agent %s hit max_tool_iterations (%d); forcing a final answer",
                self.agent.id,
                self.settings.max_tool_iterations,
            )
            final_text = ""
            async for event in self.provider.stream(
                model=self.agent.model,
                system=self._system_prompt(),
                messages=self._wrap_up_messages(messages),
                tools=None,
                temperature=self.agent.temperature,
                max_tokens=self.agent.max_tokens,
            ):
                if event.type == "text":
                    final_text += event.text
                    yield event
                elif event.type == "done" and event.response is not None:
                    usage = _merge_usage(usage, event.response.usage)
                    final_text = event.response.text or final_text

        yield RuntimeResult(
            text=final_text,
            tool_calls=invocations,
            usage=usage,
            provider=self.agent.provider,
            model=self.agent.model,
            iterations=iterations,
            budget_exhausted=budget_exhausted,
        )

    async def _apply_tool_calls(
        self, messages: list[LLMMessage], response: LLMResponse
    ) -> tuple[list[LLMMessage], list[ToolInvocation]]:
        """Run the model's requested tools and append the round trip."""
        messages = messages + [
            LLMMessage(
                role="assistant",
                content=response.text or "",
                raw=response.raw_content,
            )
        ]
        blocks: list[dict] = []
        invocations: list[ToolInvocation] = []
        for call in response.tool_calls:
            invocation = await self._execute_tool(call.name, call.arguments)
            invocations.append(invocation)
            blocks.append(
                {
                    "type": "tool_result",
                    "tool_call_id": call.id,
                    # Providers match a result to its call differently:
                    # OpenAI by id, Gemini by function name. Carry both.
                    "name": call.name,
                    "content": invocation.result,
                    "is_error": not invocation.ok,
                }
            )
        messages = messages + [LLMMessage(role="user", content=blocks)]
        return messages, invocations


def _merge_usage(total: dict, new: dict) -> dict:
    merged = dict(total)
    for key, value in (new or {}).items():
        if isinstance(value, int):
            merged[key] = merged.get(key, 0) + value
    return merged
