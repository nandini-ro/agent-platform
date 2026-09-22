"""MCP server configuration and discovery.

Kept deliberately separate from the chat and agent routers: MCP is an optional
subsystem, and none of the core chat path imports this module.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.mcp_server import MCPServer
from app.schemas.mcp import (
    TRANSPORTS,
    MCPDiscoverResponse,
    MCPServerCreate,
    MCPServerRead,
    MCPServerUpdate,
)
from app.services.mcp_manager import MCPError, manager, slugify

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


@router.get("/servers", response_model=list[MCPServerRead])
def list_servers(db: Session = Depends(get_db)) -> list[MCPServer]:
    return list(db.scalars(select(MCPServer).order_by(MCPServer.created_at)))


@router.post(
    "/servers", response_model=MCPServerRead, status_code=status.HTTP_201_CREATED
)
def create_server(payload: MCPServerCreate, db: Session = Depends(get_db)) -> MCPServer:
    slug = _unique_slug(db, slugify(payload.name))
    server = MCPServer(slug=slug, **payload.model_dump())
    db.add(server)
    db.commit()
    db.refresh(server)
    return server


@router.get("/servers/{server_id}", response_model=MCPServerRead)
def read_server(server_id: str, db: Session = Depends(get_db)) -> MCPServer:
    return _get_or_404(db, server_id)


@router.patch("/servers/{server_id}", response_model=MCPServerRead)
def update_server(
    server_id: str, payload: MCPServerUpdate, db: Session = Depends(get_db)
) -> MCPServer:
    server = _get_or_404(db, server_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(server, key, value)
    _assert_transport_is_usable(server)
    db.add(server)
    db.commit()
    db.refresh(server)
    manager.invalidate(server_id)  # config changed; re-discover on next use
    return server


@router.delete("/servers/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_server(server_id: str, db: Session = Depends(get_db)) -> Response:
    db.delete(_get_or_404(db, server_id))
    db.commit()
    manager.invalidate(server_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/servers/{server_id}/discover", response_model=MCPDiscoverResponse)
async def discover_server(
    server_id: str, db: Session = Depends(get_db)
) -> MCPDiscoverResponse:
    """Connect to the server and list its tools.

    Connection failures are reported in the body rather than as a 5xx: an
    unreachable MCP server is a normal state the UI should render, not a bug
    in this API.
    """
    server = _get_or_404(db, server_id)
    try:
        tools = await manager.discover(server, use_cache=False)
    except MCPError as exc:
        return MCPDiscoverResponse(server_id=server_id, connected=False, error=str(exc))
    return MCPDiscoverResponse(
        server_id=server_id,
        connected=True,
        tools=[t.__dict__ for t in tools],
    )


def _assert_transport_is_usable(server: MCPServer) -> None:
    """Re-check the transport's required field against the merged row.

    A PATCH is validated field by field, so only here - with the stored row and
    the change applied together - can we tell that clearing `url` has left an
    http server with nothing to connect to.
    """
    if server.transport not in TRANSPORTS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"transport must be one of {', '.join(TRANSPORTS)}",
        )
    missing = "command" if server.transport == "stdio" else "url"
    if not (getattr(server, missing) or "").strip():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"A {server.transport} server needs a {missing}.",
        )


def _get_or_404(db: Session, server_id: str) -> MCPServer:
    server = db.get(MCPServer, server_id)
    if server is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "MCP server not found")
    return server


def _unique_slug(db: Session, base: str) -> str:
    slug, suffix = base, 2
    while db.scalar(select(MCPServer).where(MCPServer.slug == slug)) is not None:
        slug = f"{base}-{suffix}"
        suffix += 1
    return slug
