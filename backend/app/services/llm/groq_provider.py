"""Groq adapter.

Groq serves an OpenAI-compatible Chat Completions endpoint, so this is a base
URL and a credential rather than a second adapter. Everything that matters here
- message translation, tool definitions, streaming tool-call reassembly - is
inherited from `OpenAIProvider` and exercised by the same tests.

Groq's documented gaps (`logprobs`, `logit_bias`, `top_logprobs`,
`messages[].name`, `n` > 1) are all fields this codebase never sends, and it
accepts the `max_completion_tokens`, `tools`, `tool_choice` and
`stream_options` parameters the shared adapter relies on.
https://console.groq.com/docs/openai

The official `groq` SDK exists and would also work. Using the OpenAI SDK avoids
a second dependency that is largely a clone of it; if Groq ever diverges, this
file is the only place that has to change.
"""

from __future__ import annotations

from app.services.llm.openai_provider import OpenAIProvider


class GroqProvider(OpenAIProvider):
    name = "groq"
    env_var = "GROQ_API_KEY"
    base_url = "https://api.groq.com/openai/v1"

    # Note: Groq silently rewrites a temperature of exactly 0 to 1e-8. Agents
    # configured at 0 therefore behave as near-zero rather than erroring.
