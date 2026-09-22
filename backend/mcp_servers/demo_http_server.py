"""The demo MCP server again, this time over streamable HTTP.

Its twin, `demo_server.py`, speaks stdio and is launched by the platform as a
subprocess. This one is a *remote* server: you start it yourself, and the
platform connects to it by URL, exactly as it would connect to an MCP server
run by someone else on another machine.

    .venv/bin/python mcp_servers/demo_http_server.py

Then add it in the UI under Tools & MCP with transport "Remote (HTTP)" and:

    http://127.0.0.1:8765/mcp

Reaching a loopback address needs HTTP_TOOL_ALLOW_PRIVATE_NETWORKS=true; the
SSRF guard refuses it otherwise, which is the correct default for a URL that
anyone with the UI can type. A genuinely remote server needs no such setting.

The tools are deliberately identical to the stdio demo's, so the two can be
compared directly: same tools, same namespacing, different transport.
"""

import os

from mcp.server import MCPServer

mcp = MCPServer(
    name="demo-remote-kb",
    instructions="The demo knowledge base, served over streamable HTTP.",
    version="0.1.0",
)

_DOCUMENTS = {
    "onboarding": (
        "New engineers should read the architecture doc, get a database seeded "
        "locally, and pair with a buddy for their first week."
    ),
    "deployment": (
        "Deployments run from the main branch. Every change needs a green test "
        "suite and one approving review before it ships."
    ),
    "security": (
        "Secrets live in environment variables and never in the database. "
        "Agent tool access is allowlisted per agent."
    ),
    "support": (
        "Support rotates weekly. The on-call engineer triages within one hour "
        "during business hours."
    ),
}


@mcp.tool()
def kb_search(query: str) -> str:
    """Search the internal knowledge base for a topic.

    Args:
        query: A topic to look up, for example "deployment" or "security".
    """
    needle = query.lower().strip()
    hits = [
        f"{topic}: {text}"
        for topic, text in _DOCUMENTS.items()
        if needle in topic or needle in text.lower()
    ]
    if not hits:
        return f"No knowledge-base entries matched {query!r}. Known topics: " + ", ".join(
            sorted(_DOCUMENTS)
        )
    return "\n\n".join(hits)


@mcp.tool()
def word_count(text: str) -> str:
    """Count the words and characters in a piece of text.

    Args:
        text: The text to measure.
    """
    words = len(text.split())
    return f"{words} words, {len(text)} characters"


if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host="127.0.0.1",
        port=int(os.environ.get("DEMO_MCP_PORT", "8765")),
    )
