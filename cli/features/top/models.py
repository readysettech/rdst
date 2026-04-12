"""Top feature models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class TopSource(str, Enum):
    AUTO = "auto"
    PG_STAT = "pg_stat"
    ACTIVITY = "activity"
    DIGEST = "digest"


class TopSortField(str, Enum):
    TOTAL_TIME = "total_time"
    FREQ = "freq"
    AVG_TIME = "avg_time"
    LOAD = "load"


@dataclass
class TopInput:
    """Input for top service."""

    target: Optional[str] = None
    source: str = TopSource.AUTO.value


@dataclass
class TopOptions:
    """Options for top service execution."""

    limit: int = 10
    sort: str = TopSortField.TOTAL_TIME.value
    filter_pattern: Optional[str] = None
    min_freq: int = 0
    min_load_pct: float = 0.0
    min_total_time_s: float = 0.0
    poll_interval_ms: int = 200
    auto_save_registry: bool = True


@dataclass
class TopQueryData:
    """Individual query data."""

    query_hash: str
    query_text: str
    normalized_query: str
    freq: int
    total_time: str
    avg_time: str
    pct_load: str
    qps: Optional[float] = None
    max_duration_ms: Optional[float] = None
    current_instances: Optional[int] = None
    observation_count: Optional[int] = None
