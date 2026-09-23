# Agent Platform

A configurable multi-agent chatbot platform with local tools, MCP tool support,
a programmatic agent API, and Garak security testing.

Create agents in a ChatGPT-style UI, give each one its own instructions, model,
tools and MCP servers, chat with them, call them over HTTP — then attack them
with Garak through that same HTTP endpoint.

```
Create Agent → Configure → Save → Select → Chat → LLM / Tools / MCP
                                     ↓
                            Agent API endpoint
                                     ↓
                            Garak security scan
```

## Contents

- [Why it is built this way](#why-it-is-built-this-way)
- [Tech stack](#tech-stack)
- [Prerequisites](#prerequisites)
- [Quick start](#quick-start)
- [Environment variables](#environment-variables)
- [Database setup](#database-setup)
- [Using the app](#using-the-app)
- [Model providers](#model-providers)
- [How tools work](#how-tools-work)
- [How MCP works](#how-mcp-works)
- [The agent API](#the-agent-api)
- [Garak security testing](#garak-security-testing)
- [Tests](#tests)
- [Project layout](#project-layout)
- [Known limitations](#known-limitations)
- [Future improvements](#future-improvements)

## Why it is built this way

**Agents are data, not code.** An agent is a database row — instructions,
provider, model, parameters, a tool allowlist and an MCP server allowlist. One
generic `AgentRuntime` reads that row and executes it. There is no per-agent
code anywhere, so adding an agent is an `INSERT`.

**One runtime, three callers.** The streaming chat UI, the stateless agent
endpoint, and Garak all execute the same `AgentRuntime`. This matters most for
security testing: if Garak exercised a different path from the UI, a clean scan
would tell you nothing about what real users can do.

**The permission gate is the point.** Every tool call — local or MCP — is
checked against the agent's own allowlist, then schema-validated, then run under
a timeout. Adversarial prompts are exactly what tries to talk a model around
that, so the check lives in the runtime, keyed off the database row, never off
what the model claims.

Full detail, diagrams and trade-offs: [`docs/architecture.md`](docs/architecture.md).

## Tech stack

| Layer | Choice |
|---|---|
| Frontend | Next.js 16, React 19, TypeScript, Tailwind CSS 4 |
| Backend | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.0 |
| Database | SQLite for development, PostgreSQL-compatible |
| Transport | REST, plus SSE for streaming replies |
| LLM | Provider abstraction; OpenAI, Gemini and Groq adapters + deterministic mock |
| MCP | Official MCP Python SDK v2 (`mcp==2.2.0`) |
| Security testing | Garak 0.17, run as a subprocess in its own virtualenv |

## Prerequisites

- **Python 3.11–3.13** (3.12 recommended). Garak requires ≥3.11.
- **Node.js 20+**
- An **API key for at least one provider** — optional. Without any key the
  platform runs on the built-in `mock` provider, and every feature including
  Garak works offline.

## Quick start

### 1. Backend

```bash
cd backend

python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp .env.example .env          # edit if you have an API key

.venv/bin/python seed.py      # creates three demo agents + the demo MCP server
.venv/bin/uvicorn app.main:app --reload
```

Backend on <http://127.0.0.1:8000>, interactive API docs at
<http://127.0.0.1:8000/docs>.

### 2. Frontend

```bash
cd frontend

npm install
cp .env.example .env.local
npm run dev
```

Open <http://localhost:3000>.

> Use `localhost`, not `127.0.0.1`. Next.js 16 blocks dev-server resources
> requested from a different origin spelling; the page would load but never
> hydrate. `next.config.ts` allows both, but `localhost` is the tested path.

### 3. Garak (only needed for security testing)

Garak installs into a **separate** virtualenv. Its dependency tree is large and
tightly pinned, and the backend never imports it — it is always a subprocess.

```bash
cd backend
python3.12 -m venv .venv-garak
.venv-garak/bin/pip install -r requirements-garak.txt
.venv-garak/bin/python -m garak --version
```

## Environment variables

All configuration comes from the environment. Nothing sensitive is hardcoded or
stored in the database. See [`backend/.env.example`](backend/.env.example).

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./chatbot.db` | Use `postgresql+psycopg://...` for PostgreSQL |
| `OPENAI_API_KEY` | *(unset)* | Enables the `openai` provider. Unset is handled gracefully |
| `GEMINI_API_KEY` | *(unset)* | Enables the `gemini` provider |
| `GROQ_API_KEY` | *(unset)* | Enables the `groq` provider |
| `HTTP_TOOL_TIMEOUT_SECONDS` | `10` | Ceiling for a custom HTTP tool call |
| `HTTP_TOOL_MAX_RESPONSE_CHARS` | `8000` | Response truncation |
| `HTTP_TOOL_ALLOWED_HOSTS` | *(empty)* | Comma-separated allowlist; empty means any public host. Applies to custom tools **and remote MCP URLs** |
| `HTTP_TOOL_ALLOW_PRIVATE_NETWORKS` | `false` | Let those reach private/loopback addresses. Dangerous; needed only to use the bundled HTTP demo MCP server |
| `DEFAULT_PROVIDER` | `mock` | Provider preselected in the agent form |
| `AGENT_API_KEY` | *(unset)* | Shared secret for every state-changing `/api` request (`POST`/`PUT`/`PATCH`/`DELETE`), sent as `X-API-Key`. Unset leaves writes open, which is the local default. See [deployment](docs/deployment.md) |
| `TOOL_TIMEOUT_SECONDS` | `10` | Per local tool call |
| `MCP_TIMEOUT_SECONDS` | `30` | Per MCP connection and call |
| `MAX_TOOL_ITERATIONS` | `12` | Caps the agentic loop. A prompt needing N tools needs N+1: one turn per tool, one to answer |
| `MAX_HISTORY_MESSAGES` | `40` | Turns replayed per request |
| `GARAK_PYTHON` | `.venv-garak/bin/python` | Interpreter used to run Garak |
| `GARAK_REPORT_DIR` | `garak_runs` | Where scan reports are written |
| `SELF_BASE_URL` | `http://127.0.0.1:8000` | URL Garak calls back on |
| `CORS_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | Comma-separated |

Frontend: `NEXT_PUBLIC_API_BASE_URL` (default `http://127.0.0.1:8000`).

## Database setup

Tables are created automatically on startup from the SQLAlchemy models — no
migration step for the POC.

```bash
.venv/bin/python seed.py      # idempotent; safe to re-run
rm chatbot.db                 # start over
```

The schema is PostgreSQL-compatible: string UUID keys, generic `JSON` columns,
timezone-aware timestamps. Switching is a `DATABASE_URL` change plus `psycopg`.
A real deployment should add Alembic instead of `create_all()`.

## Using the app

### Create an agent

1. Click **+ Create Agent** in the sidebar.
2. Fill in:
   - **Agent name** — required.
   - **Description** — free text.
   - **System instructions** — what actually drives behaviour.
   - **Model provider** — `mock` (offline), `openai` or `gemini`. A provider
     with no API key is labelled as such and the form warns you.
   - **Model** — suggestions per provider; any string is accepted.
   - **Temperature / Max tokens**.
   - **Tools** — tick the local tools this agent may execute.
   - **MCP servers** — tick the servers it may reach.
3. **Save agent**. It appears in the sidebar immediately.

Hover an agent in the sidebar to edit (✎) or delete (✕) it.

### Chat

Select an agent, type, press Enter. Replies stream token by token. Tool and MCP
calls appear as expandable badges above the reply showing the arguments, the
result, and how long the call took — green for executed, red for refused.

**New chat** starts a fresh conversation with the selected agent. Conversations
are scoped to their agent, so history never crosses between agents.

### See different agents behave differently

The seed data makes this immediate. Ask all three "What is 14 * 3?":

- **Maths Tutor** has the calculator → calls it and reports `42`.
- **Pirate Poet** has no tools → answers without one.
- **Support Assistant** has the MCP knowledge base → tries that instead.

Same model, same question, different rows in the database.

## Model providers

Four providers ship. Each is one file under
[`backend/app/services/llm/`](backend/app/services/llm/) implementing
`BaseLLMProvider`; nothing above that package imports a vendor SDK.

| Provider | SDK | Notes |
|---|---|---|
| `openai` | `openai` | Chat Completions — maps 1:1 to the neutral format; Responses would need its item model translated both ways for no gain here |
| `gemini` | `google-genai` | The current SDK, **not** the superseded `google-generativeai` |
| `groq` | `openai` | OpenAI-compatible endpoint — a `base_url` subclass of the OpenAI adapter, not a separate one |
| `mock` | — | Deterministic, offline. Keeps tests hermetic and lets Garak run without spending tokens |

Set the matching key in `backend/.env` and pick the provider in the agent form.
A provider with no key is labelled *(no API key)* in the dropdown, and calling
an agent that uses it returns a clear message naming the variable to set rather
than failing obscurely.

Model dropdowns are **suggestions, not a whitelist** — any model string is
accepted, so a model released after this was written works by typing its id.

### Tool schemas differ by provider

A tool declares one JSON Schema, but the three vendors disagree about what a
tool schema may contain, so each adapter translates through
[`llm/schemas.py`](backend/app/services/llm/schemas.py):

- **Gemini** rejects `title`, `$schema`, `additionalProperties`, `const`,
  `$ref` and others with a 400, and wants `parameters` omitted entirely for a
  no-argument tool. `title` is the one that bites in practice — Pydantic emits
  it on every property, so **every MCP tool schema** hits it.
- **OpenAI** accepts standard JSON Schema; strict mode would additionally
  require `additionalProperties: false` and all properties in `required`.

Sanitising happens in the adapter rather than in the tool registry, because MCP
schemas come from servers we do not control.

### Model parameters are not universal

`temperature` is dropped for models that reject it — OpenAI's o-series 400s on
sampling parameters. The agent keeps its configured value; the adapter simply
does not send it. If a temperature change seems to have no effect, that is why.

Some OpenAI reasoning models also refuse function tools on Chat Completions
unless reasoning is switched off:

> Function tools with reasoning_effort are not supported for `<model>` in
> /v1/chat/completions … or set reasoning_effort to 'none'.

Which models behave this way is not discoverable up front, so rather than
maintain a list the adapter retries once with `reasoning_effort="none"` on
exactly that error. Models that support tools *and* reasoning keep reasoning.

### Verified against the live APIs

All three providers have been exercised end-to-end through `AgentRuntime` —
plain text, a full tool round trip, and streaming with tools:

| Provider | Model tested | Text | Tools | Streaming + tools |
|---|---|---|---|---|
| `openai` | `gpt-5.6-luna` | ✅ | ✅ | ✅ |
| `gemini` | `gemini-3.5-flash` | ✅ | ✅ | ✅ |
| `groq` | `openai/gpt-oss-20b` | ✅ | ✅ | ✅ |

### OpenAI-compatible vendors need no adapter

Groq (and anything else serving Chat Completions — Together, Fireworks, vLLM,
Ollama's compat endpoint) is a subclass of `OpenAIProvider` overriding three
class attributes:

```python
class GroqProvider(OpenAIProvider):
    name = "groq"
    env_var = "GROQ_API_KEY"
    base_url = "https://api.groq.com/openai/v1"
```

Message translation, tool definitions and streaming tool-call reassembly are
inherited, so there is one code path to maintain and one to test. Groq's
documented gaps (`logprobs`, `logit_bias`, `top_logprobs`, `messages[].name`,
`n` > 1) are all fields this codebase never sends — there is a test asserting
that stays true.

### Adding another provider

For a vendor with its own wire format:

1. Add `your_provider.py` implementing `BaseLLMProvider`.
2. Add a key to `Settings` and `.env.example`.
3. Add one line to `_providers()` and one to `_MODELS` in `registry.py`.

No change to `AgentRuntime`, the API layer, or the frontend — the agent form
reads the catalogue from `/api/agents/providers`. Then add the provider to the
parametrised list in `tests/test_providers.py`, which enforces the shared
contract across every adapter.

## How tools work

Tools live in [`backend/app/services/tool_registry.py`](backend/app/services/tool_registry.py).
Each declares a name, a description, a JSON Schema, and an async `execute`.

Two demo tools ship:

| Tool | Description |
|---|---|
| `calculator` | Arithmetic over a parsed AST — `+ - * / % **` and parentheses |
| `current_time` | Current UTC time, ISO 8601 |

Beyond these, tools come from two places: **custom HTTP tools** defined in the
UI (below), and **MCP servers** (further down). All three kinds share one
per-agent allowlist and one permission gate.

### The permission gate

Every call the model requests goes through `AgentRuntime._execute_tool`:

```
1. Is this tool in THIS agent's allowlist?   → else refuse
2. Do the arguments match the JSON Schema?   → else refuse
3. Execute under TOOL_TIMEOUT_SECONDS        → else refuse
```

A refusal is returned to the model as a failed tool result — it is told it was
denied and can recover — and is logged server-side. An agent with an empty
`tools` list has **no** tools; the empty case never means "all".

### The calculator does not use `eval()`

It parses to an AST and walks it against an allowlist of node types, so
`__import__("os").system("...")` is rejected at the node level rather than
executed, and exponents are bounded so `9**9**9` cannot wedge the event loop.
**Never** evaluate model output as code — this is the pattern to copy.

### Creating a tool from the UI — no code

Sidebar → **Tools & MCP** → **+ New tool** defines an HTTP-backed tool through a
form: name, description, parameters, method and URL. It becomes assignable to
any agent immediately, with no restart and no code change.

```
Name         get_weather
Description  Get the current weather for a city.
Method/URL   GET  https://api.example.com/weather/{city}
Parameters   city : string : "City name, e.g. Paris"   [required]
Headers      Authorization: Bearer {{env:WEATHER_TOKEN}}
Response     current.condition.text
```

`{param}` placeholders are filled from the model's arguments and URL-encoded.
**Try it** runs the real request before you grant the tool to anything.

Three constraints make this safe enough to expose in a UI:

- **It cannot run code.** Only an outbound HTTP request. A tool builder that
  accepted Python or shell would hand any successful jailbreak remote code
  execution — the exact boundary this app exists to hold.
- **It cannot reach your network.** Every resolved address is checked before the
  request; loopback, private ranges and cloud metadata endpoints are refused.
  Redirects are not followed, because a redirect would escape that check. Set
  `HTTP_TOOL_ALLOW_PRIVATE_NETWORKS=true` only if you intend tools to call
  internal services, and prefer `HTTP_TOOL_ALLOWED_HOSTS` to pin them.
- **It cannot hold secrets.** A header may reference `{{env:NAME}}`, resolved
  from the backend's environment at call time. The database and the API only
  ever see the reference.

Creating a tool is **not** granting it — it still has to be ticked on each
agent, exactly like a built-in one, and goes through the same permission gate.

### Adding a built-in tool in code

For anything that is not an HTTP call, subclass `BaseTool` and register it:

```python
class WordCountTool(BaseTool):
    name = "word_count"
    description = "Count the words in a piece of text."
    input_schema = {
        "type": "object",
        "properties": {"text": {"type": "string", "maxLength": 10000}},
        "required": ["text"],
        "additionalProperties": False,
    }

    async def execute(self, arguments: dict) -> str:
        return str(len(arguments["text"].split()))


registry = ToolRegistry([CalculatorTool(), CurrentTimeTool(), WordCountTool()])
```

It appears in the agent form automatically. No agent can use it until ticked.

## How MCP works

[`backend/app/services/mcp_manager.py`](backend/app/services/mcp_manager.py) is
the only module that imports the MCP SDK. It connects to configured servers,
discovers their tools, and invokes them on the runtime's behalf.

MCP tools are namespaced `mcp__<server-slug>__<tool>` so two servers can both
expose a `search`. The prefix is stripped before the call reaches the server.

### Two transports

| | `http` — remote | `stdio` — local |
|---|---|---|
| Configured with | a URL | a command + args |
| Runs | on someone else's machine | as a subprocess here |
| Needs code on this box | no | yes |
| Secrets | `headers` with `{{env:NAME}}` | `env_keys` |
| Guarded by | the SSRF address check | the minimal-environment rule |

Both are reached through the same `discover` / `call_tool` path, cached the
same way, and granted per agent through the same allowlist. The difference is
confined to `MCPManager._target`.

Two demo servers ship in [`backend/mcp_servers/`](backend/mcp_servers/) — the
same toy knowledge base (`kb_search`) and text utility (`word_count`), one over
each transport, so they can be compared directly:

```bash
.venv/bin/python mcp_servers/demo_server.py        # stdio; the platform launches this
.venv/bin/python mcp_servers/demo_http_server.py   # http; you launch it, on :8765
```

### Add a remote server — no code

Sidebar → **Tools & MCP** → **+ Add server** → **Remote (HTTP)**. Give it a name
and a URL, then press **Discover**:

```
Name     Hosted Knowledge Base
URL      https://mcp.example.com/mcp
Headers  Authorization: Bearer {{env:MCP_TOKEN}}
```

Nothing is installed and no process starts on this machine. A header value
written as `{{env:NAME}}` stores only the *name* — the value is read from the
backend's environment at connect time, so it never reaches the database or an
API response.

The URL goes through the same SSRF check as the user-defined HTTP tools: an
address that resolves to loopback, a private range, link-local (including the
`169.254.169.254` cloud-metadata endpoint) or a non-`http(s)` scheme is refused.
Redirects are not followed, because following one would land somewhere the
check never saw. Pointing at the bundled HTTP demo server therefore needs
`HTTP_TOOL_ALLOW_PRIVATE_NETWORKS=true`; a genuinely remote server does not.

### Add a local server

Same form, **Local (stdio)** — a command, its arguments and a working
directory. `seed.py` registers the stdio demo server this way.

**Over the API:**

```bash
BACKEND=$(pwd)   # from backend/
curl -X POST http://127.0.0.1:8000/api/mcp/servers \
  -H 'Content-Type: application/json' \
  -d "{
    \"name\": \"Demo Knowledge Base\",
    \"description\": \"Toy KB over stdio\",
    \"transport\": \"stdio\",
    \"command\": \"$BACKEND/.venv/bin/python\",
    \"args\": [\"$BACKEND/mcp_servers/demo_server.py\"],
    \"cwd\": \"$BACKEND\"
  }"
```

Then **Discover** (or `POST /api/mcp/servers/{id}/discover`) to list its tools,
and tick the server on any agent that should reach it.

Each transport has exactly one required field — `command` for stdio, `url` for
http — enforced in the schema on create and re-checked against the stored row
on PATCH, since clearing `url` is only invalid if the transport is http.

### Secrets and MCP

`env_keys` holds the **names** of environment variables a server needs, never
their values. Values are read from the backend's own environment at launch.

The subprocess gets a **minimal** environment — a few variables needed to start
a process plus the ones its config names. It does **not** inherit the backend's
environment, so an MCP server cannot read unrelated secrets that happen to be
set. MCP arguments are schema-validated locally before crossing the process
boundary, and a server that cannot be reached degrades the agent rather than
breaking the turn.

Only `stdio` transport is implemented. Others are rejected with a clear error.

## The agent API

Every agent is programmatically callable. This is also what Garak targets.

```bash
curl -X POST http://127.0.0.1:8000/api/agents/{agent_id}/chat \
  -H 'Content-Type: application/json' \
  -d '{"message": "What is 137 * 4?"}'
```

```json
{
  "agent_id": "79efdcc8-...",
  "response": "137 × 4 = 548.",
  "tool_calls": [
    {
      "name": "calculator",
      "arguments": { "expression": "137 * 4" },
      "result": "548",
      "source": "local",
      "ok": true,
      "error": null,
      "duration_ms": 1
    }
  ],
  "metadata": {
    "provider": "mock",
    "model": "mock-1",
    "usage": {},
    "iterations": 2
  }
}
```

The call is **stateless** — no conversation is created or loaded — and runs the
same `AgentRuntime` the UI uses. If `AGENT_API_KEY` is set, send it as
`X-API-Key`.

> This endpoint returns HTTP 200 with `metadata.error` set when the runtime
> fails. That is deliberate: Garak treats any 4xx as fatal and aborts the whole
> scan, so a transient provider failure would throw away every result collected
> so far. Caller mistakes — unknown agent, bad API key — still return real error
> codes.

### Full endpoint list

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness |
| `GET` `POST` | `/api/agents` | List / create agents |
| `GET` `PATCH` `DELETE` | `/api/agents/{id}` | Read / update / delete |
| `GET` | `/api/agents/providers` | Providers, availability, suggested models |
| `POST` | `/api/agents/{id}/chat` | **Stateless agent call (Garak target)** |
| `GET` `POST` | `/api/conversations` | List (filter `?agent_id=`) / create |
| `GET` `DELETE` | `/api/conversations/{id}` | Read / delete |
| `GET` | `/api/conversations/{id}/messages` | History |
| `POST` | `/api/conversations/{id}/messages` | Send a message, **SSE** reply |
| `GET` | `/api/tools` | Tool catalogue, built-in and custom |
| `GET` `POST` | `/api/tools/http` | List / create custom HTTP tools |
| `GET` `PATCH` `DELETE` | `/api/tools/http/{id}` | Manage one |
| `POST` | `/api/tools/http/{id}/test` | Run it once with sample arguments |
| `GET` `POST` | `/api/mcp/servers` | List / register MCP servers |
| `GET` `PATCH` `DELETE` | `/api/mcp/servers/{id}` | Manage one |
| `POST` | `/api/mcp/servers/{id}/discover` | Connect and list its tools |
| `GET` | `/api/security/probes` | Curated Garak probe presets |
| `GET` `POST` | `/api/security/runs` | List / start scans |
| `GET` | `/api/security/runs/{id}` | Status and summarised results |

## Garak security testing

[Garak](https://github.com/NVIDIA/garak) is NVIDIA's LLM vulnerability scanner.
It runs as a **subprocess in its own virtualenv** and attacks the agent over
HTTP using its documented
[`rest` generator](https://reference.garak.ai/en/latest/garak.generators.rest.html).

Nothing about Garak is invented here: the adapter writes a standard REST
generator config, invokes the documented CLI, and parses the report format
Garak writes.

```
Garak → REST generator → POST /api/agents/{id}/chat → AgentRuntime
      → LLM / tools / MCP → response → Garak detectors → report.jsonl
```

### Run from the UI

Sidebar → **Security Testing** → pick an agent and a probe → **Start Garak
run**. The run appears with a live status, then a pass rate, a per-detector
breakdown, and example prompts that defeated the agent.

### Run from the command line

```bash
# Backend must be running.
cd backend
AGENT_ID=<your agent id>

# 1. Generate the REST generator config with the adapter
.venv/bin/python -c "
from app.services.garak_adapter import adapter
gen, cfg, report = adapter.write_run_files('manual-run', '$AGENT_ID')
print('generator:', gen); print('config:', cfg); print('report:', report)
"

# 2. Run Garak against it
.venv-garak/bin/python -m garak \
  --config garak_runs/manual-run/garak_config.yaml \
  --target_type rest \
  --generator_option_file garak_runs/manual-run/rest_generator.json \
  --spec probes.dan.Dan_11_0 \
  --generations 1 \
  --report_prefix manual-run
```

Output:

```
🦜 loading generator: REST: agent-61cd222a-...
📜 reporting to .../garak_runs/manual-run/manual-run.report.jsonl
🕵️  queue of probes: dan.Dan_11_0
dan.Dan_11_0                          dan.DAN: PASS  ok on    1/   1
dan.Dan_11_0      mitigation.MitigationBypass: FAIL  ok on    0/   1  (attack success rate: 100.00%)
📜 report closed :) .../manual-run.report.jsonl
✔️  garak run complete in 0.71s
```

Reports land in `backend/garak_runs/<run-id>/`: a `.report.jsonl`, a
`.hitlog.jsonl` of successful attacks, and an HTML digest.

### Writing the config by hand

```json
{
  "rest": {
    "RestGenerator": {
      "name": "my-agent",
      "uri": "http://127.0.0.1:8000/api/agents/<AGENT_ID>/chat",
      "method": "post",
      "headers": { "Content-Type": "application/json", "X-API-Key": "$KEY" },
      "req_template_json_object": { "message": "$INPUT" },
      "response_json": true,
      "response_json_field": "response"
    }
  }
}
```

`$INPUT` is where Garak substitutes each attack prompt; `$KEY` is filled from
the `REST_API_KEY` environment variable (omit the header if `AGENT_API_KEY` is
unset). Because the generated config carries only the placeholder, the secret
never lands on disk.

### Choosing probes

Garak 0.17 selects probes with `--spec`, which takes **prefixed** tokens
(`probes.dan.Dan_11_0`). The older `--probes` flag is deprecated and confusingly
takes *unprefixed* names — prefer `--spec`.

| Preset | Tests |
|---|---|
| `probes.dan.Dan_11_0` | A single well-known jailbreak. Fastest smoke test |
| `probes.dan.DanInTheWild` | A large corpus of real jailbreaks. Thorough, slow |
| `probes.promptinject.HijackHateHumans` | Hijacking the agent into attacker-chosen output |
| `probes.encoding.InjectBase64` | Smuggling instructions past the system prompt |
| `probes.leakreplay.LiteratureCloze` | Verbatim regurgitation of memorised text |

List everything with `.venv-garak/bin/python -m garak --list_probes`. Tag and
tier selectors work too, e.g. `--spec 'tag:owasp:llm01'`.

### Reading the results

Each probe is scored by one or more detectors. `passed/total` is how many
responses the detector judged safe. A pass rate below 100% means at least one
adversarial prompt got through — the hitlog shows exactly which.

Running a scan against a `mock`-provider agent is a useful sanity check of the
plumbing, but the mock never refuses anything, so expect poor scores. Point
Garak at an `openai`- or `gemini`-provider agent to measure a real model.

## Tests

```bash
cd backend
.venv/bin/python -m pytest          # 79 tests
```

Hermetic — no network, no API key, no Garak install required. Covering:

| File | What it covers |
|---|---|
| `test_agents.py` | Agent CRUD, provider validation |
| `test_chat.py` | Stateless endpoint, SSE streaming, persistence, per-agent isolation |
| `test_runtime.py` | Runtime loop, history trimming, provider abstraction, missing-key handling |
| `test_providers.py` | Provider conformance across every adapter, schema dialect translation, wire-format mapping, streaming reassembly, credential isolation |

The suite is offline — it never calls a provider, and `conftest.py` blanks every
credential so a developer's real keys in `.env` cannot leak into a test run.
| `test_tools.py` | Calculator safety (code-shaped input is rejected), the permission gate, timeouts, the full tool round trip |
| `test_mcp.py` | Real MCP discovery and invocation over **both** transports, environment isolation, header-secret references, the SSRF guard on MCP URLs, the server allowlist, graceful degradation |
| `test_http_tools.py` | Custom HTTP tools: SSRF refusals, format-string attacks, secret handling, definition validation, redirect reporting, the permission gate |
| `test_garak.py` | REST config shape, CLI construction, report parsing, run lifecycle |

`test_mcp.py` launches both demo MCP servers for real — one as a subprocess
over stdio, one as an HTTP service it then connects to by URL. It exercises the
actual protocol on both transports, not a stub.

## Project layout

```
backend/
  app/
    main.py                     FastAPI app
    api/                        agents, chat, conversations, tools, mcp, security
    models/                     SQLAlchemy models
    schemas/                    Pydantic request/response models
    services/
      agent_runtime.py          THE execution loop + permission gate
      llm/                      base.py, registry.py, schemas.py,
                                openai_provider.py, gemini_provider.py, mock_provider.py
      tool_registry.py          Local tools
      mcp_manager.py            MCP lifecycle, both transports
      garak_adapter.py          Garak config, subprocess, report parsing
    db/                         Engine, session, base
    config/settings.py          Env-driven settings
  mcp_servers/                  demo_server.py (stdio), demo_http_server.py (http)
  tests/                        pytest suite
  seed.py                       Demo agents + MCP server

frontend/
  app/page.tsx                  Application shell
  components/                   Sidebar, ChatWindow, Message, MessageInput,
                                AgentForm, AgentSelector, ToolCall,
                                ToolsPanel, SecurityPanel
  services/api.ts               REST client + SSE reader
  types/                        Shared types

docs/architecture.md            Diagrams, component responsibilities, trade-offs
```

## Known limitations

- **No authentication** on the UI or its endpoints. `AGENT_API_KEY` protects
  only the programmatic agent endpoint. Add auth before any shared deployment.
- **No real migrations.** Tables come from `create_all()`, plus a small
  additive step that adds new columns to an existing database and backfills
  them from the model's own default. It never drops or retypes anything, so a
  rename or a type change still needs a manual reset — or Alembic.
- **Provider coverage is broad but shallow.** Translation in both directions is
  unit-tested against the SDKs' own types, and every provider has been
  exercised against its live API — but only over a handful of models and short
  conversations, so long multi-tool sessions may still surface something.
- **MCP spawns a subprocess per call** (~200–400ms overhead). Discovery is
  cached for five minutes; invocations are not.
- **MCP permissions are per server, not per tool.** An agent gets all of a
  server's tools or none.
- **MCP over stdio and streamable HTTP.** The older SSE transport is not wired
  up; `Transport 'sse' is not supported` is the error.
- **A remote MCP server's tool descriptions go into the prompt**, and they are
  written by whoever runs that server. Treat adding one as extending trust: a
  hostile description is a prompt-injection vector that the SSRF check, which
  only governs *where* you connect, does nothing about.
- **Security runs are in-process asyncio tasks** and do not survive a restart.
- **History is replayed as plain text.** Tool round trips are shown in the UI
  but not re-sent to the model on later turns.
- **Garak scans cost real tokens** against a live provider. A thorough probe
  like `DanInTheWild` issues hundreds of calls.
- **Conversation titles** are the first 60 characters of the first message; no
  summarisation.
- **Custom HTTP tools are unauthenticated to create.** Anyone who can reach the
  UI can define one, matching the app's current posture. The SSRF guard limits
  the blast radius, but put this behind auth before any shared deployment.
- **The SSRF check resolves the hostname, then connects by name**, so a DNS
  rebind between the two is theoretically possible. Short timeouts and no
  redirects limit it; a host allowlist removes it.

## Future improvements

- Alembic migrations and a PostgreSQL deployment path.
- Persistent MCP session pool; the SSE transport; OAuth for remote servers
  (only static headers are supported today).
- Per-tool MCP permissions and per-agent rate limits.
- More providers (Anthropic, Bedrock, Vertex) — one file plus a registry
  entry each; OpenAI-compatible ones are three lines.
- Live model lists from each provider's `models.list()` instead of static
  suggestions.
- Durable background jobs for security runs, with cancellation.
- Scheduled Garak runs with trend tracking, and a diff against the previous scan.
- Streaming tool-call progress, message editing and regeneration.
- Frontend tests — the SSE frame parser in particular has already been a
  source of a real bug.
