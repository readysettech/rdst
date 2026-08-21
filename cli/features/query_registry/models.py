"""Query registry feature models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class QueryCommandInput:
    """Input for query service command execution."""

    subcommand: str
    kwargs: dict[str, Any]


@dataclass
class QueryBenchmarkStats:
    """Statistics for a single benchmarked query."""

    query_name: str
    query_hash: str
    executions: int
    successes: int
    failures: int
    min_ms: float
    avg_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float
    last_error: Optional[str] = None
    # Failures the per-statement timeout stopped, counted apart from the rest
    # so a query that is merely too slow reads differently from a broken one.
    timeouts: int = 0
    # Concrete parameter variants this query rotated through.
    variant_count: int = 1
    # One entry per lane the query ran in, each in the shape of the fields
    # above. The fields above carry the origin lane, so a reader that knows
    # nothing about lanes still reads the origin measurement.
    lanes: Optional[dict[str, dict[str, Any]]] = None


@dataclass
class QuerySkip:
    """One query left out of a run, and the reason it was left out.

    A skip with no lanes is out of the whole run. A skip that names lanes is
    missing from those lanes only, and runs in the run's others.
    """

    query_hash: str
    query_name: str
    reason: str
    lanes: Optional[dict[str, str]] = None
