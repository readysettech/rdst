"""Bootstrap API routes.

POST /bootstrap starts a background run via the shared RunRegistry and
returns its run_id immediately; GET .../events streams the run over SSE
with replay (after_seq) so clients can reattach after navigation, reload,
or a dropped connection. Frames carry id=seq for Last-Event-ID semantics.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from shared.api.target_guard import TargetGuard, require_target_body
from shared.run_registry import RUN_END_EVENT, RunRegistry

from ..service import BootstrapOptions, TargetBootstrapService

router = APIRouter(prefix="/bootstrap", tags=["bootstrap"])

# One registry for the process; tests swap it for a tmp-dir instance.
_registry = RunRegistry()


class BootstrapStartRequest(BaseModel):
    target: str | None = None
    deploy: bool = True
    deploy_mode: str = "docker"
    annotate: bool = True


class BootstrapStartResponse(BaseModel):
    run_id: str


class BootstrapRunStatusResponse(BaseModel):
    run_id: str
    status: str


@router.post("", response_model=BootstrapStartResponse)
async def start_bootstrap(
    request: BootstrapStartRequest,
    guard: TargetGuard = Depends(require_target_body),
) -> BootstrapStartResponse:
    """Start the add-database bootstrap for a target; returns immediately."""
    options = BootstrapOptions(
        deploy=request.deploy,
        deploy_mode=request.deploy_mode,
        annotate=request.annotate,
    )
    service = TargetBootstrapService()
    gen = service.run(guard.target_name, guard.target_config, options)
    run_id = _registry.start("bootstrap", guard.target_name, gen)
    return BootstrapStartResponse(run_id=run_id)


@router.get("/runs/{run_id}", response_model=BootstrapRunStatusResponse)
async def bootstrap_run_status(run_id: str) -> BootstrapRunStatusResponse:
    """Cheap status poll for a run."""
    status = _registry.status(run_id)
    if status is None:
        raise HTTPException(
            status_code=404, detail=f"Bootstrap run '{run_id}' not found"
        )
    return BootstrapRunStatusResponse(run_id=run_id, status=status)


@router.get("/runs/{run_id}/events")
async def bootstrap_run_events(run_id: str, after_seq: int = 0):
    """Replay events with seq > after_seq, then stream live until terminal."""
    if _registry.status(run_id) is None:
        raise HTTPException(
            status_code=404, detail=f"Bootstrap run '{run_id}' not found"
        )
    return EventSourceResponse(_sse_frames(_registry, run_id, after_seq))


def _record_to_sse(record: dict) -> dict:
    """Map a registry record to an SSE frame; id=seq enables reattach."""
    return {
        "event": record["event"],
        "data": json.dumps({**record["data"], "seq": record["seq"]}, default=str),
        "id": str(record["seq"]),
    }


async def _sse_frames(registry: RunRegistry, run_id: str, after_seq: int):
    saw_terminal = False
    async for record in registry.events(run_id, after_seq=after_seq):
        saw_terminal = saw_terminal or record["event"] == RUN_END_EVENT
        yield _record_to_sse(record)
    # An interrupted run's log has no run_end; synthesize the terminal frame
    # so clients can settle instead of treating it as a dropped connection.
    if not saw_terminal and registry.status(run_id) == "interrupted":
        yield {
            "event": RUN_END_EVENT,
            "data": json.dumps({"status": "interrupted"}),
        }
