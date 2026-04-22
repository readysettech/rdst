"""Analyze feature events."""

from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Union

from shared.service_events import ErrorEvent, ProgressEvent


@dataclass
class ExplainCompleteEvent:
    """EXPLAIN ANALYZE execution completed."""

    type: Literal["explain_complete"]
    success: bool
    database_engine: str
    execution_time_ms: float
    rows_examined: int
    rows_returned: int
    cost_estimate: float
    explain_plan: Optional[Dict[str, Any]] = None
    explain_analyze_skipped: bool = False
    error: Optional[str] = None


@dataclass
class RewritesTestedEvent:
    """Query rewrites have been tested."""

    type: Literal["rewrites_tested"]
    tested: bool
    skipped_reason: Optional[str] = None
    message: Optional[str] = None
    original_performance: Optional[Dict[str, Any]] = None
    rewrite_results: Optional[List[Dict[str, Any]]] = None
    best_rewrite: Optional[Dict[str, Any]] = None


@dataclass
class ReadysetCheckedEvent:
    """ReadySet cacheability check completed."""

    type: Literal["readyset_checked"]
    checked: bool
    cacheable: Optional[bool] = None
    confidence: Optional[Literal["high", "medium", "low", "unknown"]] = None
    method: Optional[str] = None
    explanation: Optional[str] = None
    issues: Optional[List[str]] = None
    warnings: Optional[List[str]] = None


@dataclass
class CompleteEvent:
    """Analysis completed successfully."""

    type: Literal["complete"]
    success: bool
    analysis_id: Optional[str] = None
    query_hash: Optional[str] = None
    explain_results: Optional[Dict[str, Any]] = None
    llm_analysis: Optional[Dict[str, Any]] = None
    rewrite_testing: Optional[Dict[str, Any]] = None
    readyset_cacheability: Optional[Dict[str, Any]] = None
    formatted: Optional[Dict[str, Any]] = None


AnalyzeEvent = Union[
    ProgressEvent,
    ExplainCompleteEvent,
    RewritesTestedEvent,
    ReadysetCheckedEvent,
    CompleteEvent,
    ErrorEvent,
]

__all__ = [
    "AnalyzeEvent",
    "CompleteEvent",
    "ErrorEvent",
    "ExplainCompleteEvent",
    "ProgressEvent",
    "ReadysetCheckedEvent",
    "RewritesTestedEvent",
]
