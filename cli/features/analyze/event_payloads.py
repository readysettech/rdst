from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict


class _Base(BaseModel):
    # Accept extra keys so dict-shaped service output passes through unchanged.
    model_config = ConfigDict(extra="allow")


class ExplainResults(_Base):
    success: bool
    database_engine: str
    execution_time_ms: float
    rows_examined: int
    rows_returned: int
    cost_estimate: float
    explain_plan: Optional[dict[str, Any]] = None


class PerformanceAssessment(_Base):
    overall_rating: Literal["excellent", "good", "fair", "poor"]
    efficiency_score: float
    execution_time_rating: Optional[str] = None
    primary_concerns: list[str]


class ExecutionAnalysis(_Base):
    bottlenecks: list[str]
    scan_efficiency: str


class RewriteSuggestion(_Base):
    rewritten_sql: str
    explanation: str
    expected_improvement: str
    priority: Literal["high", "medium", "low"]
    optimization_type: str


class IndexRecommendation(_Base):
    sql: str
    table: str
    columns: list[str]
    index_type: str
    rationale: str
    estimated_impact: Literal["high", "medium", "low"]
    caveats: Optional[list[str]] = None


class OptimizationOpportunity(_Base):
    priority: Literal["high", "medium", "low"]
    description: str
    type: str


class TokenUsage(_Base):
    input: int
    output: int
    total: int
    estimated_cost_usd: float


class LLMAnalysis(_Base):
    success: Optional[bool] = None
    performance_assessment: Optional[PerformanceAssessment] = None
    execution_analysis: Optional[ExecutionAnalysis] = None
    rewrite_suggestions: Optional[list[RewriteSuggestion]] = None
    index_recommendations: Optional[list[IndexRecommendation]] = None
    optimization_opportunities: Optional[list[OptimizationOpportunity]] = None
    llm_model: Optional[str] = None
    token_usage: Optional[TokenUsage] = None


class RewritePerformance(_Base):
    execution_time_ms: float
    rows_returned: Optional[int] = None
    rows_examined: Optional[int] = None
    cost_estimate: Optional[float] = None


class RewriteImprovementOverall(_Base):
    improvement_pct: float


class RewriteImprovement(_Base):
    overall: RewriteImprovementOverall


class RewriteSuggestionMetadata(_Base):
    explanation: str


class TestedRewrite(_Base):
    success: bool
    sql: str
    performance: RewritePerformance
    improvement: RewriteImprovement
    suggestion_metadata: RewriteSuggestionMetadata


class RewriteTesting(_Base):
    tested: bool
    skipped_reason: Optional[str] = None
    message: Optional[str] = None
    original_performance: Optional[RewritePerformance] = None
    rewrite_results: Optional[list[TestedRewrite]] = None
    best_rewrite: Optional[TestedRewrite] = None


class ReadysetCacheability(_Base):
    checked: bool
    cacheable: Optional[bool] = None
    confidence: Optional[Literal["high", "medium", "low", "unknown"]] = None
    method: Optional[str] = None
    explanation: Optional[str] = None
    # Raw technical text behind the UI's details expander (P41).
    detail: Optional[str] = None
    issues: Optional[list[str]] = None
    warnings: Optional[list[str]] = None


class RowsProcessed(_Base):
    examined: int
    returned: int


class AnalysisSummary(_Base):
    overall_rating: str
    efficiency_score: float
    execution_time_ms: float
    execution_time_rating: Optional[str] = None
    rows_processed: RowsProcessed
    cost_estimate: float
    primary_concerns: list[str]
    explain_analyze_skipped: Optional[bool] = None


class ExecutionMetrics(_Base):
    total_time_ms: float
    planning_time_ms: Optional[float] = None
    actual_time_ms: Optional[float] = None
    rows_examined: int
    rows_returned: int
    cost_estimate: float


class PerformanceMetrics(_Base):
    execution_metrics: ExecutionMetrics
    database_engine: str
    explain_available: bool


class OptimizationInsights(_Base):
    available: bool
    error: Optional[str] = None
    explanation: Optional[str] = None
    optimization_opportunities: Optional[list[OptimizationOpportunity]] = None


class FormattedQueryRewrite(_Base):
    id: Optional[str] = None
    type: str
    priority: str
    confidence: Optional[str] = None
    sql: str
    explanation: str
    expected_improvement: str
    trade_offs: Optional[str] = None


class FormattedIndexSuggestion(_Base):
    id: Optional[str] = None
    table: str
    type: str
    columns: list[str]
    sql_statement: str
    expected_benefit: str
    rationale: str
    storage_impact: Optional[str] = None


class Recommendations(_Base):
    available: bool
    query_rewrites: Optional[list[FormattedQueryRewrite]] = None
    index_suggestions: Optional[list[FormattedIndexSuggestion]] = None


class LlmInfo(_Base):
    model: str
    tokens: int
    cost: float


class AnalysisMetadata(_Base):
    query: str
    normalized_query: Optional[str] = None
    parameterized_sql: Optional[str] = None
    target: str
    analysis_id: str
    database_engine: str
    analyzed_at: Optional[str] = None
    llm_info: Optional[LlmInfo] = None


class FormattedAnalysis(_Base):
    success: bool
    message: Optional[str] = None
    analysis_summary: AnalysisSummary
    performance_metrics: PerformanceMetrics
    optimization_insights: OptimizationInsights
    recommendations: Recommendations
    rewrite_testing: Optional[RewriteTesting] = None
    readyset_cacheability: Optional[ReadysetCacheability] = None
    metadata: AnalysisMetadata
