"""Audit API routes.

`POST /audit` streams a metrics-only audit run over SSE; the run history
endpoints read the same on-disk stores the CLI's `audit list` / `audit show`
use, so runs started from either surface show up in both.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, AsyncGenerator, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from sse_starlette.sse import EventSourceResponse

from shared.api.target_guard import TargetGuard, require_target_body

from ..capture_service import CaptureService
from ..service import AuditService
from ..storage import find_audit_run, list_audit_runs

router = APIRouter(prefix="/audit", tags=["audit"])

# Targets with a capture in flight. Captures hold a database connection for
# their whole duration, so a second concurrent capture against the same
# target is rejected rather than queued.
_active_captures: set[str] = set()


class AuditRunRequest(BaseModel):
    target: Optional[str] = None
    insights: bool = True
    save: bool = True


class AuditRunSummary(BaseModel):
    run_id: str
    target_name: str
    started_at: str
    duration_seconds: int = 0
    total_queries: int = 0
    source: str = ""
    has_analysis: bool = False


class AuditRunListResponse(BaseModel):
    runs: list[AuditRunSummary]
    count: int


@router.post("")
async def run_audit(
    request: AuditRunRequest,
    guard: TargetGuard = Depends(require_target_body),
):
    """Run a metrics-only audit on a target (SSE stream)."""
    service = AuditService()

    async def _generator() -> AsyncGenerator[dict, None]:
        async for event in service.audit_single(
            guard.target_name, insights=request.insights, save=request.save,
        ):
            yield {
                "event": event.type,
                "data": json.dumps(asdict(event), default=str),
            }

    return EventSourceResponse(_generator())


class AuditCaptureRequest(BaseModel):
    target: Optional[str] = None
    duration: int = 60
    source: str = "auto"
    limit: int = 50
    analysis: bool = True
    save: bool = True


@router.post("/capture")
async def run_capture(
    request: AuditCaptureRequest,
    guard: TargetGuard = Depends(require_target_body),
):
    """Capture live workload for a duration (SSE stream of WorkloadEvent).

    Long-lived: the stream stays open for the whole capture window. Client
    disconnect cancels the capture and releases the database connection.
    """
    service = CaptureService()
    duration = max(10, min(request.duration, 3600))
    target_name = guard.target_name

    async def _generator() -> AsyncGenerator[dict, None]:
        if target_name in _active_captures:
            yield {
                "event": "error",
                "data": json.dumps({
                    "message": f"A capture is already running for '{target_name}'.",
                    "phase": "start",
                }),
            }
            return
        _active_captures.add(target_name)
        try:
            async for event in service.run_capture(
                target_name=target_name,
                duration_seconds=duration,
                source=request.source,
                limit=request.limit,
                run_analysis=request.analysis,
                save_capture=request.save,
            ):
                yield {
                    "event": event.type,
                    "data": json.dumps(asdict(event), default=str),
                }
        finally:
            _active_captures.discard(target_name)

    return EventSourceResponse(_generator())


@router.get("/runs")
async def get_audit_runs(
    target: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=200),
) -> AuditRunListResponse:
    """List past audit runs (quick audits and duration captures)."""
    runs = list_audit_runs(target=target, limit=limit)
    summaries = [
        AuditRunSummary(
            run_id=run.get("run_id", ""),
            target_name=run.get("target_name", ""),
            started_at=run.get("started_at", ""),
            duration_seconds=run.get("duration_seconds", 0) or 0,
            total_queries=run.get("total_queries", 0) or 0,
            source=run.get("source", "") or "",
            has_analysis=bool(run.get("has_analysis")),
        )
        for run in runs
    ]
    return AuditRunListResponse(runs=summaries, count=len(summaries))


@router.get("/runs/{run_id}")
async def get_audit_run(
    run_id: str,
    target: Optional[str] = Query(default=None),
) -> dict[str, Any]:
    """Fetch a saved audit run's full payload by ID (exact or prefix)."""
    data = find_audit_run(run_id, target=target)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    return data
