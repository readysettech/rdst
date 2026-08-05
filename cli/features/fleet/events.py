"""Fleet-owned service contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Union


@dataclass
class FleetInput:
    subcommand: str
    csv_file: str | None = None
    password_env: str | None = None
    group: str | None = None
    tag: str | None = None
    regions: str | None = None
    engine_filter: str | None = None
    name_pattern: str | None = None


@dataclass
class FleetOptions:
    dry_run: bool = False
    output_json: bool = False
    save_name: str | None = None
    diff_baseline: str | None = None
    insights: bool = False


@dataclass
class FleetStatusEvent:
    type: Literal["status"]
    phase: str
    message: str


@dataclass
class FleetImportProgressEvent:
    type: Literal["import_progress"]
    current: int
    total: int
    target_name: str
    status: str
    message: str


@dataclass
class FleetImportCompleteEvent:
    type: Literal["import_complete"]
    success: bool
    imported: int
    skipped: int
    errors: int
    target_names: list[str]


@dataclass
class FleetDiscoverEvent:
    type: Literal["discover"]
    instances_found: int
    regions_searched: list[str]
    message: str


@dataclass
class FleetListEvent:
    type: Literal["fleet_list"]
    members: list[dict[str, Any]]
    groups: list[str]


@dataclass
class FleetConnectivityEvent:
    type: Literal["connectivity"]
    target_name: str
    status: str
    latency_ms: float | None = None
    error: str | None = None
    server_version: str | None = None
    code: str | None = None
    category: str | None = None
    password_env: str | None = None
    privileges: dict[str, Any] | None = None


@dataclass
class FleetErrorEvent:
    type: Literal["error"]
    message: str
    phase: str | None = None


FleetEvent = Union[
    FleetStatusEvent,
    FleetImportProgressEvent,
    FleetImportCompleteEvent,
    FleetDiscoverEvent,
    FleetListEvent,
    FleetConnectivityEvent,
    FleetErrorEvent,
]
