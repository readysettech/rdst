"""Fleet feature slice."""

from .csv_importer import detect_region_from_hostname, parse_csv
from .events import (
    FleetConnectivityEvent,
    FleetDiscoverEvent,
    FleetErrorEvent,
    FleetEvent,
    FleetImportCompleteEvent,
    FleetImportProgressEvent,
    FleetInput,
    FleetListEvent,
    FleetOptions,
    FleetStatusEvent,
)
from .llm import build_fleet_insights_prompt, build_single_target_insights_prompt
from .models import FleetAuditSnapshot, FleetDiff, FleetDiffEntry, FleetMember, SizingVerdict, TargetType
from .pricing import estimate_class_from_shared_buffers, get_instance_info, monthly_cost, suggest_downsize
from .service import FleetService
from .snapshot_store import SnapshotStore

__all__ = [
    "FleetAuditSnapshot",
    "FleetConnectivityEvent",
    "FleetDiff",
    "FleetDiffEntry",
    "FleetDiscoverEvent",
    "FleetErrorEvent",
    "FleetEvent",
    "FleetImportCompleteEvent",
    "FleetImportProgressEvent",
    "FleetInput",
    "FleetListEvent",
    "FleetMember",
    "FleetOptions",
    "FleetService",
    "FleetStatusEvent",
    "SizingVerdict",
    "SnapshotStore",
    "TargetType",
    "build_fleet_insights_prompt",
    "build_single_target_insights_prompt",
    "detect_region_from_hostname",
    "estimate_class_from_shared_buffers",
    "get_instance_info",
    "monthly_cost",
    "parse_csv",
    "suggest_downsize",
]
