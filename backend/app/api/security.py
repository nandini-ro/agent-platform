"""Security testing (Garak).

Runs are fired as background asyncio tasks and tracked in the database, so the
UI can start one and poll it. Garak itself runs as a subprocess against this
API's own agent endpoint - see app/services/garak_adapter.py.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import new_id, utcnow
from app.db.session import SessionLocal, get_db
from app.models.agent import Agent
from app.models.security_run import SecurityRun
from app.schemas.security import ProbePreset, SecurityRunCreate, SecurityRunRead
from app.services.garak_adapter import (
    DEFAULT_PROBES,
    PROBE_PRESETS,
    GarakError,
    adapter,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/security", tags=["security"])


@router.get("/probes", response_model=list[ProbePreset])
def list_probe_presets() -> list[dict]:
    """Curated garak probe selectors for the UI."""
    return PROBE_PRESETS


@router.get("/runs", response_model=list[SecurityRunRead])
def list_runs(
    agent_id: str | None = None, db: Session = Depends(get_db)
) -> list[SecurityRun]:
    stmt = select(SecurityRun).order_by(SecurityRun.created_at.desc())
    if agent_id:
        stmt = stmt.where(SecurityRun.agent_id == agent_id)
    return list(db.scalars(stmt))


@router.post("/runs", response_model=SecurityRunRead, status_code=status.HTTP_202_ACCEPTED)
async def start_run(
    payload: SecurityRunCreate, db: Session = Depends(get_db)
) -> SecurityRun:
    if db.get(Agent, payload.agent_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent not found")

    run = SecurityRun(
        id=new_id(),
        agent_id=payload.agent_id,
        status="queued",
        probes=payload.probes or DEFAULT_PROBES,
        generations=payload.generations,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    # Fire and forget; status lives in the database, not in this task.
    asyncio.create_task(_execute_run(run.id))
    return run


@router.get("/runs/{run_id}", response_model=SecurityRunRead)
def read_run(run_id: str, db: Session = Depends(get_db)) -> SecurityRun:
    run = db.get(SecurityRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    return run


async def _execute_run(run_id: str) -> None:
    """Run garak and record the outcome. Owns its own DB session."""
    session = SessionLocal()
    try:
        run = session.get(SecurityRun, run_id)
        if run is None:
            return
        run.status = "running"
        session.add(run)
        session.commit()

        try:
            summary, report_path, log_tail = await adapter.run(
                run_id=run.id,
                agent_id=run.agent_id,
                probes=run.probes,
                generations=run.generations,
            )
        except GarakError as exc:
            logger.warning("garak run %s failed: %s", run_id, exc)
            run.status = "failed"
            run.error = str(exc)
        except Exception as exc:  # noqa: BLE001
            logger.exception("garak run %s crashed", run_id)
            run.status = "failed"
            run.error = f"Unexpected error: {exc}"
        else:
            run.status = "completed"
            run.report_path = str(report_path)
            run.summary = summary.to_dict()
            run.log_tail = log_tail

        run.finished_at = utcnow()
        session.add(run)
        session.commit()
    finally:
        session.close()
