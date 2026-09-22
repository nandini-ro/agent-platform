"""A small MCP server used to demonstrate the integration end to end.

Run directly for a manual check:

    .venv/bin/python mcp_servers/demo_server.py

It speaks MCP over stdio, so the platform launches it as a subprocess. The
tools are intentionally deterministic and side-effect free - the point is to
prove the transport, discovery, and permission path, not to do anything clever.

Built with the MCP Python SDK v2 API (`mcp.server.MCPServer`).
"""

from mcp.server import MCPServer

mcp = MCPServer(
    name="demo-knowledge-base",
    instructions="A toy knowledge base plus a text utility, for MCP demos.",
    version="0.1.0",
)

# Stand-in for whatever a real MCP server would be fronting.
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
    mcp.run(transport="stdio")
