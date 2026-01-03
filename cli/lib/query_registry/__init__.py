"""
Query Registry Module for RDST

This module provides query storage, retrieval, and management functionality for RDST.
Handles SQL normalization, hashing, and persistent storage in TOML format.

Key features:
- SQL normalization using SQLGlot for robust AST-based parsing
- Named parameter placeholders (:p1, :p2, etc.) with type information
- TOML-based persistent storage (~/.rdst/queries.toml)
- Query metadata tracking (tags, timestamps, frequency)
- Hash-based and tag-based query lookup
- Analysis results storage with comprehensive performance data
- Rewrite suggestions and optimization recommendations tracking
"""

from .query_registry import (
    QueryRegistry,
    QueryEntry,
    normalize_sql,
    hash_sql,
    generate_query_name,
    # Legacy functions - kept for backward compatibility
    extract_parameters_from_sql,
    reconstruct_query_with_params
)
from .sql_normalizer import (
    normalize_and_extract,
    reconstruct_sql,
    get_placeholder_names
)
from .analysis_results import (
    AnalysisResultsRegistry,
    AnalysisResult,
    create_analysis_result
)

__all__ = [
    # Registry classes
    "QueryRegistry",
    "QueryEntry",
    # Core functions
    "normalize_sql",
    "hash_sql",
    "generate_query_name",
    # SQLGlot-based parameterization (preferred)
    "normalize_and_extract",
    "reconstruct_sql",
    "get_placeholder_names",
    # Legacy functions (backward compatibility)
    "extract_parameters_from_sql",
    "reconstruct_query_with_params",
    # Analysis results
    "AnalysisResultsRegistry",
    "AnalysisResult",
    "create_analysis_result"
]