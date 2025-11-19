"""
Query Registry Module for RDST

This module provides query storage, retrieval, and management functionality for RDST.
Handles SQL normalization, hashing, and persistent storage in TOML format.

Key features:
- SQL normalization for consistent hashing
- TOML-based persistent storage (~/.rdst/queries.toml)
- Query metadata tracking (tags, timestamps, frequency)
- Hash-based and tag-based query lookup
- Analysis results storage with comprehensive performance data
- Rewrite suggestions and optimization recommendations tracking
"""

from .query_registry import (
    QueryRegistry,
    QueryEntry,
    ParameterSet,
    normalize_sql,
    hash_sql,
    extract_parameters_from_sql,
    reconstruct_query_with_params
)
from .analysis_results import (
    AnalysisResultsRegistry,
    AnalysisResult,
    create_analysis_result
)

__all__ = [
    "QueryRegistry",
    "QueryEntry",
    "ParameterSet",
    "normalize_sql",
    "hash_sql",
    "extract_parameters_from_sql",
    "reconstruct_query_with_params",
    "AnalysisResultsRegistry",
    "AnalysisResult",
    "create_analysis_result"
]