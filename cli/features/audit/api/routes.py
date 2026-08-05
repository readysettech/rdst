"""Audit API routes.

`POST /audit` and `POST /audit/capture` schedule detached background runs
whose events are read back through `/api/runs/{run_id}/events`, so a reload
or a closed tab does not kill the work. The run history endpoints read the
same on-disk stores the CLI's `audit list` / `audit show` use, so runs
started from either surface show up in both.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from shared.api.target_guard import TargetGuard, require_target, require_target_body
from shared.run_registry import AUDIT_RUN_KINDS, run_registry

from ..capture_service import CaptureService
from ..report.delivery import RunEmailRequest, RunEmailResponse, deliver_run_report
from ..service import AuditService
from ..storage import find_audit_run, list_audit_runs

router = APIRouter(prefix="/audit", tags=["audit"])

# Tests replace this with an isolated registry instance.
_registry = run_registry


class AuditRunRequest(BaseModel):
    target: Optional[str] = None
    insights: bool = True
    save: bool = True


class AuditRunStartResponse(BaseModel):
    run_id: str
    # True when an audit was already in flight and the caller was handed that
    # run instead of starting a second one.
    reused: bool = False


class AuditRunSummary(BaseModel):
    run_id: str
    target_name: str
    engine: str = ""
    started_at: str
    duration_seconds: int = 0
    total_queries: int = 0
    source: str = ""
    has_analysis: bool = False


class AuditRunListResponse(BaseModel):
    runs: list[AuditRunSummary]
    count: int


@router.post("", response_model=AuditRunStartResponse)
async def run_audit(
    request: AuditRunRequest,
    guard: TargetGuard = Depends(require_target_body),
) -> AuditRunStartResponse:
    """Start a metrics-only audit as a detached background run."""
    existing = _registry.find_active(AUDIT_RUN_KINDS)
    if existing is not None:
        return AuditRunStartResponse(run_id=existing, reused=True)

    service = AuditService()
    generator = service.audit_single(
        guard.target_name, insights=request.insights, save=request.save,
    )
    run_id = _registry.start("audit", guard.target_name, generator)
    return AuditRunStartResponse(run_id=run_id)


class AuditCaptureRequest(BaseModel):
    target: Optional[str] = None
    duration: int = 60
    source: str = "auto"
    limit: int = 50
    analysis: bool = True
    save: bool = True
    # Benchmark captured queries through the managed Readyset sandbox; the
    # comparison lands in the complete event's summary.
    readyset: bool = False


@router.post("/capture", response_model=AuditRunStartResponse)
async def run_capture(
    request: AuditCaptureRequest,
    guard: TargetGuard = Depends(require_target_body),
) -> AuditRunStartResponse:
    """Start a live workload capture as a detached background run.

    The capture holds a database connection for the whole window; cancelling
    the run through `DELETE /api/runs/{run_id}` releases it.
    """
    existing = _registry.find_active(AUDIT_RUN_KINDS)
    if existing is not None:
        return AuditRunStartResponse(run_id=existing, reused=True)

    service = CaptureService()
    generator = service.run_capture(
        target_name=guard.target_name,
        duration_seconds=max(10, min(request.duration, 3600)),
        source=request.source,
        limit=request.limit,
        run_analysis=request.analysis,
        collect_metrics_audit=True,
        save_capture=request.save,
        readyset_testing=request.readyset,
    )
    run_id = _registry.start("audit_capture", guard.target_name, generator)
    return AuditRunStartResponse(run_id=run_id)


class AuditRequirementsResponse(BaseModel):
    target: str
    engine: str
    query_stats: str
    detail: str = ""
    remediation: Optional[str] = None
    docker_available: bool = False
    category: Optional[str] = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )


@router.get("/requirements")
async def get_capture_requirements(
    guard: TargetGuard = Depends(require_target),
) -> AuditRequirementsResponse:
    """Check capture prerequisites for a target.

    Reports whether pg_stat_statements (PG) / performance_schema (MySQL)
    is available, with setup instructions when it is not. Connection
    failures surface as query_stats "error".
    """

    def _check() -> dict[str, Any]:
        from shared.db_connection import (
            create_direct_connection,
            resolve_connection_params,
        )

        target_config = guard.target_config
        ssh_config = target_config.get("ssh")
        tunnel_manager = None
        try:
            if ssh_config:
                from shared.ssh_tunnel import get_tunnel_manager

                tunnel_manager = get_tunnel_manager()
                # Preflight is an explicit connection test. Freshness belongs
                # to the target's locked ensure operation so close/reopen cannot
                # interleave with another resolver.
                params = resolve_connection_params(
                    target=guard.target_name,
                    target_config=target_config,
                    force_fresh_tunnel=True,
                )
                target_config = dict(target_config)
                target_config.pop("ssh", None)
                target_config.update(
                    {
                        "engine": params["engine"],
                        "host": params["host"],
                        "port": params["port"],
                        "user": params["user"],
                        "password": params["password"],
                        "database": params["database"],
                        "tls": params["tls"],
                        "tls_verify": params.get("tls_verify", False),
                        "tls_ca": params.get("tls_ca"),
                    }
                )
                if params.get("hostaddr"):
                    target_config["hostaddr"] = params["hostaddr"]
            connection = create_direct_connection(target_config, connect_timeout=5)
        except Exception as exc:
            from shared.api.ssh_errors import connectivity_error_payload

            if tunnel_manager is not None:
                tunnel_manager.close(guard.target_name)
            payload = connectivity_error_payload(
                exc,
                guard.target_name,
                target_config,
            )
            if payload:
                return {
                    "status": "error",
                    "detail": payload["message"],
                    "remediation": None,
                    "category": payload["category"],
                }
            detail = str(exc).splitlines()[0][:300] if str(exc) else "Connection failed"
            if ssh_config:
                detail = (
                    f"SSH tunnel to jump host {ssh_config.get('host')} opened, but "
                    f"the database is unreachable through it: {detail}. Check the "
                    "database host, port, network rules, and DB credentials."
                )
                category = "database_connection_failed"
            else:
                from features.allowlist.service import (
                    is_provider_network_failure,
                    provider_network_hint,
                )

                if is_provider_network_failure(target_config, exc):
                    detail = f"{detail}. {provider_network_hint(target_config)}"
                    category = "provider_ip_blocked_maybe"
                else:
                    category = None
            return {
                "status": "error",
                "detail": detail,
                "remediation": None,
                "category": category,
            }
        try:
            return CaptureService.check_query_stats(connection, guard.target_engine)
        finally:
            try:
                connection.close()
            except Exception:
                pass

    from features.audit.readyset_benchmark import docker_available

    result = await asyncio.to_thread(_check)
    has_docker = await asyncio.to_thread(docker_available)
    return AuditRequirementsResponse(
        target=guard.target_name,
        engine=guard.target_engine,
        query_stats=result["status"],
        detail=result.get("detail") or "",
        remediation=result.get("remediation"),
        docker_available=has_docker,
        category=result.get("category"),
    )


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
            engine=run.get("engine", "") or "",
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


@router.post("/runs/{run_id}/email")
async def email_audit_run(
    run_id: str, request: Request, body: RunEmailRequest
) -> RunEmailResponse:
    """Email the report for a saved audit run.

    The report is not gated on email: this is an explicit, optional send.
    """
    return await deliver_run_report(run_id, request, body)
