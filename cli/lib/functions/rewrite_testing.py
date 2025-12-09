"""
Query Rewrite Testing and Performance Comparison

Automatically tests LLM-suggested query rewrites by executing them
and comparing performance metrics with the original query.
"""

import re
import time
import json
from typing import Dict, Any, List, Optional
from .explain_analysis import execute_explain_analyze
from .query_safety import validate_query_safety


def _has_unresolved_placeholders(sql: str) -> bool:
    """
    Check if SQL contains parameter placeholders that haven't been substituted.

    This happens when:
    - Query comes from rdst top with PostgreSQL prepared statements ($1, $2)
    - Query comes from MySQL performance_schema digest (?)
    - Query was normalized but we don't have stored parameter values

    Args:
        sql: SQL query string to check

    Returns:
        True if query contains unresolved placeholders
    """
    if not sql:
        return False

    # PostgreSQL-style: $1, $2, etc.
    if re.search(r'\$\d+', sql):
        return True

    # MySQL/generic style: ? (but not inside quotes)
    # Simple check - if ? appears outside of string literals
    # Remove string literals first
    sql_no_strings = re.sub(r"'[^']*'", '', sql)
    if '?' in sql_no_strings:
        return True

    return False


def test_query_rewrites(original_sql: str, rewrite_suggestions: List[Dict[str, Any]],
                       target: str = None, **kwargs) -> Dict[str, Any]:
    """
    Test query rewrites by executing EXPLAIN ANALYZE and comparing performance.

    Args:
        original_sql: The original SQL query
        rewrite_suggestions: List of suggested rewrites from LLM analysis
        target: Target database configuration name
        **kwargs: Additional workflow parameters including target_config, baseline_result

    Returns:
        Dict containing:
        - success: boolean indicating if testing succeeded
        - original_performance: performance metrics for original query
        - rewrite_results: list of test results for each rewrite
        - best_rewrite: the best performing rewrite (if any)
        - recommendations: final recommendations based on testing
    """
    try:
        # Check if query has unresolved parameter placeholders ($1, $2, ?)
        # This happens when query comes from rdst top with prepared statements
        # or from MySQL digest without stored parameter values
        if _has_unresolved_placeholders(original_sql):
            return {
                "success": True,
                "tested": False,
                "skipped_reason": "parameterized_query",
                "original_performance": None,
                "rewrite_results": [],
                "best_rewrite": None,
                "recommendations": "Rewrite testing skipped - query contains parameter placeholders",
                "message": (
                    "This query contains parameter placeholders ($1, $2 or ?) without actual values. "
                    "This typically happens when the query was captured from rdst top using prepared statements. "
                    "To test rewrites, run 'rdst analyze' with the original query from your application code "
                    "that includes actual parameter values."
                )
            }

        # Handle case where WorkflowManager passes rewrite_suggestions as string
        if isinstance(rewrite_suggestions, str):
            # Try to parse as JSON if it's a string
            try:
                import json
                rewrite_suggestions = json.loads(rewrite_suggestions)
            except (json.JSONDecodeError, TypeError):
                # If parsing fails, create empty list
                rewrite_suggestions = []

        if not rewrite_suggestions:
            return {
                "success": True,
                "original_performance": None,
                "rewrite_results": [],
                "best_rewrite": None,
                "recommendations": "No rewrite suggestions to test",
                "message": "No rewrites provided for testing"
            }

        # Get baseline performance - use provided baseline_result if available, otherwise run it
        baseline_result_param = kwargs.get('baseline_result')

        # WorkflowManager passes this as a JSON string, so parse it
        if isinstance(baseline_result_param, str):
            try:
                baseline_result_param = json.loads(baseline_result_param)
            except (json.JSONDecodeError, TypeError):
                baseline_result_param = None

        if baseline_result_param and isinstance(baseline_result_param, dict) and baseline_result_param.get('success'):
            # Use the provided baseline result from ExecuteExplainAnalyze step (no need to run original query again)
            baseline_result = _convert_explain_result_to_baseline(baseline_result_param, original_sql)
        else:
            # Fall back to running baseline (for backwards compatibility)
            baseline_result = _get_baseline_performance(original_sql, target, kwargs)

        if not baseline_result['success']:
            return {
                "success": False,
                "error": f"Failed to get baseline performance: {baseline_result.get('error')}",
                "original_performance": None,
                "rewrite_results": []
            }

        # Check if baseline EXPLAIN ANALYZE was skipped/timed out
        baseline_skipped = baseline_result.get('explain_analyze_skipped') or baseline_result.get('explain_analyze_timeout')

        # Test each rewrite suggestion
        rewrite_results = []
        for i, suggestion in enumerate(rewrite_suggestions[:5]):  # Limit to 5 rewrites for safety
            # Don't print rewrite details during workflow - keeps progress on one line
            # The final report will show the tested rewrites
            rewrite_sql = suggestion.get('rewritten_sql', 'Unknown query')

            rewrite_result = _test_single_rewrite(
                suggestion, baseline_result, target, kwargs, i
            )
            rewrite_results.append(rewrite_result)

        # Analyze results and find best rewrite
        analysis = _analyze_rewrite_results(baseline_result, rewrite_results, baseline_skipped)

        return {
            "success": True,
            "original_performance": baseline_result['performance'],
            "rewrite_results": rewrite_results,
            "best_rewrite": analysis['best_rewrite'],
            "performance_comparison": analysis['comparison'],
            "recommendations": analysis['recommendations'],
            "testing_summary": analysis['summary'],
            "baseline_skipped": baseline_skipped,
            "baseline_skip_reason": baseline_result.get('skip_reason') or baseline_result.get('fallback_reason')
        }

    except Exception as e:
        return {
            "success": False,
            "error": f"Rewrite testing failed: {str(e)}",
            "original_performance": None,
            "rewrite_results": []
        }


def _convert_explain_result_to_baseline(explain_result: Dict[str, Any], original_sql: str) -> Dict[str, Any]:
    """Convert explain_result from ExecuteExplainAnalyze step to baseline_result format."""
    return {
        "success": True,
        "performance": {
            "execution_time_ms": explain_result.get('execution_time_ms', 0),
            "rows_examined": explain_result.get('rows_examined', 0),
            "rows_returned": explain_result.get('rows_returned', 0),
            "cost_estimate": explain_result.get('cost_estimate', 0),
            "explain_plan": explain_result.get('explain_plan')
        },
        "sql": original_sql,
        "explain_analyze_skipped": explain_result.get('explain_analyze_skipped', False),
        "explain_analyze_timeout": explain_result.get('explain_analyze_timeout', False),
        "fallback_reason": explain_result.get('fallback_reason'),
        "skip_reason": explain_result.get('skip_reason'),
        "actual_elapsed_time_ms": explain_result.get('actual_elapsed_time_ms')
    }


def _get_baseline_performance(original_sql: str, target: str, kwargs: Dict) -> Dict[str, Any]:
    """Get baseline performance metrics for the original query."""
    try:
        # Validate original query safety
        safety_check = validate_query_safety(original_sql)
        if not safety_check.get('safe', False):
            return {
                "success": False,
                "error": f"Original query failed safety validation: {', '.join(safety_check.get('issues', []))}",
                "performance": None
            }

        # Execute EXPLAIN ANALYZE on original query
        explain_result = execute_explain_analyze(
            sql=original_sql,
            target=target,
            target_config=kwargs.get('target_config'),
            fast_mode=kwargs.get('fast_mode', False)
        )

        if not explain_result.get('success', False):
            return {
                "success": False,
                "error": f"EXPLAIN ANALYZE failed for original query: {explain_result.get('error')}",
                "performance": None
            }

        return {
            "success": True,
            "performance": {
                "execution_time_ms": explain_result.get('execution_time_ms', 0),
                "rows_examined": explain_result.get('rows_examined', 0),
                "rows_returned": explain_result.get('rows_returned', 0),
                "cost_estimate": explain_result.get('cost_estimate', 0),
                "explain_plan": explain_result.get('explain_plan')
            },
            "sql": original_sql,
            "explain_analyze_skipped": explain_result.get('explain_analyze_skipped', False),
            "explain_analyze_timeout": explain_result.get('explain_analyze_timeout', False),
            "fallback_reason": explain_result.get('fallback_reason'),
            "skip_reason": explain_result.get('skip_reason'),
            "actual_elapsed_time_ms": explain_result.get('actual_elapsed_time_ms')
        }

    except Exception as e:
        return {
            "success": False,
            "error": f"Baseline performance collection failed: {str(e)}",
            "performance": None
        }


def _test_single_rewrite(suggestion: Dict[str, Any], baseline_result: Dict[str, Any],
                        target: str, kwargs: Dict, index: int) -> Dict[str, Any]:
    """Test a single rewrite suggestion.

    Uses baseline execution time to set a timeout for rewrite testing.
    If rewrite takes longer than baseline, it's cancelled and marked as slower.
    """
    try:
        suggestion_id = suggestion.get('suggestion_id', f'rewrite_{index}')
        rewritten_sql = suggestion.get('rewritten_sql', '').strip()

        if not rewritten_sql:
            return {
                "suggestion_id": suggestion_id,
                "success": False,
                "error": "No rewritten SQL provided",
                "performance": None,
                "improvement": None,
                "recommendation": "skip"
            }

        # Check if this is a DDL suggestion (advisory) vs SELECT rewrite (executable)
        sql_upper = rewritten_sql.upper().strip()
        is_ddl_advisory = (sql_upper.startswith('CREATE ') or
                          sql_upper.startswith('ALTER ') or
                          sql_upper.startswith('DROP '))

        if is_ddl_advisory:
            # This is an advisory suggestion (like CREATE INDEX) - show it but don't execute
            # Provide empty improvement structure to avoid NoneType errors
            empty_improvement = {
                "execution_time": {"improvement_pct": 0, "is_better": False},
                "cost_estimate": {"improvement_pct": 0, "is_better": False},
                "rows_examined": {"improvement_pct": 0, "is_better": False},
                "overall": {"improvement_pct": 0, "is_better": False, "significant": False}
            }
            return {
                "suggestion_id": suggestion_id,
                "success": True,
                "sql": rewritten_sql,
                "performance": None,
                "improvement": empty_improvement,
                "recommendation": "advisory_ddl",
                "is_advisory": True,
                "suggestion_metadata": {
                    "type": suggestion.get('optimization_type', 'DDL Advisory'),
                    "priority": suggestion.get('priority'),
                    "confidence": suggestion.get('confidence'),
                    "explanation": suggestion.get('explanation'),
                    "expected_improvement": suggestion.get('expected_improvement')
                },
                "message": "Advisory suggestion - not executed for safety"
            }

        # For executable queries (SELECT), validate safety
        safety_check = validate_query_safety(rewritten_sql)
        if not safety_check.get('safe', False):
            return {
                "suggestion_id": suggestion_id,
                "success": False,
                "error": f"Rewrite failed safety validation: {', '.join(safety_check.get('issues', []))}",
                "safety_issues": safety_check.get('issues', []),
                "performance": None,
                "improvement": None,
                "recommendation": "reject_unsafe"
            }

        # Calculate timeout for rewrite testing
        # Allow rewrites to run up to 30 seconds to measure actual performance
        # If original took >30s, stop when rewrite exceeds original time
        baseline_perf = baseline_result.get('performance', {})
        baseline_time_ms = baseline_perf.get('execution_time_ms', 0) if baseline_perf else 0

        # 30 second cap - if baseline < 30s, allow up to 30s; if baseline > 30s, use baseline time
        MAX_REWRITE_TIME_MS = 30000  # 30 seconds
        used_30s_cap = baseline_time_ms < MAX_REWRITE_TIME_MS
        if used_30s_cap:
            rewrite_max_time_ms = MAX_REWRITE_TIME_MS
        else:
            rewrite_max_time_ms = baseline_time_ms

        # Execute EXPLAIN ANALYZE on rewrite with timeout
        explain_result = execute_explain_analyze(
            sql=rewritten_sql,
            target=target,
            target_config=kwargs.get('target_config'),
            fast_mode=kwargs.get('fast_mode', False),
            rewrite_max_time_ms=rewrite_max_time_ms
        )

        if not explain_result.get('success', False):
            return {
                "suggestion_id": suggestion_id,
                "success": False,
                "error": f"EXPLAIN ANALYZE failed for rewrite: {explain_result.get('error')}",
                "sql": rewritten_sql,
                "performance": None,
                "improvement": None,
                "recommendation": "execution_failed"
            }

        # Compare performance with baseline
        # Check if this rewrite timed out (slower than baseline)
        rewrite_timeout_exceeded = explain_result.get('rewrite_timeout_exceeded', False)
        rewrite_skipped = explain_result.get('explain_analyze_skipped', False) or explain_result.get('explain_analyze_timeout', False)
        actual_elapsed = explain_result.get('actual_elapsed_time_ms', explain_result.get('execution_time_ms', 0))

        rewrite_performance = {
            "execution_time_ms": explain_result.get('execution_time_ms', 0),
            "actual_elapsed_time_ms": actual_elapsed,
            "rows_examined": explain_result.get('rows_examined', 0),
            "rows_returned": explain_result.get('rows_returned', 0),
            "cost_estimate": explain_result.get('cost_estimate', 0),
            "was_skipped": rewrite_skipped,
            "rewrite_timeout_exceeded": rewrite_timeout_exceeded,
            "skip_reason": explain_result.get('skip_reason')
        }

        # If rewrite timed out, mark as reject_slower with appropriate message
        if rewrite_timeout_exceeded:
            # Determine the appropriate message based on whether we hit 30s cap or exceeded baseline
            if used_30s_cap:
                timeout_message = "Rewrite cancelled - stopped after 30 seconds"
            else:
                timeout_message = f"Rewrite cancelled - exceeded original query time ({baseline_time_ms/1000:.1f}s). Actual time unknown."

            empty_improvement = {
                "execution_time": {"improvement_pct": -100, "is_better": False},
                "cost_estimate": {"improvement_pct": 0, "is_better": False},
                "rows_examined": {"improvement_pct": 0, "is_better": False},
                "overall": {"improvement_pct": -100, "is_better": False, "significant": True}
            }
            return {
                "suggestion_id": suggestion_id,
                "success": True,
                "sql": rewritten_sql,
                "performance": rewrite_performance,
                "improvement": empty_improvement,
                "recommendation": "reject_slower",
                "was_skipped": True,
                "rewrite_timeout_exceeded": True,
                "skip_reason": explain_result.get('skip_reason', 'Rewrite slower than baseline'),
                "suggestion_metadata": {
                    "type": suggestion.get('optimization_type'),
                    "priority": suggestion.get('priority'),
                    "confidence": suggestion.get('confidence'),
                    "explanation": suggestion.get('explanation'),
                    "expected_improvement": suggestion.get('expected_improvement')
                },
                "message": timeout_message
            }

        improvement = _calculate_improvement(
            baseline_result['performance'],
            rewrite_performance
        )

        # Determine recommendation based on improvement
        recommendation = _determine_rewrite_recommendation(improvement, suggestion)

        return {
            "suggestion_id": suggestion_id,
            "success": True,
            "sql": rewritten_sql,
            "performance": rewrite_performance,
            "improvement": improvement,
            "recommendation": recommendation,
            "was_skipped": rewrite_skipped,
            "skip_reason": explain_result.get('skip_reason'),
            "suggestion_metadata": {
                "type": suggestion.get('optimization_type'),
                "priority": suggestion.get('priority'),
                "confidence": suggestion.get('confidence'),
                "explanation": suggestion.get('explanation'),
                "expected_improvement": suggestion.get('expected_improvement')
            }
        }

    except Exception as e:
        return {
            "suggestion_id": suggestion.get('suggestion_id', f'rewrite_{index}'),
            "success": False,
            "error": f"Rewrite testing failed: {str(e)}",
            "performance": None,
            "improvement": None,
            "recommendation": "testing_error"
        }


def _calculate_improvement(baseline: Dict[str, Any], rewrite: Dict[str, Any]) -> Dict[str, Any]:
    """Calculate performance improvement metrics."""
    improvement = {}

    # Time improvement
    baseline_time = baseline.get('execution_time_ms', 1)  # Avoid division by zero
    rewrite_time = rewrite.get('execution_time_ms', 1)

    if baseline_time > 0:
        time_improvement_pct = ((baseline_time - rewrite_time) / baseline_time) * 100
        improvement['execution_time'] = {
            "baseline_ms": baseline_time,
            "rewrite_ms": rewrite_time,
            "improvement_pct": round(time_improvement_pct, 2),
            "is_better": time_improvement_pct > 0
        }
    else:
        improvement['execution_time'] = {
            "baseline_ms": baseline_time,
            "rewrite_ms": rewrite_time,
            "improvement_pct": 0,
            "is_better": False
        }

    # Cost improvement
    baseline_cost = baseline.get('cost_estimate', 0)
    rewrite_cost = rewrite.get('cost_estimate', 0)

    if baseline_cost > 0:
        cost_improvement_pct = ((baseline_cost - rewrite_cost) / baseline_cost) * 100
        improvement['cost_estimate'] = {
            "baseline_cost": baseline_cost,
            "rewrite_cost": rewrite_cost,
            "improvement_pct": round(cost_improvement_pct, 2),
            "is_better": cost_improvement_pct > 0
        }
    else:
        improvement['cost_estimate'] = {
            "baseline_cost": baseline_cost,
            "rewrite_cost": rewrite_cost,
            "improvement_pct": 0,
            "is_better": False
        }

    # Rows examined improvement
    baseline_rows = baseline.get('rows_examined', 0)
    rewrite_rows = rewrite.get('rows_examined', 0)

    if baseline_rows > 0:
        rows_improvement_pct = ((baseline_rows - rewrite_rows) / baseline_rows) * 100
        improvement['rows_examined'] = {
            "baseline_rows": baseline_rows,
            "rewrite_rows": rewrite_rows,
            "improvement_pct": round(rows_improvement_pct, 2),
            "is_better": rows_improvement_pct > 0
        }
    else:
        improvement['rows_examined'] = {
            "baseline_rows": baseline_rows,
            "rewrite_rows": rewrite_rows,
            "improvement_pct": 0,
            "is_better": rewrite_rows <= baseline_rows
        }

    # Overall improvement score (weighted average)
    time_weight = 0.6
    cost_weight = 0.3
    rows_weight = 0.1

    overall_improvement = (
        improvement['execution_time']['improvement_pct'] * time_weight +
        improvement['cost_estimate']['improvement_pct'] * cost_weight +
        improvement['rows_examined']['improvement_pct'] * rows_weight
    )

    improvement['overall'] = {
        "improvement_pct": round(overall_improvement, 2),
        "is_better": overall_improvement > 0,
        "significant": abs(overall_improvement) >= 5.0  # 5% threshold for significance
    }

    return improvement


def _determine_rewrite_recommendation(improvement: Dict[str, Any], suggestion: Dict[str, Any]) -> str:
    """Determine recommendation based on improvement metrics."""
    overall = improvement.get('overall', {})
    overall_improvement = overall.get('improvement_pct', 0)
    is_significant = overall.get('significant', False)

    # Consider suggestion confidence
    confidence = suggestion.get('confidence', 'medium').lower()
    priority = suggestion.get('priority', 'medium').lower()

    if overall_improvement >= 20:
        return "strongly_recommend"
    elif overall_improvement >= 10:
        return "recommend"
    elif overall_improvement >= 5 and confidence == 'high':
        return "consider"
    elif overall_improvement >= 1 and priority == 'high':
        return "consider_with_caution"
    elif overall_improvement > -5:
        return "marginal_benefit"
    else:
        return "reject_slower"


def _analyze_rewrite_results(baseline_result: Dict[str, Any],
                            rewrite_results: List[Dict[str, Any]],
                            baseline_skipped: bool = False) -> Dict[str, Any]:
    """Analyze all rewrite results and provide final recommendations."""
    successful_results = [r for r in rewrite_results if r.get('success', False)]

    if not successful_results:
        return {
            "best_rewrite": None,
            "comparison": "No successful rewrites to compare",
            "recommendations": "All rewrite attempts failed or were unsafe",
            "summary": f"Tested {len(rewrite_results)} rewrites, none succeeded"
        }

    # Find best rewrite by overall improvement (or just first one if baseline skipped)
    best_rewrite = None
    best_improvement = -float('inf')

    recommended_rewrites = []
    for result in successful_results:
        improvement_data = result.get('improvement', {})
        if improvement_data is None:
            improvement_data = {}
        improvement = improvement_data.get('overall', {})
        if improvement is None:
            improvement = {}
        improvement_pct = improvement.get('improvement_pct', 0)
        recommendation = result.get('recommendation', '')

        if improvement_pct > best_improvement:
            best_improvement = improvement_pct
            best_rewrite = result

        if recommendation in ['strongly_recommend', 'recommend', 'consider', 'advisory_ddl']:
            recommended_rewrites.append(result)

    # Generate comparison summary
    comparison = _generate_comparison_summary(baseline_result, successful_results, baseline_skipped)

    # Generate final recommendations
    recommendations = _generate_final_recommendations(
        baseline_result, best_rewrite, recommended_rewrites, baseline_skipped
    )

    # Generate summary
    if baseline_skipped:
        skip_reason = baseline_result.get('skip_reason', 'original query took too long')
        summary = f"Tested {len(rewrite_results)} rewrites: " \
                 f"{len(successful_results)} successful. "  \
                 f"WARNING: Cannot compare performance - {skip_reason}"
    else:
        summary = f"Tested {len(rewrite_results)} rewrites: " \
                 f"{len(successful_results)} successful, " \
                 f"{len(recommended_rewrites)} recommended"

        if best_rewrite:
            improvement_data = best_rewrite.get('improvement', {})
            if improvement_data is None:
                improvement_data = {}
            overall_data = improvement_data.get('overall', {})
            if overall_data is None:
                overall_data = {}
            best_improvement_pct = overall_data.get('improvement_pct', 0)
            summary += f". Best improvement: {best_improvement_pct:.1f}%"

    return {
        "best_rewrite": best_rewrite,
        "comparison": comparison,
        "recommendations": recommendations,
        "summary": summary,
        "recommended_rewrites": recommended_rewrites
    }


def _generate_comparison_summary(baseline: Dict[str, Any],
                                successful_results: List[Dict[str, Any]],
                                baseline_skipped: bool = False) -> str:
    """Generate a human-readable comparison summary."""
    if not successful_results:
        return "No successful rewrites to compare"

    if baseline_skipped:
        skip_reason = baseline.get('skip_reason', 'original query took too long')
        summary_parts = [
            f"WARNING: {skip_reason} - showing rewrite execution times only:"
        ]

        for result in successful_results[:3]:  # Top 3 results
            perf = result.get('performance', {})
            if perf is None:
                perf = {}
            was_skipped = result.get('was_skipped', False) or perf.get('was_skipped', False)
            skip_reason = result.get('skip_reason') or perf.get('skip_reason')

            if was_skipped:
                # Show actual elapsed time when skipped, not the instant EXPLAIN time
                actual_elapsed = perf.get('actual_elapsed_time_ms', 0)
                time_str = f"N/A (skipped after {actual_elapsed / 1000:.1f}s)"
            else:
                rewrite_time = perf.get('execution_time_ms', 0)
                time_str = f"{rewrite_time:.1f}ms"

            rewrite_cost = perf.get('cost_estimate', 0)
            sql = result.get('sql', 'Unknown')

            summary_parts.append(
                f"\nRewrite {result['suggestion_id']}: {time_str}, cost {rewrite_cost:.1f}"
            )
            if was_skipped and skip_reason:
                summary_parts.append(f"  Note: {skip_reason}")
            summary_parts.append(f"  FULL SQL: {sql}")
    else:
        baseline_time = baseline['performance'].get('execution_time_ms', 0)
        baseline_cost = baseline['performance'].get('cost_estimate', 0)

        summary_parts = [
            f"Original query: {baseline_time:.1f}ms, cost {baseline_cost:.1f}"
        ]

        for result in successful_results[:3]:  # Top 3 results
            perf = result.get('performance', {})
            if perf is None:
                perf = {}
            improvement_data = result.get('improvement', {})
            if improvement_data is None:
                improvement_data = {}
            improvement = improvement_data.get('overall', {})
            if improvement is None:
                improvement = {}
            rewrite_time = perf.get('execution_time_ms', 0)
            rewrite_cost = perf.get('cost_estimate', 0)
            improvement_pct = improvement.get('improvement_pct', 0)

            summary_parts.append(
                f"Rewrite {result['suggestion_id']}: {rewrite_time:.1f}ms, "
                f"cost {rewrite_cost:.1f} ({improvement_pct:+.1f}%)"
            )

    return "\n".join(summary_parts)


def _generate_final_recommendations(baseline: Dict[str, Any], best_rewrite: Optional[Dict[str, Any]],
                                   recommended_rewrites: List[Dict[str, Any]],
                                   baseline_skipped: bool = False) -> str:
    """Generate final recommendations for query rewrites."""
    if not best_rewrite:
        return "No beneficial query rewrites identified. Consider index optimization instead."

    if baseline_skipped:
        # When baseline was skipped, we can't make performance comparisons
        skip_reason = baseline.get('skip_reason', 'original query took too long')
        recommendations = [
            f"WARNING: {skip_reason} - cannot compare performance.",
            "",
            "Rewrite execution times available below for reference.",
            "Consider testing rewrites in a staging environment to compare performance."
        ]
        return "\n".join(recommendations)

    improvement_data = best_rewrite.get('improvement', {})
    if improvement_data is None:
        improvement_data = {}
    overall_data = improvement_data.get('overall', {})
    if overall_data is None:
        overall_data = {}
    best_improvement = overall_data.get('improvement_pct', 0)
    best_recommendation = best_rewrite.get('recommendation', '')

    recommendations = []

    if best_recommendation in ['strongly_recommend', 'recommend']:
        recommendations.append(
            f"✓ Implement rewrite {best_rewrite['suggestion_id']} "
            f"for {best_improvement:.1f}% performance improvement"
        )
    elif best_recommendation == 'consider':
        recommendations.append(
            f"? Consider rewrite {best_rewrite['suggestion_id']} "
            f"({best_improvement:.1f}% improvement) after thorough testing"
        )

    if len(recommended_rewrites) > 1:
        recommendations.append(
            f"• {len(recommended_rewrites)} rewrites show potential benefits"
        )

    if not recommendations:
        recommendations.append("No significant performance improvements identified from rewrites")

    # Add testing advice
    if any(r.get('recommendation') in ['strongly_recommend', 'recommend'] for r in recommended_rewrites):
        recommendations.append("⚠ Test all recommended rewrites with production data before deployment")

    return "\n".join(recommendations)