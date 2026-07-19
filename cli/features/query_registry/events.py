"""Query registry feature events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Optional, Union

from .models import QueryBenchmarkStats


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
