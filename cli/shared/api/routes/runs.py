"""Generic process-local background-run status, event, and cancel routes."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from shared.run_registry import RunRegistry, run_registry

router = APIRouter(prefix="/runs", tags=["runs"])

# Tests replace this with an isolated registry instance.
_registry = run_registry


class BackgroundRunResponse(BaseModel):
    run_id: str
    kind: str
    target: str
    status: str
    last_seq: int
    metadata: dict[str, Any] = Field(default_factory=dict)


class BackgroundRunCancelResponse(BaseModel):
    run_id: str
    cancelled: bool


@router.get("/{run_id}", response_model=BackgroundRunResponse)
async def run_status(run_id: str) -> BackgroundRunResponse:
    description = _registry.describe(run_id)
    if description is None:
        raise HTTPException(status_code=404, detail=f"Background run '{run_id}' not found")
    return BackgroundRunResponse(**description)


@router.get("/{run_id}/events")
async def run_events(run_id: str, after_seq: int = 0):
    if _registry.status(run_id) is None:
        raise HTTPException(status_code=404, detail=f"Background run '{run_id}' not found")
    return EventSourceResponse(_sse_frames(_registry, run_id, after_seq))


@router.delete("/{run_id}", response_model=BackgroundRunCancelResponse)
async def cancel_run(run_id: str) -> BackgroundRunCancelResponse:
    if _registry.status(run_id) is None:
        raise HTTPException(status_code=404, detail=f"Background run '{run_id}' not found")
    return BackgroundRunCancelResponse(
        run_id=run_id,
        cancelled=_registry.cancel(run_id),
    )


def _record_to_sse(record: dict) -> dict:
    return {
        "event": record["event"],
        "data": json.dumps({**record["data"], "seq": record["seq"]}, default=str),
        "id": str(record["seq"]),
    }


async def _sse_frames(registry: RunRegistry, run_id: str, after_seq: int):
    async for record in registry.events(run_id, after_seq=after_seq):
        yield _record_to_sse(record)
