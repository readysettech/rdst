from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Union

from shared.service_events import ErrorEvent, ProgressEvent

from .event_payloads import (
    ExplainResults,
    FormattedAnalysis,
    LLMAnalysis,
    ReadysetCacheability,
    RewritePerformance,
    RewriteTesting,
    TestedRewrite,
)


@dataclass
class ExplainCompleteEvent:
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
    type: Literal["rewrites_tested"]
    tested: bool
    skipped_reason: Optional[str] = None
    message: Optional[str] = None
    original_performance: Optional[RewritePerformance] = None
    rewrite_results: Optional[List[TestedRewrite]] = None
    best_rewrite: Optional[TestedRewrite] = None


@dataclass
class ReadysetCheckedEvent:
    type: Literal["readyset_checked"]
    checked: bool
    cacheable: Optional[bool] = None
    confidence: Optional[Literal["high", "medium", "low", "unknown"]] = None
    method: Optional[str] = None
    explanation: Optional[str] = None
    # Raw technical text (driver/client output) for the UI's details expander;
    # `explanation` stays human-readable (P41).
    detail: Optional[str] = None
    issues: Optional[List[str]] = None
    warnings: Optional[List[str]] = None


@dataclass
class CompleteEvent:
    type: Literal["complete"]
    success: bool
    analysis_id: Optional[str] = None
    query_hash: Optional[str] = None
    explain_results: Optional[ExplainResults] = None
    llm_analysis: Optional[LLMAnalysis] = None
    rewrite_testing: Optional[RewriteTesting] = None
    readyset_cacheability: Optional[ReadysetCacheability] = None
    formatted: Optional[FormattedAnalysis] = None


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
