"""Cache feature events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional, Union

from shared.service_events import ErrorEvent, ProgressEvent


@dataclass
class CacheStatusEvent:
    """Cache deployment status."""

    type: Literal["cache_status"]
    deployed: bool
    running: bool
    endpoint: Optional[str] = None
    cache_target: Optional[str] = None
    container_name: Optional[str] = None


@dataclass
class CacheDeployCompleteEvent:
    """Cache deployment completed."""

    type: Literal["deploy_complete"]
    success: bool
    deployed: bool
    running: bool
    endpoint: Optional[str] = None
    cache_target: Optional[str] = None
    container_name: Optional[str] = None


@dataclass
class CacheListEvent:
    """List of cached queries."""

    type: Literal["cache_list"]
    success: bool
    caches: list[dict[str, str]]
    count: int


@dataclass
class CacheAddEvent:
    """Cache add result."""

    type: Literal["cache_add"]
    success: bool
    supported: bool
    query: str
    query_hash: Optional[str] = None
    detail: Optional[str] = None


@dataclass
class CacheDeleteEvent:
    """Cache delete result."""

    type: Literal["cache_delete"]
    success: bool
    cache_id: str


@dataclass
class CacheDropAllEvent:
    """Drop all caches result."""

    type: Literal["cache_drop_all"]
    success: bool
    count: int


@dataclass
class CacheRunCompleteEvent:
    """Performance comparison result (origin vs cache)."""

    type: Literal["cache_run_complete"]
    success: bool
    query: str
    iterations: int
    origin_stats: dict[str, float]
    cache_stats: dict[str, float]
    speedup_mean: float
    speedup_median: float
    improvement_pct: float
    winner: str


CacheEvent = Union[
    ProgressEvent,
    CacheStatusEvent,
    CacheDeployCompleteEvent,
    CacheListEvent,
    CacheAddEvent,
    CacheDeleteEvent,
    CacheDropAllEvent,
    CacheRunCompleteEvent,
    ErrorEvent,
]
