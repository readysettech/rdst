"""
Functions Library for RDST Analyze Workflow

This module provides a collection of workflow functions that implement the RDST
analyze command functionality. All functions follow the workflow manager pattern:
- Accept JSON input through **kwargs
- Return JSON-serializable Dict[str, Any]
- Handle errors gracefully with appropriate error codes
- Support the dual parameterization strategy for query safety and registry consistency
"""

from .query_safety import validate_query_safety
from .query_parameterization import parameterize_for_llm, normalize_for_registry
from .explain_analysis import execute_explain_analyze
from .query_metrics import collect_query_metrics
from .schema_collector import collect_target_schema
from .llm_analysis import analyze_with_llm, extract_rewrites
from .rewrite_testing import test_query_rewrites
from .readyset_cacheability import check_readyset_cacheability
from .readyset_setup import check_container_needs_start
from .readyset_container import (
    start_readyset_container,
    wait_for_readyset_ready,
    check_readyset_container_status
)
from .readyset_explain_cache import explain_create_cache_readyset
from .target_setup import (
    get_target_config,
    detect_test_db_container,
    start_test_db_container,
    wait_for_database_ready,
    recreate_schema_from_target,
    create_test_db_target_config,
    return_target_config
)
from .test_data_generator import (
    check_tables_have_data,
    get_test_database_schema,
    generate_test_data_with_llm,
    load_test_data_to_database
)
from .workflow_integration import store_analysis_results, format_analysis_output
from .parallel_merge import merge_parallel_analysis_results

ANALYZE_WORKFLOW_FUNCTIONS = {
    "validate_query_safety": validate_query_safety,
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
}

DATABASE_SETUP_FUNCTIONS = {
    "get_target_config": get_target_config,
    "detect_test_db_container": detect_test_db_container,
    "start_test_db_container": start_test_db_container,
    "wait_for_database_ready": wait_for_database_ready,
    "recreate_schema_from_target": recreate_schema_from_target,
    "create_test_db_target_config": create_test_db_target_config,
    "return_target_config": return_target_config,
    "check_container_needs_start": check_container_needs_start,
    "check_tables_have_data": check_tables_have_data,
    "get_test_database_schema": get_test_database_schema,
    "generate_test_data_with_llm": generate_test_data_with_llm,
    "load_test_data_to_database": load_test_data_to_database,
}

READYSET_FUNCTIONS = {
    "start_readyset_container": start_readyset_container,
    "wait_for_readyset_ready": wait_for_readyset_ready,
    "check_readyset_container_status": check_readyset_container_status,
    "explain_create_cache_readyset": explain_create_cache_readyset,
}