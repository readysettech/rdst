"""Query registry feature events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Union

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
    """Benchmark failed before completion."""

    type: Literal["error"]
    error: str


QueryEvent = Union[QueryStatusEvent, QueryCompleteEvent, QueryErrorEvent]
QueryBenchmarkEvent = Union[
    QueryBenchmarkProgressEvent,
    QueryBenchmarkCompleteEvent,
    QueryBenchmarkErrorEvent,
]
