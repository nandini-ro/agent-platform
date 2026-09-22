"""Tool catalogue, and CRUD for user-defined HTTP tools.

Code-defined tools are read-only: the registry is source, not configuration.
HTTP tools are created through the UI and stored in the database - they cannot
execute code, only make an outbound request under the constraints enforced in
`app/services/http_tool.py`.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.http_tool import HTTPTool
from app.schemas.http_tool import (
    HTTPToolCreate,
    HTTPToolRead,
    HTTPToolTestRequest,
    HTTPToolTestResult,
    HTTPToolUpdate,
    ToolCatalogueEntry,
)
from app.services.http_tool import (
    HTTPToolError,
    UserHTTPTool,
    validate_definition,
)
from app.services.tool_registry import ToolError, registry

router = APIRouter(prefix="/api/tools", tags=["tools"])


@router.get("", response_model=list[ToolCatalogueEntry])
def list_tools(db: Session = Depends(get_db)) -> list[dict]:
    """Every tool an agent could be granted, from either source."""
    entries = [{**t, "source": "builtin"} for t in registry.catalogue()]
    rows = db.scalars(select(HTTPTool).order_by(HTTPTool.name))
    entries.extend(
        {
            "name": row.name,
            "description": row.description or f"Call {row.url_template}",
            "input_schema": row.parameters or {"type": "object", "properties": {}},
            "source": "http",
        }
        for row in rows
        if row.enabled
    )
    return entries


# -- user-defined HTTP tools ------------------------------------------------


@router.get("/http", response_model=list[HTTPToolRead])
def list_http_tools(db: Session = Depends(get_db)) -> list[HTTPTool]:
    return list(db.scalars(select(HTTPTool).order_by(HTTPTool.created_at.desc())))


@router.post("/http", response_model=HTTPToolRead, status_code=status.HTTP_201_CREATED)
def create_http_tool(payload: HTTPToolCreate, db: Session = Depends(get_db)) -> HTTPTool:
    _validate(payload.model_dump())
    if db.scalar(select(HTTPTool).where(HTTPTool.name == payload.name)) is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"A tool named '{payload.name}' already exists."
        )
    tool = HTTPTool(**payload.model_dump())
    db.add(tool)
    db.commit()
    db.refresh(tool)
    return tool


@router.get("/http/{tool_id}", response_model=HTTPToolRead)
def read_http_tool(tool_id: str, db: Session = Depends(get_db)) -> HTTPTool:
    return _get_or_404(db, tool_id)


@router.patch("/http/{tool_id}", response_model=HTTPToolRead)
def update_http_tool(
    tool_id: str, payload: HTTPToolUpdate, db: Session = Depends(get_db)
) -> HTTPTool:
    tool = _get_or_404(db, tool_id)
    changes = payload.model_dump(exclude_unset=True)
    merged = {
        "name": tool.name,
        "method": changes.get("method", tool.method),
        "url_template": changes.get("url_template", tool.url_template),
        "headers": changes.get("headers", tool.headers),
        "parameters": changes.get("parameters", tool.parameters),
    }
    _validate(merged, existing_name=tool.name)
    for key, value in changes.items():
        setattr(tool, key, value)
    db.add(tool)
    db.commit()
    db.refresh(tool)
    return tool


@router.delete("/http/{tool_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_http_tool(tool_id: str, db: Session = Depends(get_db)) -> Response:
    db.delete(_get_or_404(db, tool_id))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/http/{tool_id}/test", response_model=HTTPToolTestResult)
async def test_http_tool(
    tool_id: str, payload: HTTPToolTestRequest, db: Session = Depends(get_db)
) -> HTTPToolTestResult:
    """Run the tool once with supplied arguments, so it can be checked before
    an agent is given it. Same validation and SSRF checks as a real call."""
    tool = UserHTTPTool(_get_or_404(db, tool_id))
    started = time.perf_counter()
    try:
        tool.validate(payload.arguments)
        result = await tool.execute(payload.arguments)
    except (HTTPToolError, ToolError) as exc:
        return HTTPToolTestResult(
            ok=False,
            error=str(exc),
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
    except Exception as exc:  # noqa: BLE001
        return HTTPToolTestResult(ok=False, error=f"Unexpected error: {exc}")
    return HTTPToolTestResult(
        ok=True,
        result=result,
        duration_ms=int((time.perf_counter() - started) * 1000),
    )


def _validate(values: dict, existing_name: str | None = None) -> None:
    try:
        validate_definition(
            name=existing_name or values["name"],
            method=values.get("method") or "GET",
            url_template=values["url_template"],
            headers=values.get("headers") or {},
            parameters=values.get("parameters") or {},
        )
    except HTTPToolError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


def _get_or_404(db: Session, tool_id: str) -> HTTPTool:
    tool = db.get(HTTPTool, tool_id)
    if tool is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tool not found")
    return tool
