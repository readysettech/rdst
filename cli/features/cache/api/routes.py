"""Temporary local Readyset sandbox API routes.

RDST owns one local sandbox for demo comparisons. This API intentionally does
not expose deployment or durable cache-management operations.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from features.cache.performance_comparison import (
    MAX_COMPARISON_ITERATIONS,
    MAX_COMPARISON_WARMUP,
    MIN_COMPARISON_ITERATIONS,
    MIN_COMPARISON_WARMUP,
)
from shared.api.target_guard import TargetGuard, require_target_body
from shared.db_connection import probe_target_connection
from shared.run_registry import run_registry

router = APIRouter(prefix="/cache", tags=["cache"])


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
async def get_sandbox_status() -> SandboxStatusResponse:
    """Return process-local sandbox diagnostics without connection secrets."""
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
    request: SandboxPrewarmRequest,
    guard: TargetGuard = Depends(require_target_body),
) -> SandboxPrewarmResponse:
    """Queue low-priority preparation for the requested Comparisons target."""
    from shared.deploy.sandbox_manager import sandbox_manager

    await _require_readyset_runtime()
    await _require_healthy_upstream(guard)
    sandbox_manager.request_prewarm(guard.target_name)
    return SandboxPrewarmResponse(queued=True)


@router.post("/test-runs", response_model=CacheTestRunStartResponse)
async def start_cache_test_run(
    request: CacheTestRunRequest,
    guard: TargetGuard = Depends(require_target_body),
) -> CacheTestRunStartResponse:
    """Start or attach to one complete temporary Readyset speed experiment."""
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
