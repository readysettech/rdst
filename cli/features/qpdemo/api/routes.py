"""/api/demo — the web-first QueryPilot demo router.

Streaming endpoints (provision, load) use SSE; the rest are plain JSON. All state
lives in the process-wide DemoService (RDST Web is a local single-user server).
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from features.qpdemo.service import DemoService

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
def preflight():
    return _svc().preflight()


@router.post("/provision")
def provision():
    return EventSourceResponse(_sse(_svc().provision()))


@router.post("/teardown")
def teardown():
    return _svc().teardown()


@router.get("/status")
def status():
    return _svc().status()


@router.get("/workload")
def workload():
    return {"queries": _svc().workload()}


@router.post("/load/start")
def load_start(body: WorkersBody | None = None):
    _svc().start_load(body.workers if body else None)
    return {"success": True, "running": True}


@router.post("/load/stop")
def load_stop():
    _svc().stop_load()
    return {"success": True, "running": False}


@router.patch("/load")
def load_intensity(body: WorkersBody):
    if body.workers:
        _svc().set_intensity(body.workers)
    return {"success": True, "workers": body.workers}


@router.get("/load/stream")
def load_stream():
    def gen():
        for w in _svc().load_stream():
            yield {"event": "window", "data": json.dumps(w)}
    return EventSourceResponse(gen())


@router.get("/load/history")
def load_history():
    return _svc().load_history()


@router.get("/patterns")
def patterns():
    return {"patterns": _svc().patterns()}


@router.post("/cache")
def cache(body: FingerprintBody):
    try:
        return _svc().cache(body.fingerprint)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/uncache")
def uncache(body: FingerprintBody):
    try:
        return _svc().uncache(body.fingerprint)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/querypilot")
def querypilot(body: QueryPilotBody):
    return _svc().set_querypilot(body.enabled)


@router.patch("/discovery-mode")
def discovery_mode(body: DiscoveryModeBody):
    try:
        return _svc().set_discovery_mode(body.mode)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.patch("/settings")
def settings(body: SettingsBody):
    try:
        return _svc().set_settings(body.cache_budget)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
