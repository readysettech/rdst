"""
Analysis Results Storage Extension for Query Registry

Extends the query registry to store comprehensive analysis results from
the RDST analyze workflow, including performance metrics, LLM insights,
and rewrite test results.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any
import toml

from .query_registry import QueryRegistry, hash_sql


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

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for TOML serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AnalysisResult':
        """Create AnalysisResult from dictionary (TOML deserialization)."""
        # Handle backward compatibility
        for key, default_value in [
            ('rewrite_test_results', None),
            ('database_engine', ''),
            ('analysis_duration_ms', 0.0),
            ('llm_model_used', ''),
            ('tokens_used', 0)
        ]:
            if key not in data:
                data[key] = default_value

        return cls(**data)


class AnalysisResultsRegistry:
    """
    Registry for storing and retrieving query analysis results.

    Stores results in TOML format at ~/.rdst/analysis_results.toml with structure:
    [results.{query_hash}.{analysis_id}]
    query_hash = "abc123def456"
    analysis_id = "20240115_103000_001"
    target = "production_db"
    timestamp = "2024-01-15T10:30:00Z"
    performance_metrics = {...}
    llm_analysis = {...}
    ...
    """

    def __init__(self, registry_path: Optional[str] = None):
        """
        Initialize the analysis results registry.

        Args:
            registry_path: Custom path to registry file. Defaults to ~/.rdst/analysis_results.toml
        """
        if registry_path:
            self.registry_path = Path(registry_path)
        else:
            self.registry_path = Path.home() / ".rdst" / "analysis_results.toml"

        # In-memory cache of analysis results
        self._results: Dict[str, Dict[str, AnalysisResult]] = {}  # {query_hash: {analysis_id: result}}
        self._loaded = False

    def _ensure_directory(self) -> None:
        """Ensure the registry directory exists."""
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> None:
        """Load analysis results from TOML file into memory."""
        if not self.registry_path.exists():
            self._results = {}
            self._loaded = True
            return

        try:
            with open(self.registry_path, 'r', encoding='utf-8') as f:
                data = toml.load(f)

            # Load results from TOML structure
            results_data = data.get('results', {})
            self._results = {}

            for query_hash, analyses in results_data.items():
                self._results[query_hash] = {}
                for analysis_id, analysis_data in analyses.items():
                    try:
                        self._results[query_hash][analysis_id] = AnalysisResult.from_dict(analysis_data)
                    except Exception as e:
                        print(f"Warning: Skipping malformed analysis result {query_hash}/{analysis_id}: {e}")
                        continue

            self._loaded = True

        except Exception as e:
            print(f"Warning: Could not load analysis results registry: {e}")
            self._results = {}
            self._loaded = True

    def save(self) -> None:
        """Save analysis results from memory to TOML file."""
        if not self._loaded:
            self.load()

        self._ensure_directory()

        # Convert to TOML structure
        toml_data = {
            'results': {}
        }

        for query_hash, analyses in self._results.items():
            toml_data['results'][query_hash] = {}
            for analysis_id, result in analyses.items():
                toml_data['results'][query_hash][analysis_id] = result.to_dict()

        try:
            with open(self.registry_path, 'w', encoding='utf-8') as f:
                toml.dump(toml_data, f)
        except Exception as e:
            raise RuntimeError(f"Failed to save analysis results registry: {e}")

    def store_analysis_result(self, query_hash: str, result: AnalysisResult) -> str:
        """
        Store an analysis result for a query.

        Args:
            query_hash: Hash of the analyzed query
            result: AnalysisResult object to store

        Returns:
            The analysis_id for the stored result
        """
        if not self._loaded:
            self.load()

        # Ensure query_hash exists in results
        if query_hash not in self._results:
            self._results[query_hash] = {}

        # Generate analysis_id if not provided
        if not result.analysis_id:
            timestamp = datetime.now(timezone.utc)
            result.analysis_id = timestamp.strftime("%Y%m%d_%H%M%S_") + f"{len(self._results[query_hash]):03d}"

        # Store the result
        self._results[query_hash][result.analysis_id] = result
        self.save()

        return result.analysis_id

    def get_latest_analysis(self, query_hash: str) -> Optional[AnalysisResult]:
        """
        Get the most recent analysis result for a query.

        Args:
            query_hash: Hash of the query

        Returns:
            Most recent AnalysisResult or None if not found
        """
        if not self._loaded:
            self.load()

        query_analyses = self._results.get(query_hash, {})
        if not query_analyses:
            return None

        # Find most recent analysis by timestamp
        latest_result = None
        latest_timestamp = ""

        for analysis_result in query_analyses.values():
            if analysis_result.timestamp > latest_timestamp:
                latest_timestamp = analysis_result.timestamp
                latest_result = analysis_result

        return latest_result

    def get_analysis_by_id(self, query_hash: str, analysis_id: str) -> Optional[AnalysisResult]:
        """
        Get a specific analysis result by ID.

        Args:
            query_hash: Hash of the query
            analysis_id: ID of the analysis

        Returns:
            AnalysisResult or None if not found
        """
        if not self._loaded:
            self.load()

        return self._results.get(query_hash, {}).get(analysis_id)

    def get_all_analyses_for_query(self, query_hash: str) -> List[AnalysisResult]:
        """
        Get all analysis results for a query, sorted by timestamp (newest first).

        Args:
            query_hash: Hash of the query

        Returns:
            List of AnalysisResult objects
        """
        if not self._loaded:
            self.load()

        query_analyses = self._results.get(query_hash, {})
        results = list(query_analyses.values())

        # Sort by timestamp, newest first
        results.sort(key=lambda r: r.timestamp, reverse=True)

        return results

    def list_analyzed_queries(self, limit: Optional[int] = None) -> List[str]:
        """
        List all query hashes that have analysis results.

        Args:
            limit: Maximum number of query hashes to return

        Returns:
            List of query hashes, sorted by most recent analysis
        """
        if not self._loaded:
            self.load()

        # Get all query hashes with their most recent analysis timestamps
        query_timestamps = []
        for query_hash, analyses in self._results.items():
            if analyses:
                latest_timestamp = max(result.timestamp for result in analyses.values())
                query_timestamps.append((query_hash, latest_timestamp))

        # Sort by timestamp, newest first
        query_timestamps.sort(key=lambda x: x[1], reverse=True)

        query_hashes = [qh for qh, _ in query_timestamps]

        if limit:
            query_hashes = query_hashes[:limit]

        return query_hashes

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
        if not self._loaded:
            self.load()

        if query_hash in self._results:
            count = len(self._results[query_hash])
            del self._results[query_hash]
            self.save()
            return count

        return 0

    def cleanup_old_analyses(self, keep_per_query: int = 10) -> int:
        """
        Clean up old analysis results, keeping only the most recent N per query.

        Args:
            keep_per_query: Number of analyses to keep per query

        Returns:
            Number of analyses removed
        """
        if not self._loaded:
            self.load()

        removed_count = 0

        for query_hash in list(self._results.keys()):
            analyses = self.get_all_analyses_for_query(query_hash)
            if len(analyses) > keep_per_query:
                # Keep only the most recent analyses
                to_keep = analyses[:keep_per_query]
                keep_ids = {a.analysis_id for a in to_keep}

                # Remove older analyses
                original_count = len(self._results[query_hash])
                self._results[query_hash] = {
                    aid: result for aid, result in self._results[query_hash].items()
                    if aid in keep_ids
                }
                removed_count += original_count - len(self._results[query_hash])

        if removed_count > 0:
            self.save()

        return removed_count


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
        caching_recommendations: ReadySet caching recommendations
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
        database_engine=kwargs.get('database_engine', ''),
        analysis_duration_ms=kwargs.get('analysis_duration_ms', 0.0),
        llm_model_used=kwargs.get('llm_model_used', ''),
        tokens_used=kwargs.get('tokens_used', 0)
    )