"""Analyze workflow function registry."""

from .explain_analysis import execute_explain_analyze
from .llm_analysis import analyze_with_llm, extract_rewrites
from .parallel_merge import merge_parallel_analysis_results
from .query_metrics import collect_query_metrics
from .shallow_analysis import analyze_shallow_with_llm
from .rewrite_testing import test_query_rewrites
from features.cache.readyset_cacheability import check_readyset_cacheability
from features.cache.readyset_workflow_functions import (
    DATABASE_SETUP_FUNCTIONS,
    READYSET_FUNCTIONS,
)
from features.schema.schema_collector import collect_target_schema
from features.schema.schema_from_yaml import collect_schema_from_yaml
from .workflow_integration import (
    enforce_query_safety,
    format_analysis_output,
    store_analysis_results,
)
from shared.query_parameterization import normalize_for_registry, parameterize_for_llm

ANALYZE_WORKFLOW_FUNCTIONS = {
    "validate_query_safety": enforce_query_safety,
    "parameterize_for_llm": parameterize_for_llm,
    "normalize_for_registry": normalize_for_registry,
    "execute_explain_analyze": execute_explain_analyze,
    "collect_query_metrics": collect_query_metrics,
    "collect_target_schema": collect_target_schema,
    "analyze_with_llm": analyze_with_llm,
    "extract_rewrites": extract_rewrites,
    "test_query_rewrites": test_query_rewrites,
    "check_readyset_cacheability": check_readyset_cacheability,
    "store_analysis_results": store_analysis_results,
    "format_analysis_output": format_analysis_output,
    "merge_parallel_analysis_results": merge_parallel_analysis_results,
    "collect_schema_from_yaml": collect_schema_from_yaml,
    "analyze_shallow_with_llm": analyze_shallow_with_llm,
}

__all__ = [
    "ANALYZE_WORKFLOW_FUNCTIONS",
    "DATABASE_SETUP_FUNCTIONS",
    "READYSET_FUNCTIONS",
]
