"""
Workflow Integration Functions for RDST Analyze

Additional workflow functions for storing analysis results and formatting output.
These functions bridge between the workflow execution and the query registry systems.
"""

import time
from typing import Dict, Any, Optional
from ..query_registry.query_registry import (
    QueryRegistry,
    hash_sql
)
from .validation import validate_recommendations


def store_analysis_results(**kwargs) -> Dict[str, Any]:
    """
    Store query analysis results in the query registry.

    Args:
        **kwargs: All workflow context data

    Returns:
        Dict containing:
        - success: boolean indicating storage success
        - query_hash: hash of the analyzed query
        - analysis_id: ID of the stored analysis (using query hash)
        - error: error message if failed
    """
    try:
        # Extract required data from workflow context
        query = kwargs.get('query', '')
        target = kwargs.get('target', '')
        registry_normalization = kwargs.get('registry_normalization', {})

        if not query or not target:
            return {
                "success": False,
                "error": "Missing required parameters: query or target",
                "query_hash": "",
                "analysis_id": None
            }

        # Store/update query in query registry using the original SQL
        # Skip registry storage for large_query_bypass (queries > 1KB)
        source = kwargs.get('source', 'analyze')
        if source == 'large_query_bypass':
            # Large queries are not saved to registry - just generate the hash
            from ..query_registry.query_registry import hash_sql
            stored_hash = hash_sql(query)
            return {
                "success": True,
                "query_hash": stored_hash,
                "analysis_id": stored_hash,
                "is_new_query": True,
                "message": f"Query analyzed (hash: {stored_hash}). Large queries are not saved to registry."
            }

        query_registry = QueryRegistry()

        # Use the original SQL (not normalized) for proper parameter extraction
        stored_hash, is_new = query_registry.add_query(
            sql=query,  # Use original SQL with actual values
            tag=kwargs.get('save_as', ''),
            source=source,
            target=target
        )

        return {
            "success": True,
            "query_hash": stored_hash,
            "analysis_id": stored_hash,  # Use hash as analysis ID for now
            "is_new_query": is_new,
            "message": f"Query stored in registry with hash: {stored_hash}"
        }

    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to store analysis results: {str(e)}",
            "query_hash": "",
            "analysis_id": None
        }


def format_analysis_output(**kwargs) -> Dict[str, Any]:
    """
    Format the final analysis output for user presentation.

    Args:
        **kwargs: All workflow context data

    Returns:
        Dict containing formatted output for the user
    """
    try:
        # Extract key results
        explain_results = kwargs.get('explain_results', {})
        llm_analysis = kwargs.get('llm_analysis', {})
        optimization_suggestions = kwargs.get('optimization_suggestions', {})
        rewrite_test_results = kwargs.get('rewrite_test_results')
        readyset_cacheability = kwargs.get('readyset_cacheability', {})
        readyset_explain_cache = kwargs.get('readyset_explain_cache', {})
        query_metrics = kwargs.get('query_metrics', {})
        query = kwargs.get('query', '')
        target = kwargs.get('target', '')
        analysis_id = kwargs.get('analysis_id', '')

        # Validate recommendations to detect hallucination
        schema_collection = kwargs.get('schema_collection', {})
        schema_info = schema_collection.get('schema_info', '')
        validation_results = validate_recommendations(llm_analysis, schema_info)

        # Build the formatted output
        output = {
            "analysis_summary": _format_analysis_summary(explain_results, llm_analysis),
            "performance_metrics": _format_performance_metrics(explain_results, query_metrics),
            "optimization_insights": _format_optimization_insights(llm_analysis),
            "recommendations": _format_recommendations(optimization_suggestions),
            "readyset_cacheability": _format_readyset_cacheability(readyset_cacheability, readyset_explain_cache),
            "validation": validation_results,  # Add validation results
            "metadata": {
                "query": query,
                "normalized_query": kwargs.get('normalized_query', ''),  # Privacy-safe version
                "parameterized_sql": kwargs.get('parameterized_sql', ''),  # Privacy-safe version
                "target": target,
                "analysis_id": analysis_id,
                "database_engine": explain_results.get('database_engine', ''),
                "analyzed_at": explain_results.get('timestamp', ''),
                "llm_model": llm_analysis.get('llm_model', '')
            }
        }

        # Add rewrite test results if available
        if rewrite_test_results and rewrite_test_results.get('success'):
            output["rewrite_testing"] = _format_rewrite_testing(rewrite_test_results)

        # Add success indicators
        output["success"] = True
        output["message"] = f"Query analysis completed successfully (ID: {analysis_id})"

        return output

    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to format analysis output: {str(e)}",
            "raw_data": {
                "explain_results": kwargs.get('explain_results', {}),
                "llm_analysis": kwargs.get('llm_analysis', {}),
                "optimization_suggestions": kwargs.get('optimization_suggestions', {})
            }
        }


def _format_analysis_summary(explain_results: Dict[str, Any],
                           llm_analysis: Dict[str, Any]) -> Dict[str, Any]:
    """Format high-level analysis summary."""
    # Performance rating from LLM analysis
    analysis_data = llm_analysis.get('analysis_results', {})
    perf_assessment = analysis_data.get('performance_assessment', {})

    return {
        "overall_rating": perf_assessment.get('overall_rating', 'unknown'),
        "execution_time_rating": perf_assessment.get('execution_time_rating', 'unknown'),
        "efficiency_score": perf_assessment.get('efficiency_score', 0),
        "execution_time_ms": explain_results.get('execution_time_ms', 0),
        "rows_processed": {
            "examined": explain_results.get('rows_examined', 0),
            "returned": explain_results.get('rows_returned', 0)
        },
        "cost_estimate": explain_results.get('cost_estimate', 0),
        "primary_concerns": perf_assessment.get('primary_concerns', [])
    }


def _format_performance_metrics(explain_results: Dict[str, Any],
                               query_metrics: Dict[str, Any]) -> Dict[str, Any]:
    """Format detailed performance metrics."""
    metrics = {
        "execution_metrics": {
            "total_time_ms": explain_results.get('execution_time_ms', 0),
            "planning_time_ms": explain_results.get('planning_time_ms', 0),
            "actual_time_ms": explain_results.get('actual_time_ms', 0),
            "rows_examined": explain_results.get('rows_examined', 0),
            "rows_returned": explain_results.get('rows_returned', 0),
            "cost_estimate": explain_results.get('cost_estimate', 0)
        },
        "database_engine": explain_results.get('database_engine', ''),
        "explain_available": explain_results.get('success', False)
    }

    # Add database-specific metrics if available
    db_metrics = query_metrics.get('metrics', {})
    if db_metrics:
        metrics["database_telemetry"] = {
            "source": query_metrics.get('source', ''),
            "available_sources": query_metrics.get('available_sources', []),
            "metrics_collected": len(db_metrics),
            "sample_metrics": dict(list(db_metrics.items())[:10])  # First 10 metrics
        }

    return metrics


def _format_optimization_insights(llm_analysis: Dict[str, Any]) -> Dict[str, Any]:
    """Format LLM optimization insights."""
    if not llm_analysis.get('success', False):
        return {
            "available": False,
            "error": llm_analysis.get('error', 'LLM analysis failed'),
            "explanation": "Optimization insights not available due to LLM analysis failure"
        }

    analysis_data = llm_analysis.get('analysis_results', {})
    execution_analysis = analysis_data.get('execution_analysis', {})

    return {
        "available": True,
        "explanation": analysis_data.get('explanation', ''),
        "expensive_operations": execution_analysis.get('most_expensive_operations', []),
        "scan_analysis": execution_analysis.get('scan_analysis', {}),
        "join_analysis": execution_analysis.get('join_analysis', {}),
        "optimization_opportunities": analysis_data.get('optimization_opportunities', [])
    }


def _format_recommendations(optimization_suggestions: Dict[str, Any]) -> Dict[str, Any]:
    """Format optimization recommendations."""
    if not optimization_suggestions.get('success', False):
        return {
            "available": False,
            "error": optimization_suggestions.get('error', 'Optimization suggestions failed'),
            "query_rewrites": [],
            "index_suggestions": [],
            "caching_recommendations": {}
        }

    return {
        "available": True,
        "query_rewrites": _format_rewrite_suggestions(optimization_suggestions.get('rewrite_suggestions', [])),
        "index_suggestions": _format_index_suggestions(optimization_suggestions.get('index_suggestions', [])),
        "caching_recommendations": optimization_suggestions.get('caching_recommendations', {})
    }


def _format_rewrite_suggestions(rewrites: list) -> list:
    """Format query rewrite suggestions for user display."""
    formatted = []
    for rewrite in rewrites:
        formatted_rewrite = {
            "id": rewrite.get('suggestion_id', ''),
            "type": rewrite.get('type', ''),
            "priority": rewrite.get('priority', ''),
            "confidence": rewrite.get('confidence', ''),
            "sql": rewrite.get('rewritten_sql', ''),
            "explanation": rewrite.get('explanation', ''),
            "expected_improvement": rewrite.get('expected_improvement', ''),
            "trade_offs": rewrite.get('trade_offs', ''),
            "requires_testing": rewrite.get('test_recommended', True)
        }
        formatted.append(formatted_rewrite)
    return formatted


def _format_index_suggestions(indexes: list) -> list:
    """Format index suggestions for user display."""
    formatted = []
    for index in indexes:
        formatted_index = {
            "id": index.get('recommendation_id', ''),
            "table": index.get('table', ''),
            "type": index.get('index_type', ''),
            "columns": index.get('columns', []),
            "sql_statement": index.get('sql_statement', ''),
            "expected_benefit": index.get('estimated_benefit', ''),
            "rationale": index.get('rationale', ''),
            "maintenance_cost": index.get('maintenance_cost', ''),
            "storage_impact": index.get('storage_impact', '')
        }
        formatted.append(formatted_index)
    return formatted


def _format_readyset_cacheability(cacheability_results: Dict[str, Any],
                                   readyset_explain_cache: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Format ReadySet cacheability check results.

    Args:
        cacheability_results: Merged cacheability results (from parallel_merge)
        readyset_explain_cache: Raw EXPLAIN CREATE CACHE results from ReadySet container

    Returns:
        Formatted cacheability information
    """
    if not cacheability_results:
        return {
            "checked": False,
            "note": "Cacheability check not performed"
        }

    # Extract final verdict from merged results
    final_verdict = cacheability_results.get('final_verdict', {})
    readyset_container = cacheability_results.get('readyset_container', {})

    # Format the output
    result = {
        "checked": True,
        "cacheable": final_verdict.get('cacheable', False),
        "confidence": final_verdict.get('confidence', 'unknown'),
        "method": final_verdict.get('method', 'unknown'),  # 'readyset_container' or 'static_analysis'
    }

    # If we have real ReadySet EXPLAIN CREATE CACHE results, include them
    if readyset_explain_cache and readyset_explain_cache.get('success'):
        result["readyset_tested"] = True
        result["readyset_container_status"] = readyset_container.get('status', False)
        result["readyset_container_name"] = readyset_container.get('container_name', '')
        result["explain_output"] = readyset_explain_cache.get('explain_output', '')
        result["create_cache_command"] = readyset_explain_cache.get('create_cache_command', '')
        result["warnings"] = readyset_explain_cache.get('warnings', [])
        result["explanation"] = readyset_explain_cache.get('explanation', '')
    else:
        # Fall back to static analysis results
        result["readyset_tested"] = False
        static_analysis = cacheability_results.get('static_analysis', {})
        result["explanation"] = static_analysis.get('explanation', '')
        result["issues"] = static_analysis.get('issues', [])
        result["warnings"] = static_analysis.get('warnings', [])
        result["recommended_options"] = static_analysis.get('recommended_options', {})

    return result


def _format_rewrite_testing(rewrite_test_results: Dict[str, Any]) -> Dict[str, Any]:
    """Format rewrite testing results for user display."""
    # Check if testing was skipped due to parameterized query (no actual values)
    if rewrite_test_results.get('skipped_reason') == 'parameterized_query':
        return {
            "tested": False,
            "skipped_reason": "parameterized_query",
            "message": rewrite_test_results.get('message', ''),
            "recommendations": rewrite_test_results.get('recommendations', ''),
            "results": []
        }

    if not rewrite_test_results.get('success', False):
        return {
            "tested": False,
            "error": rewrite_test_results.get('error', 'Rewrite testing failed'),
            "results": []
        }

    return {
        "tested": True,
        "summary": rewrite_test_results.get('testing_summary', ''),
        "best_rewrite": rewrite_test_results.get('best_rewrite'),
        "recommendations": rewrite_test_results.get('recommendations', ''),
        "original_performance": rewrite_test_results.get('original_performance', {}),
        "rewrite_results": rewrite_test_results.get('rewrite_results', []),
        "performance_comparison": rewrite_test_results.get('performance_comparison', '')
    }