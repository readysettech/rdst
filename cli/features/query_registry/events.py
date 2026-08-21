"""Query registry feature events."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional, Union

from .models import QueryBenchmarkStats, QuerySkip


@dataclass
class QueryStatusEvent:
    """Status update for query operations."""

    type: Literal["status"]
    message: str


@dataclass
class QueryCompleteEvent:
    """Query operation complete with result payload."""

    type: Literal["complete"]
    success: bool
    result: dict[str, Any]


@dataclass
class QueryErrorEvent:
    """Query operation failed."""

    type: Literal["error"]
    message: str


@dataclass
class QueryBenchmarkProgressEvent:
    """Benchmark progress tick."""

    type: Literal["progress"]
    elapsed_seconds: float
    total_executions: int
    total_successes: int
    total_failures: int
    qps: float
    queries: list[QueryBenchmarkStats]
    # Warmup runs before the clock starts and stays out of every statistic;
    # its tally is reported so the count of real work is not a mystery.
    warmup_executions: int = 0
    skipped_count: int = 0
    skipped_queries: list[QuerySkip] = field(default_factory=list)
    # Set on the ticks the run emits while it prepares the Readyset lane's
    # caches, before any worker starts and while every tally above is zero.
    # ``prepared_count`` of ``prepare_total`` caches exist at that point.
    phase: Optional[Literal["preparing"]] = None
    prepared_count: Optional[int] = None
    prepare_total: Optional[int] = None


@dataclass
class QueryBenchmarkCompleteEvent:
    """Benchmark finished; carries the final tally."""

    type: Literal["complete"]
    elapsed_seconds: float
    total_executions: int
    total_successes: int
    total_failures: int
    qps: float
    queries: list[QueryBenchmarkStats]
    warmup_executions: int = 0
    skipped_count: int = 0
    skipped_queries: list[QuerySkip] = field(default_factory=list)
    # The lanes that produced measurements, in reporting order.
    lanes_run: list[str] = field(default_factory=lambda: ["origin"])
    # {"status": "ok" | "unavailable", "detail": str}, present when the
    # caller asked for the Readyset lane.
    readyset_setup: Optional[dict[str, str]] = None


@dataclass
class QueryBenchmarkErrorEvent:
    """Benchmark failed (or was rejected by a safety rail) before completion.

    Carries the shared error envelope ({code, message, detail}, B7/T24) so the
    client normalizes a benchmark failure exactly like every other SSE error.
    ``message`` stays humane and safe to show; ``detail`` holds only the
    exception class name for correlation — never the raw ``str(e)``, which can
    embed host / DSN / SQL material.
    """

    type: Literal["error"]
    message: str
    code: Optional[str] = None
    detail: Optional[str] = None


QueryEvent = Union[QueryStatusEvent, QueryCompleteEvent, QueryErrorEvent]
QueryBenchmarkEvent = Union[
    QueryBenchmarkProgressEvent,
    QueryBenchmarkCompleteEvent,
    QueryBenchmarkErrorEvent,
]
