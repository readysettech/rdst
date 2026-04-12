"""Scan feature events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Optional, Union

from .models import ScanPhase


@dataclass
class ScanStatusEvent:
    """Status update during scan."""

    type: Literal["status"]
    phase: ScanPhase | str
    message: str


@dataclass
class ScanFilesFoundEvent:
    """Files with ORM patterns discovered."""

    type: Literal["files_found"]
    files: list[dict[str, Any]]
    total: int


@dataclass
class ScanProgressEvent:
    """Progress update within a scan phase."""

    type: Literal["progress"]
    phase: ScanPhase | str
    current: int
    total: int
    message: str


@dataclass
class ScanQueryResultEvent:
    """Individual query result from scan."""

    type: Literal["query_result"]
    query: dict[str, Any]


@dataclass
class ScanRegistryEvent:
    """Registry save results."""

    type: Literal["registry"]
    new_queries: int
    updated_queries: int
    total_queries: int
    skipped: bool
    registry_path: str = ""


@dataclass
class ScanCompleteEvent:
    """Scan completed."""

    type: Literal["complete"]
    success: bool
    summary: dict[str, Any]


@dataclass
class ScanErrorEvent:
    """Scan error."""

    type: Literal["error"]
    message: str
    phase: Optional[ScanPhase | str] = None


ScanEvent = Union[
    ScanStatusEvent,
    ScanFilesFoundEvent,
    ScanProgressEvent,
    ScanQueryResultEvent,
    ScanRegistryEvent,
    ScanCompleteEvent,
    ScanErrorEvent,
]
