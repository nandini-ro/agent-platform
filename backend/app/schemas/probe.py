"""Request and response of the gateway probe (app/api/probe.py)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# What an OpenAI-compatible endpoint accepts as a function name.
_TOOL_NAME = r"^[A-Za-z0-9_-]{1,64}$"


class ProbeToolCall(BaseModel):
    """A tool round trip to replay, as the runtime's follow-up request would
    carry it: the model's call, then the tool's result."""

    name: str = Field(pattern=_TOOL_NAME)
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: str = Field(default="[probe] tool result", max_length=4000)


class ProbeRequest(BaseModel):
    prompt: str = Field(default="ping", min_length=1, max_length=4000)
    # Function names to declare. Agios matches tools by name, so a name the
    # platform has no tool for (terminal_exec, say) is declared all the same.
    tools: list[str] = Field(default_factory=list, max_length=20)
    # Replay a call to this tool and its result: the request Agios checks.
    tool_call: ProbeToolCall | None = None
    stream: bool = False
    # LiteLLM's per-request cache switch, so a cached reply cannot stand in
    # for what the gateway decides now.
    bypass_cache: bool = True
    timeout_s: float = Field(default=30.0, gt=0, le=120)

    def tool_names(self) -> list[str]:
        names = list(self.tools)
        if self.tool_call and self.tool_call.name not in names:
            names.append(self.tool_call.name)
        return names


class ProbeResponse(BaseModel):
    url: str
    sent: dict[str, Any]  # the request body; the key travels only in a header
    status: int | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    body: str = ""
    json_body: Any = None
    truncated: bool = False
    headers_ms: int | None = None
    first_chunk_ms: int | None = None
    total_ms: int | None = None
    error: str | None = None
