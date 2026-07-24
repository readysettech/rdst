"""Bootstrap API routes.

POST /bootstrap starts a background run via the shared RunRegistry and
returns its run_id immediately; GET .../events streams the run over SSE
with in-process replay (after_seq) so clients can reattach after navigation,
reload, or a dropped connection while RDST remains open. Frames carry id=seq
for Last-Event-ID semantics.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from shared.api.target_guard import TargetGuard, require_target_body
from shared.run_registry import RunRegistry, run_registry

from ..service import BootstrapOptions, TargetBootstrapService

router = APIRouter(prefix="/bootstrap", tags=["bootstrap"])

# One registry for the process; tests replace it with an isolated instance.
_registry = run_registry


class BootstrapStartRequest(BaseModel):
    target: str | None = None
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
    options = BootstrapOptions(annotate=request.annotate)
    service = TargetBootstrapService()
    key_wakeup = asyncio.Event()
    gen = service.run(
        guard.target_name, guard.target_config, options, key_wakeup=key_wakeup
    )
    run_id = _registry.start(
        "bootstrap", guard.target_name, gen, resume_event=key_wakeup
    )
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
    async for record in registry.events(run_id, after_seq=after_seq):
        yield _record_to_sse(record)
