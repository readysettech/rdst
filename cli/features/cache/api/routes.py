"""Temporary local Readyset sandbox API routes.

RDST owns one local sandbox for demo comparisons. This API intentionally does
not expose deployment or durable cache-management operations.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from features.cache.live_comparison import (
    MAX_COMPARE_CONCURRENCY_PER_LANE,
    MAX_COMPARE_DURATION_SECONDS,
    MIN_COMPARE_CONCURRENCY_PER_LANE,
    MIN_COMPARE_DURATION_SECONDS,
    LiveComparisonController,
)
from features.cache.performance_comparison import (
    MAX_COMPARISON_ITERATIONS,
    MAX_COMPARISON_WARMUP,
    MIN_COMPARISON_ITERATIONS,
    MIN_COMPARISON_WARMUP,
)
from shared.api.guards import require_local_request
from shared.api.target_guard import TargetGuard, require_target_body
from shared.db_connection import probe_target_connection
from shared.run_registry import run_registry

router = APIRouter(prefix="/cache", tags=["cache"])
compare_router = APIRouter(prefix="/cache", tags=["cache"])


class CacheRunRequest(BaseModel):
    query: str
    target: Optional[str] = None
    iterations: int = Field(
        default=15,
        ge=MIN_COMPARISON_ITERATIONS,
        le=MAX_COMPARISON_ITERATIONS,
    )
    warmup: int = Field(
        default=5,
        ge=MIN_COMPARISON_WARMUP,
        le=MAX_COMPARISON_WARMUP,
    )


class CacheTestRunRequest(CacheRunRequest):
    query_hash: Optional[str] = None
    label: Optional[str] = None


class CacheTestRunStartResponse(BaseModel):
    run_id: str


class CacheCompareRunRequest(BaseModel):
    query: str
    target: Optional[str] = None
    query_hash: Optional[str] = None
    label: Optional[str] = None
    concurrency: int = Field(
        default=4,
        ge=MIN_COMPARE_CONCURRENCY_PER_LANE,
        le=MAX_COMPARE_CONCURRENCY_PER_LANE,
    )
    duration_seconds: int = Field(
        default=30,
        ge=MIN_COMPARE_DURATION_SECONDS,
        le=MAX_COMPARE_DURATION_SECONDS,
    )


class CacheCompareLoadRequest(BaseModel):
    concurrency: int = Field(
        ge=MIN_COMPARE_CONCURRENCY_PER_LANE,
        le=MAX_COMPARE_CONCURRENCY_PER_LANE,
    )


class CacheCompareLoadResponse(BaseModel):
    run_id: str
    concurrency: int


class SandboxStatusResponse(BaseModel):
    phase: str
    current_target: Optional[str] = None
    generation: int
    lease_owner: Optional[str] = None
    lease_purpose: Optional[str] = None
    queued_requests: int
    dirty_reason: Optional[str] = None
    failed_target: Optional[str] = None
    last_error: Optional[str] = None
    last_released_at: Optional[str] = None
    expires_at: Optional[str] = None
    container_name: str
    healthy: bool
    docker_installed: bool
    docker_running: bool


class SandboxPrewarmRequest(BaseModel):
    target: Optional[str] = None


class SandboxPrewarmResponse(BaseModel):
    queued: bool


# Tests replace this with an isolated process-local registry.
_run_registry = run_registry
_compare_controllers: dict[str, LiveComparisonController] = {}


def _prune_compare_controllers() -> None:
    terminal = (None, "done", "partial", "failed", "cancelled")
    for run_id in list(_compare_controllers):
        if _run_registry.status(run_id) in terminal:
            _compare_controllers.pop(run_id, None)


async def _probe_upstream(target_config: dict) -> dict:
    return await asyncio.to_thread(
        probe_target_connection,
        target_config,
        connect_timeout=3,
    )


async def _docker_runtime_status() -> dict:
    from shared.deploy.local_docker import docker_runtime_status

    return await asyncio.to_thread(docker_runtime_status)


async def _require_readyset_runtime() -> None:
    runtime = await _docker_runtime_status()
    if not runtime.get("installed"):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "docker_not_installed",
                "message": (
                    "Docker is required only for local Readyset comparisons. "
                    "Install Docker, then try again."
                ),
            },
        )
    if not runtime.get("running"):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "docker_not_running",
                "message": (
                    "Docker is installed but is not running. Start Docker, "
                    "then try the Readyset comparison again."
                ),
            },
        )


async def _require_healthy_upstream(guard: TargetGuard) -> None:
    """Reject Readyset work before it enters the lifecycle queue."""
    state = await _probe_upstream(guard.target_config)
    if state.get("success"):
        return
    raise HTTPException(
        status_code=503,
        detail={
            "code": "upstream_unavailable",
            "message": (
                "Readyset comparisons require a reachable source database. "
                f"RDST could not connect to '{guard.target_name}', so no "
                "Readyset work was queued."
            ),
            "detail": state.get("error"),
        },
    )


@router.get("/sandbox", response_model=SandboxStatusResponse)
async def get_sandbox_status(http_request: Request) -> SandboxStatusResponse:
    """Return process-local sandbox diagnostics without connection secrets."""
    require_local_request(http_request)

    from shared.deploy.sandbox_manager import sandbox_manager

    diagnostics, runtime = await asyncio.gather(
        sandbox_manager.diagnostics(),
        _docker_runtime_status(),
    )
    return SandboxStatusResponse(
        **diagnostics,
        docker_installed=bool(runtime.get("installed")),
        docker_running=bool(runtime.get("running")),
    )


@router.post("/sandbox/prewarm", response_model=SandboxPrewarmResponse)
async def prewarm_sandbox(
    http_request: Request,
    request: SandboxPrewarmRequest,
    guard: TargetGuard = Depends(require_target_body),
) -> SandboxPrewarmResponse:
    """Queue low-priority preparation for the requested Comparisons target."""
    require_local_request(http_request)

    from shared.deploy.sandbox_manager import sandbox_manager

    await _require_readyset_runtime()
    await _require_healthy_upstream(guard)
    sandbox_manager.request_prewarm(guard.target_name)
    return SandboxPrewarmResponse(queued=True)


@router.post("/test-runs", response_model=CacheTestRunStartResponse)
async def start_cache_test_run(
    http_request: Request,
    request: CacheTestRunRequest,
    guard: TargetGuard = Depends(require_target_body),
) -> CacheTestRunStartResponse:
    """Start or attach to one complete temporary Readyset speed experiment."""
    require_local_request(http_request)

    from ..experiment_service import (
        ReadysetExperimentService,
        parameter_fingerprint,
    )

    await _require_readyset_runtime()
    await _require_healthy_upstream(guard)
    metadata = {
        "query_hash": request.query_hash
        or hashlib.sha256(request.query.encode()).hexdigest()[:16],
        "label": request.label,
        "parameter_fingerprint": parameter_fingerprint(request.query),
        "iterations": request.iterations,
        "warmup": request.warmup,
    }
    existing = _run_registry.find_active_matching(
        "speed_test",
        guard.target_name,
        metadata,
        keys=(
            "query_hash",
            "parameter_fingerprint",
            "iterations",
            "warmup",
        ),
    )
    if existing is not None:
        return CacheTestRunStartResponse(run_id=existing)

    service = ReadysetExperimentService()
    run_id = _run_registry.start_factory(
        "speed_test",
        guard.target_name,
        lambda owner_id: service.compare(
            owner_id=owner_id,
            target=guard.target_name,
            query=request.query,
            iterations=request.iterations,
            warmup=request.warmup,
        ),
        metadata=metadata,
    )
    return CacheTestRunStartResponse(run_id=run_id)


@compare_router.post("/compare-runs", response_model=CacheTestRunStartResponse)
async def start_cache_compare_run(
    request: CacheCompareRunRequest,
    guard: TargetGuard = Depends(require_target_body),
) -> CacheTestRunStartResponse:
    """Start an adjustable, equal-concurrency comparison in the same sandbox."""
    from ..experiment_service import ReadysetExperimentService, parameter_fingerprint

    await _require_readyset_runtime()
    await _require_healthy_upstream(guard)
    _prune_compare_controllers()
    controller = LiveComparisonController(request.concurrency)
    service = ReadysetExperimentService()
    metadata = {
        "query_hash": request.query_hash
        or hashlib.sha256(request.query.encode()).hexdigest()[:16],
        "label": request.label,
        "parameter_fingerprint": parameter_fingerprint(request.query),
        "concurrency": request.concurrency,
        "duration_seconds": request.duration_seconds,
    }
    run_id = _run_registry.start_factory(
        "cache_compare",
        guard.target_name,
        lambda owner_id: service.compare_live(
            owner_id=owner_id,
            target=guard.target_name,
            query=request.query,
            duration_seconds=request.duration_seconds,
            controller=controller,
        ),
        metadata=metadata,
    )
    _compare_controllers[run_id] = controller
    return CacheTestRunStartResponse(run_id=run_id)


@compare_router.patch(
    "/compare-runs/{run_id}/load", response_model=CacheCompareLoadResponse
)
async def update_cache_compare_load(
    run_id: str, request: CacheCompareLoadRequest
) -> CacheCompareLoadResponse:
    """Adjust the in-flight clients for an active live comparison."""
    _prune_compare_controllers()
    run = _run_registry.describe(run_id)
    controller = _compare_controllers.get(run_id)
    if run is None or run.get("kind") != "cache_compare":
        raise HTTPException(status_code=404, detail="Comparison run not found")
    if run.get("status") != "running" or controller is None:
        raise HTTPException(
            status_code=409, detail="Comparison is no longer running"
        )
    controller.set_concurrency(request.concurrency)
    return CacheCompareLoadResponse(
        run_id=run_id, concurrency=controller.concurrency
    )
