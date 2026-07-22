"""Cache management API routes.

All endpoints accept the DATABASE target name. The backend resolves
the corresponding cache target internally via CacheService.
"""

from __future__ import annotations

import json
from typing import AsyncGenerator, Optional, Union

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from shared.api.target_guard import TargetGuard, require_target, require_target_body
from shared.run_registry import run_registry
from shared.service_events import ErrorEvent, ProgressEvent

from ..events import (
    CacheAddEvent,
    CacheDeleteEvent,
    CacheDeployCompleteEvent,
    CacheDropAllEvent,
    CacheEvent,
    CacheLifecycleEvent,
    CacheListEvent,
    CacheRunCompleteEvent,
    CacheStatusEvent,
)
from ..models import CacheInput, CacheOptions
from ..service import CacheService

router = APIRouter(prefix="/cache", tags=["cache"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class CacheDeployRequest(BaseModel):
    target: Optional[str] = None
    mode: str = "docker"
    port: Optional[int] = None
    namespace: Optional[str] = None
    host: Optional[str] = None
    ssh_user: Optional[str] = None


class CacheAddRequest(BaseModel):
    query: str
    target: Optional[str] = None
    tag: Optional[str] = None
    dry_run: bool = False


class CacheRegisterRequest(BaseModel):
    target: Optional[str] = None
    cache_host: str
    cache_port: int = 5433


class CacheLifecycleRequest(BaseModel):
    target: Optional[str] = None


class CacheRunRequest(BaseModel):
    query: str
    target: Optional[str] = None
    iterations: int = 15
    warmup: int = 5


class CacheTestRunRequest(CacheRunRequest):
    query_hash: Optional[str] = None
    label: Optional[str] = None


class CacheTestRunStartResponse(BaseModel):
    run_id: str


# Shared process-local registry; tests replace it with an isolated instance.
_run_registry = run_registry


# Response shapes returned by non-SSE endpoints. These mirror `_event_to_dict`
# below — FastAPI infers the OpenAPI schema from the `->` return annotation on
# each route and validates the response dict against it.

class CacheErrorResponse(BaseModel):
    success: bool
    error: str


class CacheStatusResponse(BaseModel):
    deployed: bool
    running: bool
    endpoint: Optional[str] = None
    cache_target: Optional[str] = None
    container_name: Optional[str] = None


class CacheEntryResponse(BaseModel):
    cache_id: str
    cache_name: str
    query: str
    type: str
    ttl: str
    registry_hash: Optional[str] = None


class CacheListResponse(BaseModel):
    success: bool
    caches: list[CacheEntryResponse]
    count: int


class CacheAddResponse(BaseModel):
    success: bool
    supported: bool
    query: str
    query_hash: Optional[str] = None
    detail: Optional[str] = None


class CacheDeleteResponse(BaseModel):
    success: bool
    cache_id: str


class CacheDropAllResponse(BaseModel):
    success: bool
    count: int


class CacheLifecycleResponse(BaseModel):
    success: bool
    operation: str
    state: Optional[str] = None
    detail: Optional[str] = None


# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------


def _event_to_sse(event: CacheEvent) -> dict:
    """Convert a typed cache event to an SSE dict."""
    if isinstance(event, ProgressEvent):
        return {
            "event": "progress",
            "data": json.dumps({
                "stage": event.stage,
                "percent": event.percent,
                "message": event.message,
            }),
        }
    if isinstance(event, CacheDeployCompleteEvent):
        return {
            "event": "complete",
            "data": json.dumps({
                "deployed": event.deployed,
                "running": event.running,
                "endpoint": event.endpoint,
                "cache_target": event.cache_target,
                "container_name": event.container_name,
            }),
        }
    if isinstance(event, CacheRunCompleteEvent):
        return {
            "event": "complete",
            "data": json.dumps({
                "success": event.success,
                "query": event.query,
                "iterations": event.iterations,
                "origin_stats": event.origin_stats,
                "cache_stats": event.cache_stats,
                "speedup_mean": event.speedup_mean,
                "speedup_median": event.speedup_median,
                "improvement_pct": event.improvement_pct,
                "winner": event.winner,
            }),
        }
    if isinstance(event, ErrorEvent):
        # Shared {code, message, detail} envelope (B7/T24).
        error_data: dict = {
            "code": event.code,
            "message": event.message,
            "detail": event.detail,
        }
        if event.stage:
            error_data["stage"] = event.stage
        return {
            "event": "error",
            "data": json.dumps(error_data),
        }
    # Fallback
    return {
        "event": "unknown",
        "data": json.dumps({"type": getattr(event, "type", "unknown")}),
    }


# ---------------------------------------------------------------------------
# Helper: collect final event from async generator
# ---------------------------------------------------------------------------


async def _collect_final(gen: AsyncGenerator) -> CacheEvent:
    """Iterate an async generator and return the last event."""
    last = None
    async for event in gen:
        last = event
    return last


def _event_to_dict(event: CacheEvent) -> dict:
    """Convert a typed event to a JSON-serializable dict."""
    if isinstance(event, CacheStatusEvent):
        return {
            "deployed": event.deployed,
            "running": event.running,
            "endpoint": event.endpoint,
            "cache_target": event.cache_target,
            "container_name": event.container_name,
        }
    if isinstance(event, CacheListEvent):
        return {
            "success": event.success,
            "caches": event.caches,
            "count": event.count,
        }
    if isinstance(event, CacheAddEvent):
        return {
            "success": event.success,
            "supported": event.supported,
            "query": event.query,
            "query_hash": event.query_hash,
            "detail": event.detail,
        }
    if isinstance(event, CacheDeleteEvent):
        return {
            "success": event.success,
            "cache_id": event.cache_id,
        }
    if isinstance(event, CacheDropAllEvent):
        return {
            "success": event.success,
            "count": event.count,
        }
    if isinstance(event, CacheLifecycleEvent):
        return {
            "success": event.success,
            "operation": event.operation,
            "state": event.state,
            "detail": event.detail,
        }
    if isinstance(event, ErrorEvent):
        return {
            "success": False,
            "error": event.message,
        }
    return {"success": False, "error": "Unknown event"}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/status")
async def get_cache_status(
    guard: TargetGuard = Depends(require_target),
) -> Union[CacheStatusResponse, CacheErrorResponse]:
    """Check cache deployment status for a database target."""
    service = CacheService()
    event = await _collect_final(
        service.get_status(CacheInput(target=guard.target_name))
    )
    return _event_to_dict(event)


@router.post("/deploy")
async def deploy_cache(
    request: CacheDeployRequest,
    guard: TargetGuard = Depends(require_target_body),
):
    """Deploy Readyset cache (SSE stream)."""
    service = CacheService()
    input_data = CacheInput(target=guard.target_name)
    options = CacheOptions(
        mode=request.mode, port=request.port,
        namespace=request.namespace,
        host=request.host, ssh_user=request.ssh_user,
    )

    async def _generator() -> AsyncGenerator[dict, None]:
        async for event in service.deploy(input_data, options):
            yield _event_to_sse(event)

    return EventSourceResponse(_generator())


@router.get("/list")
async def list_caches(
    guard: TargetGuard = Depends(require_target),
) -> Union[CacheListResponse, CacheErrorResponse]:
    """List cached queries for a database target."""
    service = CacheService()
    event = await _collect_final(
        service.list_caches(CacheInput(target=guard.target_name))
    )
    return _event_to_dict(event)


@router.post("/add")
async def add_cache(
    request: CacheAddRequest,
    guard: TargetGuard = Depends(require_target_body),
) -> Union[CacheAddResponse, CacheErrorResponse]:
    """Create a cache or dry-run check."""
    service = CacheService()
    input_data = CacheInput(
        target=guard.target_name,
        query=request.query,
        tag=request.tag,
    )
    options = CacheOptions(dry_run=request.dry_run)

    event = await _collect_final(
        service.add_cache(input_data, options)
    )
    return _event_to_dict(event)


@router.post("/register")
async def register_cache_target(
    request: CacheRegisterRequest,
    guard: TargetGuard = Depends(require_target_body),
) -> Union[CacheStatusResponse, CacheErrorResponse]:
    """Register a cache target with a user-provided endpoint (for non-local deploys)."""
    service = CacheService()
    event = await _collect_final(
        service.register_cache_endpoint(
            CacheInput(target=guard.target_name),
            host=request.cache_host,
            port=request.cache_port,
        )
    )
    return _event_to_dict(event)


@router.delete("/remove")
async def remove_cache(
    guard: TargetGuard = Depends(require_target),
) -> Union[CacheDeleteResponse, CacheErrorResponse]:
    """Remove cache target and stop container."""
    service = CacheService()
    event = await _collect_final(
        service.remove_cache_target(CacheInput(target=guard.target_name))
    )
    return _event_to_dict(event)


@router.delete("/drop-all")
async def drop_all_caches(
    guard: TargetGuard = Depends(require_target),
) -> Union[CacheDropAllResponse, CacheErrorResponse]:
    """Delete all caches."""
    service = CacheService()
    event = await _collect_final(
        service.drop_all(CacheInput(target=guard.target_name))
    )
    return _event_to_dict(event)


@router.post("/start")
async def start_cache(
    request: CacheLifecycleRequest,
    guard: TargetGuard = Depends(require_target_body),
) -> Union[CacheLifecycleResponse, CacheErrorResponse]:
    """Start a stopped cache without redeploying."""
    return await _lifecycle_endpoint(guard.target_name, "start")


@router.post("/stop")
async def stop_cache(
    request: CacheLifecycleRequest,
    guard: TargetGuard = Depends(require_target_body),
) -> Union[CacheLifecycleResponse, CacheErrorResponse]:
    """Stop a running cache without removing it."""
    return await _lifecycle_endpoint(guard.target_name, "stop")


@router.post("/restart")
async def restart_cache(
    request: CacheLifecycleRequest,
    guard: TargetGuard = Depends(require_target_body),
) -> Union[CacheLifecycleResponse, CacheErrorResponse]:
    """Restart a deployed cache, preserving its config."""
    return await _lifecycle_endpoint(guard.target_name, "restart")


async def _lifecycle_endpoint(target_name: str, operation: str) -> dict:
    service = CacheService()
    event = await _collect_final(
        service.lifecycle(CacheInput(target=target_name), operation)
    )
    return _event_to_dict(event)


@router.post("/run")
async def run_cache_comparison(
    request: CacheRunRequest,
    guard: TargetGuard = Depends(require_target_body),
):
    """Run a query against both origin DB and Readyset cache, stream comparison results (SSE)."""
    service = CacheService()
    input_data = CacheInput(target=guard.target_name, query=request.query)

    async def _generator() -> AsyncGenerator[dict, None]:
        async for event in service.run_comparison(
            input_data, iterations=request.iterations, warmup=request.warmup,
        ):
            yield _event_to_sse(event)

    return EventSourceResponse(_generator())


@router.post("/test-runs", response_model=CacheTestRunStartResponse)
async def start_cache_test_run(
    request: CacheTestRunRequest,
    guard: TargetGuard = Depends(require_target_body),
) -> CacheTestRunStartResponse:
    """Start a detached origin-vs-cache benchmark and return immediately."""
    service = CacheService()
    generator = service.run_comparison(
        CacheInput(target=guard.target_name, query=request.query),
        iterations=request.iterations,
        warmup=request.warmup,
    )
    run_id = _run_registry.start(
        "cache_test",
        guard.target_name,
        generator,
        metadata={
            "query_hash": request.query_hash,
            "label": request.label,
        },
    )
    return CacheTestRunStartResponse(run_id=run_id)


@router.delete("/{cache_id}")
async def delete_cache(
    cache_id: str,
    guard: TargetGuard = Depends(require_target),
) -> Union[CacheDeleteResponse, CacheErrorResponse]:
    """Delete a single cache by ID."""
    service = CacheService()
    event = await _collect_final(
        service.delete_cache(CacheInput(target=guard.target_name, cache_id=cache_id))
    )
    return _event_to_dict(event)
