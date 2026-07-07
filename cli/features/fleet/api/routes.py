"""Fleet API routes.

Targets listing is plain JSON (config read, no database access). Status
checks and CSV import stream FleetEvent payloads over SSE. `rdst web` runs
on the user's machine, so the import endpoint takes a local CSV path, same
as the CLI's `fleet import --from`.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, AsyncGenerator, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from sse_starlette.sse import EventSourceResponse

from ..service import FleetService
from ..snapshot_store import SnapshotStore

router = APIRouter(prefix="/fleet", tags=["fleet"])


class FleetImportRequest(BaseModel):
    csv_file: str
    password_env: str = "FLEET_PASS"
    group: Optional[str] = None
    tags: Optional[list[str]] = None
    dry_run: bool = False


class FleetTargetsResponse(BaseModel):
    members: list[dict[str, Any]]
    groups: list[str]
    count: int


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


@router.get("/status")
async def check_fleet_status(
    group: Optional[str] = Query(default=None),
    tag: Optional[str] = Query(default=None),
):
    """Check connectivity for fleet targets (SSE stream)."""
    service = FleetService()

    async def _generator() -> AsyncGenerator[dict, None]:
        async for event in service.check_status(group=group, tag=tag):
            yield _event_to_sse(event)

    return EventSourceResponse(_generator())


class FleetDiscoverRequest(BaseModel):
    regions: list[str]
    engine_filter: Optional[str] = None
    name_pattern: Optional[str] = None
    password_env: str = "FLEET_PASS"
    user: Optional[str] = None
    default_database: Optional[str] = None
    group: Optional[str] = None
    dry_run: bool = False


@router.post("/discover")
async def discover_fleet(request: FleetDiscoverRequest):
    """Discover RDS/Aurora instances from AWS and add them as targets (SSE stream)."""
    service = FleetService()
    engine_filter = request.engine_filter if request.engine_filter not in (None, "all") else None

    async def _generator() -> AsyncGenerator[dict, None]:
        if not request.regions:
            yield {
                "event": "error",
                "data": json.dumps({"message": "At least one region is required", "phase": "discover"}),
            }
            return
        async for event in service.discover(
            request.regions,
            engine_filter=engine_filter,
            name_pattern=request.name_pattern,
            password_env=request.password_env,
            default_user=request.user,
            default_group=request.group,
            default_database=request.default_database,
            dry_run=request.dry_run,
        ):
            yield _event_to_sse(event)

    return EventSourceResponse(_generator())


class FleetAuditRequest(BaseModel):
    group: Optional[str] = None
    tag: Optional[str] = None
    insights: bool = True
    save: bool = True
    save_name: Optional[str] = None


@router.post("/audit")
async def run_fleet_audit(request: FleetAuditRequest):
    """Audit all fleet targets concurrently (SSE stream of AuditEvent)."""
    import datetime

    from features.audit.service import AuditService
    from shared.config.targets import TargetsConfig

    config = TargetsConfig()
    config.load()
    targets = config.list_fleet_targets(group=request.group, tag=request.tag)
    save_name = request.save_name
    if save_name is None and request.save:
        save_name = f"fleet_{datetime.datetime.now():%Y%m%d_%H%M%S}"
    service = AuditService(config=config)

    async def _generator() -> AsyncGenerator[dict, None]:
        if not targets:
            yield {
                "event": "error",
                "data": json.dumps({"message": "No targets found", "phase": "start"}),
            }
            return
        async for event in service.audit_fleet(
            targets, insights=request.insights, save_name=save_name,
        ):
            yield _event_to_sse(event)

    return EventSourceResponse(_generator())


class FleetSnapshotSummary(BaseModel):
    snapshot_id: str
    name: str
    created_at: str
    targets_audited: int
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
    """Import fleet targets from a local CSV file (SSE stream)."""
    service = FleetService()

    async def _generator() -> AsyncGenerator[dict, None]:
        async for event in service.import_fleet(
            request.csv_file,
            password_env=request.password_env,
            default_group=request.group,
            default_tags=request.tags,
            dry_run=request.dry_run,
        ):
            yield _event_to_sse(event)

    return EventSourceResponse(_generator())
