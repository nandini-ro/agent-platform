"""Repoint stdio MCP servers at this machine's interpreter and script paths.

    python relink_mcp.py

A stdio server row stores an absolute command, script path and working
directory. Those are correct on the machine that created them and meaningless
anywhere else, so a database carried from a laptop into a container spawns
nothing, and every tool on it disappears from the agent at discovery time.

This rewrites those three fields for servers whose script still exists in
`mcp_servers/`, matched by filename. Rows pointing at a script outside this
checkout are reported and left alone: guessing at a path this repo knows
nothing about would be worse than saying it could not be resolved. HTTP
servers are untouched - a URL travels between machines perfectly well.
"""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import select

from app.db.session import SessionLocal, init_db
from app.models.mcp_server import MCPServer

BACKEND_DIR = Path(__file__).parent.resolve()
SERVERS_DIR = BACKEND_DIR / "mcp_servers"


def _local_copy(arg: str) -> Path | None:
    """This checkout's copy of the script `arg` names, if it has one."""
    candidate = SERVERS_DIR / Path(arg).name
    return candidate if candidate.is_file() else None


def main() -> None:
    init_db()
    session = SessionLocal()
    relinked = skipped = 0
    try:
        for server in session.scalars(select(MCPServer)):
            if server.transport != "stdio":
                continue

            args = list(server.args or [])
            if not any(_local_copy(a) for a in args):
                print(f"  ? {server.slug}: no script from mcp_servers/ in {args}")
                skipped += 1
                continue

            server.command = sys.executable
            server.args = [str(_local_copy(a) or a) for a in args]
            server.cwd = str(BACKEND_DIR)
            relinked += 1
            print(f"  + {server.slug} -> {server.command} {' '.join(server.args)}")

        session.commit()
    finally:
        session.close()

    print(f"\n{relinked} relinked, {skipped} left alone.")
    if skipped:
        print("Anything left alone needs its command and args fixed by hand,")
        print("or deleting and re-creating in the UI.")


if __name__ == "__main__":
    main()
