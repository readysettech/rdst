"""Fleet feature slice."""

from .auth import (
    detect_aws_credentials,
    get_botocore_session,
    get_rds_client,
    get_secretsmanager_client,
)
from .csv_importer import detect_region_from_hostname, parse_csv
from .discovery import _ENGINE_MAP, _parse_instance, discover_aurora_cluster, discover_rds_instances
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
from .secrets import DEFAULT_TTL_SECONDS, clear_cache, resolve_secret
from .service import FleetService
from .snapshot_store import SnapshotStore

__all__ = [
    "DEFAULT_TTL_SECONDS",
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
    "_ENGINE_MAP",
    "_parse_instance",
    "build_fleet_insights_prompt",
    "build_single_target_insights_prompt",
    "clear_cache",
    "detect_aws_credentials",
    "detect_region_from_hostname",
    "discover_aurora_cluster",
    "discover_rds_instances",
    "estimate_class_from_shared_buffers",
    "get_botocore_session",
    "get_instance_info",
    "get_rds_client",
    "get_secretsmanager_client",
    "monthly_cost",
    "parse_csv",
    "resolve_secret",
    "suggest_downsize",
]
