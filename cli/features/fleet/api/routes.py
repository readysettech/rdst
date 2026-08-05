"""Fleet API routes.

Targets listing is plain JSON (config read, no database access). Status
checks and CSV import stream FleetEvent payloads over SSE. `rdst web` runs
on the user's machine, so the import endpoint takes a local CSV path (same
as the CLI's `fleet import --from`) or raw CSV text from the browser's
file picker.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import asdict
from typing import Any, AsyncGenerator, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from pydantic import SecretStr

from sse_starlette.sse import EventSourceResponse

from features.audit.api.routes import AuditRunStartResponse
from features.audit.report.delivery import (
    RunEmailRequest,
    RunEmailResponse,
    deliver_run_report,
)
from shared.run_registry import AUDIT_RUN_KINDS, run_registry
from shared.service_events import ErrorEvent

from ..service import FleetService, fleet_member_shape
from ..snapshot_store import SnapshotStore

router = APIRouter(prefix="/fleet", tags=["fleet"])

# Tests replace this with an isolated registry instance.
_registry = run_registry


class FleetImportRequest(BaseModel):
    # Either a server-local path or the raw CSV text (from the browser file
    # picker). Exactly one must be provided; csv_content wins when both are.
    csv_file: Optional[str] = None
    csv_content: Optional[str] = None
    password_env: Optional[str] = None
    password: Optional[SecretStr] = None
    group: Optional[str] = None
    tags: Optional[list[str]] = None
    dry_run: bool = False


class FleetTargetsResponse(BaseModel):
    members: list[dict[str, Any]]
    groups: list[str]
    count: int


class FleetTargetGroupRequest(BaseModel):
    group: Optional[str] = None


def _event_to_sse(event: Any) -> dict:
    return {"event": event.type, "data": json.dumps(asdict(event), default=str)}


@router.get("/targets")
async def list_fleet_targets(
    group: Optional[str] = Query(default=None),
    tag: Optional[str] = Query(default=None),
) -> FleetTargetsResponse:
    """List fleet members (all configured database targets)."""
    service = FleetService()
    members: list[dict[str, Any]] = []
    groups: list[str] = []
    async for event in service.list_fleet(group=group, tag=tag):
        if event.type == "fleet_list":
            members = event.members
            groups = event.groups
    return FleetTargetsResponse(members=members, groups=groups, count=len(members))


@router.patch("/targets/{name}")
async def patch_fleet_target_group(
    name: str, request: FleetTargetGroupRequest
) -> dict[str, Any]:
    """Set or clear a configured fleet target's group."""
    from shared.config.targets import TargetsConfig

    config = TargetsConfig()
    config.load()
    target = config.get(name)
    if target is None:
        raise HTTPException(status_code=404, detail=f"Target '{name}' not found")
    if request.group is None:
        target.pop("group", None)
    else:
        target["group"] = request.group
    config.upsert(name, target)
    config.save()
    return fleet_member_shape(name, target)


@router.get("/status")
async def check_fleet_status(
    group: Optional[str] = Query(default=None),
    tag: Optional[str] = Query(default=None),
    targets: Optional[list[str]] = Query(default=None),
):
    """Check connectivity for fleet targets (SSE stream)."""
    service = FleetService()

    async def _generator() -> AsyncGenerator[dict, None]:
        async for event in service.check_status(
            group=group,
            tag=tag,
            targets=targets,
        ):
            yield _event_to_sse(event)

    return EventSourceResponse(_generator())


class FleetAuditRequest(BaseModel):
    group: Optional[str] = None
    tag: Optional[str] = None
    # Explicit target names to audit; mutually exclusive with group/tag.
    targets: Optional[list[str]] = None
    insights: bool = True
    save: bool = True
    save_name: Optional[str] = None
    # Live capture window per target in seconds (clamped 10-3600).
    # Absent/null = metrics-only audit.
    duration: Optional[int] = None


# Fleet audits are capped: each target holds a database connection for the
# whole run, and duration captures multiply the cost. The cap applies to the
# resolved target set regardless of how it was selected.
MAX_FLEET_AUDIT_TARGETS = 8

# One failed target is isolated: the fleet audit keeps auditing its siblings
# and still completes, so this event must not fail the whole run.
FLEET_CHILD_ERROR_EVENTS = frozenset({"target_error"})


@router.post("/audit", response_model=AuditRunStartResponse)
async def run_fleet_audit(request: FleetAuditRequest) -> AuditRunStartResponse:
    """Start a fleet-wide audit as one detached background run.

    Targets come from an explicit `targets` list, or from the fleet filtered
    by group/tag (all fleet targets when neither is given). The whole fleet
    is a single run, so its events read back through
    `/api/runs/{run_id}/events` and one cancel stops every target.
    """
    import datetime

    from features.audit.service import AuditService
    from shared.config.targets import TargetsConfig

    existing = _registry.find_active(AUDIT_RUN_KINDS)
    if existing is not None:
        return AuditRunStartResponse(run_id=existing, reused=True)

    config = TargetsConfig()
    config.load()
    if request.targets is not None and (request.group or request.tag):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_request",
                "message": "'targets' cannot be combined with 'group' or 'tag'",
            },
        )
    if request.targets is not None:
        known = set(config.list_fleet_targets())
        unknown = [name for name in request.targets if name not in known]
        if unknown:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "unknown_targets",
                    "message": f"Unknown targets: {', '.join(unknown)}",
                    "targets": unknown,
                },
            )
        # Dedupe while preserving request order.
        targets = list(dict.fromkeys(request.targets))
    else:
        targets = config.list_fleet_targets(group=request.group, tag=request.tag)
    save_name = request.save_name
    if save_name is None and request.save:
        save_name = f"fleet_{datetime.datetime.now():%Y%m%d_%H%M%S}"
    duration = max(10, min(request.duration, 3600)) if request.duration else None
    service = AuditService(config=config)

    async def _generator() -> AsyncGenerator[Any, None]:
        if not targets:
            yield ErrorEvent(type="error", message="No targets found", stage="start")
            return
        if len(targets) > MAX_FLEET_AUDIT_TARGETS:
            yield ErrorEvent(
                type="error",
                message=(
                    f"Audit up to {MAX_FLEET_AUDIT_TARGETS} targets at once — "
                    f"filter by group or tag ({len(targets)} targets selected)"
                ),
                code="too_many_targets",
                stage="start",
            )
            return
        async for event in service.audit_fleet(
            targets, insights=request.insights, save_name=save_name,
            duration_seconds=duration,
        ):
            yield event

    run_id = _registry.start(
        "fleet_audit",
        request.group or "fleet",
        _generator(),
        child_error_events=FLEET_CHILD_ERROR_EVENTS,
    )
    return AuditRunStartResponse(run_id=run_id)


class FleetSnapshotSummary(BaseModel):
    snapshot_id: str
    name: str
    created_at: str
    targets_audited: int
    # Names of the audited targets and the longest capture window across
    # them, so the history list needs no per-snapshot detail fetch.
    target_names: list[str] = []
    duration_seconds: int = 0
    kind: str = "fleet"


class FleetSnapshotListResponse(BaseModel):
    snapshots: list[FleetSnapshotSummary]
    count: int


class FleetDiffEntryResponse(BaseModel):
    target_name: str
    field_name: str
    old_value: Any = None
    new_value: Any = None
    change_pct: Optional[float] = None


class FleetDiffResponse(BaseModel):
    baseline_id: str
    current_id: str
    baseline_date: str
    current_date: str
    entries: list[FleetDiffEntryResponse]
    new_targets: list[str]
    removed_targets: list[str]


@router.get("/snapshots")
async def list_fleet_snapshots(
    kind: str = Query(
        "fleet",
        description="Filter by snapshot kind: fleet, single, or all",
    ),
) -> FleetSnapshotListResponse:
    """List saved fleet audit snapshots, newest first.

    Single-target audit saves share the snapshot directory; they belong to
    the audit run history, so this defaults to fleet-audit snapshots only.
    """
    snapshots = SnapshotStore().list_snapshots()
    if kind != "all":
        snapshots = [snap for snap in snapshots if snap.get("kind") == kind]
    summaries = [
        FleetSnapshotSummary(
            snapshot_id=snap.get("snapshot_id", ""),
            name=snap.get("name", ""),
            created_at=snap.get("created_at", "") or "",
            targets_audited=snap.get("targets_audited", 0) or 0,
            target_names=snap.get("target_names") or [],
            duration_seconds=snap.get("duration_seconds", 0) or 0,
            kind=snap.get("kind", "fleet"),
        )
        for snap in snapshots
    ]
    return FleetSnapshotListResponse(snapshots=summaries, count=len(summaries))


@router.get("/snapshots/{snapshot_id}")
async def get_fleet_snapshot(snapshot_id: str) -> dict[str, Any]:
    """Fetch a snapshot's full payload by ID (exact or prefix)."""
    data = SnapshotStore().load(snapshot_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Snapshot '{snapshot_id}' not found")
    return data


@router.post("/snapshots/{snapshot_id}/email")
async def email_fleet_snapshot(
    snapshot_id: str, request: Request, body: RunEmailRequest
) -> RunEmailResponse:
    """Email the report for a saved fleet snapshot.

    Same artifact and delivery path as the single-target audit run endpoint.
    """
    return await deliver_run_report(snapshot_id, request, body)


@router.delete("/snapshots/{snapshot_id}")
async def delete_fleet_snapshot(snapshot_id: str) -> dict[str, bool]:
    """Delete a snapshot by exact ID."""
    if not SnapshotStore().delete(snapshot_id):
        raise HTTPException(status_code=404, detail=f"Snapshot '{snapshot_id}' not found")
    return {"success": True}


@router.get("/diff")
async def diff_fleet_snapshots(
    baseline: str = Query(...),
    current: str = Query(...),
) -> FleetDiffResponse:
    """Compare two snapshots' per-target metrics and sizing verdicts."""
    diff = SnapshotStore().diff(baseline, current)
    if diff is None:
        raise HTTPException(
            status_code=404,
            detail="One or both snapshots not found",
        )
    return FleetDiffResponse(
        baseline_id=diff.baseline_id,
        current_id=diff.current_id,
        baseline_date=diff.baseline_date or "",
        current_date=diff.current_date or "",
        entries=[
            FleetDiffEntryResponse(
                target_name=entry.target_name,
                field_name=entry.field_name,
                old_value=entry.old_value,
                new_value=entry.new_value,
                change_pct=entry.change_pct,
            )
            for entry in diff.entries
        ],
        new_targets=diff.new_targets,
        removed_targets=diff.removed_targets,
    )


@router.post("/import")
async def import_fleet(request: FleetImportRequest):
    """Import fleet targets from a CSV file or uploaded content (SSE stream)."""
    import tempfile

    service = FleetService()

    async def _generator() -> AsyncGenerator[dict, None]:
        csv_file = request.csv_file
        tmp_path: Optional[str] = None
        if request.csv_content is not None:
            tmp = tempfile.NamedTemporaryFile(
                mode="w", suffix=".csv", delete=False, encoding="utf-8"
            )
            tmp.write(request.csv_content)
            tmp.close()
            csv_file = tmp_path = tmp.name
        if not csv_file:
            yield {
                "event": "error",
                "data": json.dumps(
                    {"message": "Provide csv_file or csv_content", "phase": "import"}
                ),
            }
            return
        try:
            async for event in service.import_fleet(
                csv_file,
                password_env=request.password_env,
                password=(
                    request.password.get_secret_value() if request.password else None
                ),
                default_group=request.group,
                default_tags=request.tags,
                dry_run=request.dry_run,
            ):
                yield _event_to_sse(event)
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    return EventSourceResponse(_generator())
