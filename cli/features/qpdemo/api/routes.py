"""/api/demo — the web-first QueryPilot demo router.

Streaming endpoints (provision, load) use SSE; the rest are plain JSON. All state
lives in the process-wide DemoService (RDST Web is a local single-user server).
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from features.qpdemo.service import DemoService
from shared.api.guards import require_local_request

router = APIRouter(prefix="/demo", tags=["demo"])


def _svc() -> DemoService:
    return DemoService.instance()


def _sse(gen):
    for event in gen:
        yield {"event": event.get("type", "message"), "data": json.dumps(event)}


class WorkersBody(BaseModel):
    workers: int | None = None


class FingerprintBody(BaseModel):
    fingerprint: str


class QueryPilotBody(BaseModel):
    enabled: bool


class DiscoveryModeBody(BaseModel):
    mode: str


class SettingsBody(BaseModel):
    cache_budget: int


@router.get("/preflight")
def preflight(request: Request):
    require_local_request(request)
    return _svc().preflight()


@router.post("/provision")
def provision(request: Request):
    require_local_request(request)
    return EventSourceResponse(_sse(_svc().provision()))


@router.post("/teardown")
def teardown(request: Request):
    require_local_request(request)
    return _svc().teardown()


@router.get("/status")
def status(request: Request):
    require_local_request(request)
    return _svc().status()


@router.get("/tour")
def tour(request: Request):
    require_local_request(request)
    return {"done": _svc().tour_done()}


@router.post("/tour-done")
def tour_done(request: Request):
    require_local_request(request)
    _svc().mark_tour_done()
    return {"done": True}


@router.get("/workload")
def workload(request: Request):
    require_local_request(request)
    return {"queries": _svc().workload()}


@router.post("/load/start")
def load_start(request: Request, body: WorkersBody | None = None):
    require_local_request(request)
    _svc().start_load(body.workers if body else None)
    return {"success": True, "running": True}


@router.post("/load/stop")
def load_stop(request: Request):
    require_local_request(request)
    _svc().stop_load()
    return {"success": True, "running": False}


@router.patch("/load")
def load_intensity(request: Request, body: WorkersBody):
    require_local_request(request)
    if body.workers:
        _svc().set_intensity(body.workers)
    return {"success": True, "workers": body.workers}


@router.get("/load/stream")
def load_stream(request: Request):
    require_local_request(request)

    def gen():
        for w in _svc().load_stream():
            yield {"event": "window", "data": json.dumps(w)}
    return EventSourceResponse(gen())


@router.get("/load/history")
def load_history(request: Request):
    require_local_request(request)
    return _svc().load_history()


@router.get("/patterns")
def patterns(request: Request):
    require_local_request(request)
    return {"patterns": _svc().patterns()}


@router.post("/cache")
def cache(request: Request, body: FingerprintBody):
    require_local_request(request)
    try:
        return _svc().cache(body.fingerprint)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/uncache")
def uncache(request: Request, body: FingerprintBody):
    require_local_request(request)
    try:
        return _svc().uncache(body.fingerprint)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/querypilot")
def querypilot(request: Request, body: QueryPilotBody):
    require_local_request(request)
    return _svc().set_querypilot(body.enabled)


@router.patch("/discovery-mode")
def discovery_mode(request: Request, body: DiscoveryModeBody):
    require_local_request(request)
    try:
        return _svc().set_discovery_mode(body.mode)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.patch("/settings")
def settings(request: Request, body: SettingsBody):
    require_local_request(request)
    try:
        return _svc().set_settings(body.cache_budget)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
