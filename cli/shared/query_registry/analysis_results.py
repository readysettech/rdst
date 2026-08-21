"""
Analysis Results Storage Extension for Query Registry

Extends the query registry to store comprehensive analysis results from
the RDST analyze workflow, including performance metrics, LLM insights,
and rewrite test results.

Bodies are stored in ``library.db`` (schema v13, see library_store.py),
which imports whatever ``analysis_results.toml`` held when it first opens
and leaves the file beside its backup for the previous release to read.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any

import shared.constants as shared_constants

from .library_store import (
    ANALYSIS_HISTORY_LIMIT,
    LibraryStore,
    library_db_path_for,
)

logger = logging.getLogger(__name__)


@dataclass
class AnalysisResult:
    """
    Represents comprehensive analysis results for a query.
    """
    query_hash: str
    analysis_id: str  # Unique ID for this analysis run
    target: str
    timestamp: str

    # Core analysis results
    performance_metrics: Dict[str, Any]
    llm_analysis: Dict[str, Any]
    explain_plan: Dict[str, Any]
    query_metrics: Dict[str, Any]

    # Optimization results
    rewrite_suggestions: List[Dict[str, Any]]
    index_suggestions: List[Dict[str, Any]]
    caching_recommendations: Dict[str, Any]
    rewrite_test_results: Optional[Dict[str, Any]] = None

    # Metadata
    database_engine: str = ""
    analysis_duration_ms: float = 0.0
    llm_model_used: str = ""
    tokens_used: int = 0

    # The finished analyze run exactly as the results view consumed it
    # (explain_results, llm_analysis, rewrite_testing, index_testing,
    # readyset_cacheability, formatted), so reopening a stored analysis
    # renders from storage instead of re-running the query.
    display_payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AnalysisResult':
        """Create AnalysisResult from a stored dictionary."""
        # Handle backward compatibility. A body written by a newer build
        # may carry fields this one does not model; they stay in storage
        # and ride the read-only route, so dropping them here is safe.
        known = set(cls.__dataclass_fields__)
        data = {key: value for key, value in data.items() if key in known}
        for key, default_value in [
            ('rewrite_test_results', None),
            ('database_engine', ''),
            ('analysis_duration_ms', 0.0),
            ('llm_model_used', ''),
            ('tokens_used', 0),
            ('display_payload', {}),
        ]:
            if key not in data:
                data[key] = default_value

        return cls(**data)


class AnalysisResultsRegistry:
    """
    Registry for storing and retrieving query analysis results.

    Results live in the ``query_analysis`` table of the library store
    beside the query registry, keyed by (query_hash, analysis_id) and
    bounded to the most recent ANALYSIS_HISTORY_LIMIT runs per query. A
    re-run appends a row; the oldest beyond the cap is dropped at insert.
    """

    def __init__(self, registry_path: Optional[str] = None):
        """
        Initialize the analysis results registry.

        Args:
            registry_path: Custom path to the legacy TOML file. Defaults to
                ~/.rdst/analysis_results.toml; the authoritative store is the
                library.db serving the query registry beside it, and the TOML
                is imported the first time that store opens.
        """
        if registry_path:
            self.registry_path = Path(registry_path)
        else:
            self.registry_path = shared_constants.rdst_data_dir() / "analysis_results.toml"

        queries_path = _queries_toml_path_for(self.registry_path)
        self._store = LibraryStore(library_db_path_for(queries_path), queries_path)

    def load(self) -> None:
        """Open the backing store, importing a legacy TOML if one is there."""
        self._store.ensure_open()

    def save(self) -> None:
        """No-op: every write commits its own transaction."""

    def store_analysis_result(self, query_hash: str, result: AnalysisResult) -> str:
        """
        Store an analysis result for a query.

        Args:
            query_hash: Hash of the analyzed query
            result: AnalysisResult object to store

        Returns:
            The analysis_id for the stored result
        """
        if not result.analysis_id:
            result.analysis_id = self._next_analysis_id(query_hash)

        self._store.record_analysis(
            query_hash=query_hash,
            analysis_id=result.analysis_id,
            created_at=result.timestamp,
            target=result.target,
            payload=result.to_dict(),
            keep=ANALYSIS_HISTORY_LIMIT,
        )
        return result.analysis_id

    def attach_display_payload(
        self, query_hash: str, analysis_id: str, payload: Dict[str, Any]
    ) -> bool:
        """
        Attach the finished results view to an already-stored analysis.

        The workflow stores an analysis before the run's cacheability and
        index findings exist, so the display payload lands in a second
        write once the run has produced everything the viewer renders.

        Returns:
            True when the stored analysis was updated.
        """
        stored = self._store.analysis_payload(query_hash, analysis_id)
        if stored is None:
            return False
        stored["display_payload"] = payload
        return self._store.update_analysis_payload(query_hash, analysis_id, stored)

    def get_latest_analysis(self, query_hash: str) -> Optional[AnalysisResult]:
        """
        Get the most recent analysis result for a query.

        Args:
            query_hash: Hash of the query

        Returns:
            Most recent AnalysisResult or None if not found
        """
        analyses = self.get_all_analyses_for_query(query_hash)
        return analyses[0] if analyses else None

    def get_analysis_by_id(self, query_hash: str, analysis_id: str) -> Optional[AnalysisResult]:
        """
        Get a specific analysis result by ID.

        Args:
            query_hash: Hash of the query
            analysis_id: ID of the analysis

        Returns:
            AnalysisResult or None if not found
        """
        payload = self._store.analysis_payload(query_hash, analysis_id)
        return _decode(payload) if payload is not None else None

    def get_stored_payload(
        self, query_hash: str, analysis_id: str
    ) -> Optional[Dict[str, Any]]:
        """Return one stored body for read-only redisplay.

        Every modeled field is present, so a body imported from an older
        release reads the same shape as one written today, and any field a
        newer build wrote rides along untouched.
        """
        stored = self._store.analysis_payload(query_hash, analysis_id)
        if stored is None:
            return None
        decoded = _decode(stored)
        return {**stored, **decoded.to_dict()} if decoded else stored

    def get_all_analyses_for_query(self, query_hash: str) -> List[AnalysisResult]:
        """
        Get all analysis results for a query, sorted by timestamp (newest first).

        Args:
            query_hash: Hash of the query

        Returns:
            List of AnalysisResult objects
        """
        results = []
        for payload in self._store.analysis_payloads(query_hash):
            decoded = _decode(payload)
            if decoded is not None:
                results.append(decoded)
        return results

    def list_analyzed_queries(self, limit: Optional[int] = None) -> List[str]:
        """
        List all query hashes that have analysis results.

        Args:
            limit: Maximum number of query hashes to return

        Returns:
            List of query hashes, sorted by most recent analysis
        """
        query_hashes = self._store.analyzed_query_hashes()
        if limit:
            query_hashes = query_hashes[:limit]
        return query_hashes

    def _next_analysis_id(self, query_hash: str) -> str:
        """Mint an id for a run that arrived without one.

        The timestamp prefix keeps ids sortable; the suffix counts this
        query's stored runs and steps past whatever the history already
        holds, so two runs in the same second stay distinct.
        """
        stored = {
            str(payload.get("analysis_id") or "")
            for payload in self._store.analysis_payloads(query_hash)
        }
        prefix = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_")
        for sequence in range(len(stored), len(stored) + 1000):
            candidate = f"{prefix}{sequence:03d}"
            if candidate not in stored:
                return candidate
        return f"{prefix}{len(stored):03d}"

    def get_analysis_summary(self, query_hash: str) -> Optional[Dict[str, Any]]:
        """
        Get a summary of all analyses for a query.

        Args:
            query_hash: Hash of the query

        Returns:
            Summary dict with analysis statistics
        """
        analyses = self.get_all_analyses_for_query(query_hash)
        if not analyses:
            return None

        latest = analyses[0]

        # Calculate performance trends
        execution_times = [a.performance_metrics.get('execution_time_ms', 0) for a in analyses]
        cost_estimates = [a.performance_metrics.get('cost_estimate', 0) for a in analyses]

        summary = {
            "query_hash": query_hash,
            "total_analyses": len(analyses),
            "first_analyzed": analyses[-1].timestamp if analyses else "",
            "last_analyzed": latest.timestamp,
            "targets_analyzed": list(set(a.target for a in analyses)),
            "database_engines": list(set(a.database_engine for a in analyses if a.database_engine)),
            "latest_performance": latest.performance_metrics,
            "has_rewrites": bool(latest.rewrite_suggestions),
            "has_index_suggestions": bool(latest.index_suggestions),
            "has_caching_recommendations": bool(latest.caching_recommendations),
            "performance_trend": {
                "execution_times": execution_times,
                "avg_execution_time": sum(execution_times) / len(execution_times) if execution_times else 0,
                "min_execution_time": min(execution_times) if execution_times else 0,
                "max_execution_time": max(execution_times) if execution_times else 0,
            }
        }

        return summary

    def remove_analyses_for_query(self, query_hash: str) -> int:
        """
        Remove all analysis results for a query.

        Args:
            query_hash: Hash of the query

        Returns:
            Number of analyses removed
        """
        return self._store.delete_analyses(query_hash)

    def cleanup_old_analyses(self, keep_per_query: int = ANALYSIS_HISTORY_LIMIT) -> int:
        """
        Clean up old analysis results, keeping only the most recent N per query.

        Storing already trims to ANALYSIS_HISTORY_LIMIT, so this only has
        work to do when asked for a tighter bound.

        Args:
            keep_per_query: Number of analyses to keep per query

        Returns:
            Number of analyses removed
        """
        return self._store.prune_analyses(keep_per_query)


def _queries_toml_path_for(analysis_path: Path) -> Path:
    """Return the queries.toml whose library store holds this history.

    Inverse of ``library_store.analysis_toml_path_for``, so a registry
    opened on a custom analysis filename lands on that registry's own
    store rather than the canonical one.
    """
    suffix = ".analysis_results.toml"
    if analysis_path.name == "analysis_results.toml":
        return analysis_path.with_name("queries.toml")
    if analysis_path.name.endswith(suffix):
        return analysis_path.with_name(analysis_path.name[: -len(suffix)])
    return analysis_path.with_name(analysis_path.name + ".queries.toml")


def _decode(payload: Dict[str, Any]) -> Optional[AnalysisResult]:
    """Decode a stored body, skipping (and reporting) one that is malformed."""
    try:
        return AnalysisResult.from_dict(dict(payload))
    except Exception as exc:
        logger.warning(
            "Skipping malformed analysis result %s/%s: %s",
            payload.get("query_hash"),
            payload.get("analysis_id"),
            exc,
        )
        return None


def extract_performance_assessment(llm_analysis: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract the compact performance assessment from a stored llm_analysis dict.

    Accepts both the top-level shape the analyze functions return and the
    nested shape under "analysis_results". Returns {} when absent.
    """
    assessment = llm_analysis.get("performance_assessment")
    if not isinstance(assessment, dict) or not assessment:
        nested = llm_analysis.get("analysis_results")
        assessment = nested.get("performance_assessment") if isinstance(nested, dict) else None
    return assessment if isinstance(assessment, dict) else {}


# Convenience functions for creating analysis results
def create_analysis_result(query_hash: str, target: str,
                          performance_metrics: Dict[str, Any],
                          llm_analysis: Dict[str, Any],
                          explain_plan: Dict[str, Any],
                          query_metrics: Dict[str, Any],
                          rewrite_suggestions: List[Dict[str, Any]] = None,
                          index_suggestions: List[Dict[str, Any]] = None,
                          caching_recommendations: Dict[str, Any] = None,
                          **kwargs) -> AnalysisResult:
    """
    Create an AnalysisResult object with current timestamp.

    Args:
        query_hash: Hash of the analyzed query
        target: Target database name
        performance_metrics: Performance metrics from EXPLAIN ANALYZE
        llm_analysis: Analysis results from LLM
        explain_plan: Raw EXPLAIN plan data
        query_metrics: Additional metrics from database telemetry
        rewrite_suggestions: Query rewrite suggestions
        index_suggestions: Index recommendations
        caching_recommendations: Readyset caching recommendations
        **kwargs: Additional metadata

    Returns:
        AnalysisResult object
    """
    timestamp = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

    return AnalysisResult(
        query_hash=query_hash,
        analysis_id="",  # Will be generated during storage
        target=target,
        timestamp=timestamp,
        performance_metrics=performance_metrics,
        llm_analysis=llm_analysis,
        explain_plan=explain_plan,
        query_metrics=query_metrics,
        rewrite_suggestions=rewrite_suggestions or [],
        index_suggestions=index_suggestions or [],
        caching_recommendations=caching_recommendations or {},
        rewrite_test_results=kwargs.get('rewrite_test_results'),
        database_engine=kwargs.get('database_engine', ''),
        analysis_duration_ms=kwargs.get('analysis_duration_ms', 0.0),
        llm_model_used=kwargs.get('llm_model_used', ''),
        tokens_used=kwargs.get('tokens_used', 0),
        display_payload=kwargs.get('display_payload') or {},
    )
