"""
RDST Clean Output Formatter

Provides Claude Code-style clean, scannable formatting for RDST analyze results.
Removes runtime progress noise and presents information in a hierarchical, actionable format.
"""

from typing import Dict, Any, List, Optional
import textwrap
import shlex

# Rich imports for beautiful formatting
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.syntax import Syntax
    from rich.table import Table
    from rich.text import Text
    from rich import box
    _RICH_AVAILABLE = True
except ImportError:
    _RICH_AVAILABLE = False


def _generate_db_test_command(sql: str, target_config: Dict[str, Any], db_engine: str) -> Optional[str]:
    """
    Generate a one-liner database command for testing a query.

    Uses environment variable reference (not actual password) for security.

    Args:
        sql: The SQL query to test
        target_config: Target configuration with host, port, user, password_env, database
        db_engine: Database engine type (mysql, postgresql, postgres)

    Returns:
        One-liner shell command string, or None if config is incomplete
    """
    if not target_config or not sql:
        return None

    host = target_config.get("host")
    port = target_config.get("port")
    user = target_config.get("user")
    database = target_config.get("database")
    # Use the env var NAME, not the actual password (security!)
    password_env = target_config.get("password_env", "DB_PASSWORD")

    if not all([host, port, user, database]):
        return None

    # Clean up SQL: remove trailing semicolon and normalize whitespace
    sql_clean = sql.strip().rstrip(';')
    # Escape single quotes in SQL for shell
    sql_escaped = sql_clean.replace("'", "'\"'\"'")

    # Reference the environment variable by name (e.g., $IMDB_POSTGRES_PASSWORD)
    pwd_ref = f"${password_env}"

    engine_lower = (db_engine or "").lower()

    if engine_lower in ("mysql", "mariadb"):
        return f"MYSQL_PWD=\"{pwd_ref}\" mysql -h {host} -P {port} -u {user} {database} -e '{sql_escaped}'"

    elif engine_lower in ("postgresql", "postgres", "pg"):
        # Use \timing to show query execution time
        return f"PGPASSWORD=\"{pwd_ref}\" psql -h {host} -p {port} -U {user} -d {database} -c '\\timing' -c '{sql_escaped}'"

    return None


def _clean_error_message(error: str) -> str:
    """Clean up error messages to be user-friendly (no tracebacks)."""
    if not error:
        return "Unknown error"

    # Remove traceback sections entirely
    if "Traceback (most recent call last):" in error:
        parts = error.split("Traceback (most recent call last):")
        before_traceback = parts[0].strip()
        if before_traceback:
            # Clean the before-traceback part too
            return _clean_error_message(before_traceback)
        return "Database error occurred"

    # Clean up PostgreSQL-style error messages
    clean_lines = []
    for line in error.split('\n'):
        line = line.strip()
        # Skip LINE 1: and ^ pointer lines
        if line.startswith('LINE 1:') or line.startswith('^'):
            continue
        # Skip file paths
        if line.startswith('File "') or line.startswith('cursor.'):
            continue
        # Keep HINT lines but clean them
        if line.startswith('HINT:'):
            clean_lines.append(line)
        elif line:
            clean_lines.append(line)

    result = ' '.join(clean_lines[:2])  # Max 2 meaningful lines

    # Extract just the error type and message for PostgreSQL errors
    # e.g., "UndefinedFunction: operator does not exist: text = integer"
    if ': ' in result and ('Error' in result or 'Exception' in result or 'Function' in result):
        # Keep just the error description
        parts = result.split(': ', 1)
        if len(parts) > 1:
            return parts[1][:200]

    return result[:300]  # Limit length


def _wrap_text(text: str, width: int = 100, indent: str = "", subsequent_indent: str = "") -> List[str]:
    """
    Wrap text to specified width while preserving formatting.

    Args:
        text: Text to wrap
        width: Maximum line width (default: 100)
        indent: Indentation for first line
        subsequent_indent: Indentation for subsequent lines

    Returns:
        List of wrapped lines
    """
    if not text:
        return []

    # Use textwrap to handle the wrapping
    wrapped = textwrap.fill(
        text,
        width=width,
        initial_indent=indent,
        subsequent_indent=subsequent_indent,
        break_long_words=False,
        break_on_hyphens=False
    )

    return wrapped.split('\n')


def format_analyze_output(workflow_result: Dict[str, Any]) -> str:
    """
    Format analysis results in a clean, scannable format.

    Args:
        workflow_result: Complete workflow execution result

    Returns:
        Formatted string output for display to user
    """
    try:
        # Get the formatted output from workflow
        formatted_output = workflow_result.get("FormatFinalResults")

        # Use raw workflow formatting if FormatFinalResults is None or failed
        if not formatted_output or not isinstance(formatted_output, dict) or not formatted_output.get("success", True):
            return _format_from_raw_workflow(workflow_result)

        lines = []

        # Header box
        lines.extend(_format_header(formatted_output))
        lines.append("")

        # Query - use normalized/parameterized version for privacy (no PII)
        metadata = formatted_output.get("metadata") or {}
        # Prefer normalized_query > parameterized_sql > query
        query = (
            metadata.get("normalized_query") or
            metadata.get("parameterized_sql") or
            metadata.get("query", "")
        )
        if query:
            lines.extend(_format_query(query))
            lines.append(_divider())

        # Performance summary (compact, scannable)
        summary = formatted_output.get("analysis_summary") or {}
        perf_metrics = formatted_output.get("performance_metrics") or {}
        if summary:
            lines.extend(_format_performance_summary(summary, perf_metrics))
            lines.append(_divider())

        # Tested optimizations (if any)
        rewrite_testing = formatted_output.get("rewrite_testing") or {}
        # Get target config and db engine for copy-paste commands
        target_config = workflow_result.get("target_config") or {}
        db_engine = metadata.get("database_engine", "")
        if rewrite_testing.get("tested") and rewrite_testing.get("rewrite_results"):
            lines.extend(_format_tested_optimizations(rewrite_testing, target_config, db_engine))
            lines.append(_divider())
        elif rewrite_testing.get("skipped_reason") == "parameterized_query":
            # Query has placeholders ($1, $2, ?) - can't test rewrites without actual values
            lines.append("⚠️  REWRITE TESTING SKIPPED")
            lines.append("")
            lines.append("  This query contains parameter placeholders ($1, $2 or ?) without actual values.")
            lines.append("  Query rewrites were suggested but could not be tested.")
            lines.append("")
            lines.append("  This typically happens when:")
            lines.append("    • Query was captured from rdst top using prepared statements")
            lines.append("    • Query was normalized from performance_schema without stored parameters")
            lines.append("")
            lines.append("  To test rewrites with actual execution times:")
            lines.append("    rdst analyze --query \"SELECT ... WHERE id = 123\"")
            lines.append("  Use the original query from your application code with real parameter values.")
            lines.append("")
            lines.append(_divider())

        # Index recommendations (clear, actionable)
        recommendations = formatted_output.get("recommendations") or {}
        if recommendations.get("available") and recommendations.get("index_suggestions"):
            lines.extend(_format_index_recommendations(recommendations))
            lines.append(_divider())

        # Query rewrite suggestions (AI recommended, not yet tested)
        if recommendations.get("available") and recommendations.get("query_rewrites"):
            # Only show if not already in tested optimizations
            if not (rewrite_testing.get("tested") and rewrite_testing.get("rewrite_results")):
                lines.extend(_format_query_rewrite_suggestions(recommendations, target_config, db_engine))
                lines.append(_divider())

        # ReadySet cacheability
        readyset_analysis = workflow_result.get("readyset_analysis") or {}
        readyset_cacheability = formatted_output.get("readyset_cacheability") or {}
        if readyset_analysis.get("success") or readyset_cacheability.get("checked"):
            lines.extend(_format_readyset_cacheability(readyset_analysis, readyset_cacheability))
            lines.append(_divider())

        # Optimization insights (additional recommendations)
        optimization_insights = formatted_output.get("optimization_insights") or {}
        if optimization_insights.get("available"):
            lines.extend(_format_additional_recommendations(optimization_insights))
            lines.append(_divider())

        # Next steps (actionable)
        lines.extend(_format_next_steps(formatted_output, rewrite_testing, recommendations, metadata))

        return "\n".join(lines)

    except Exception as e:
        # Last resort fallback
        return f"Analysis completed but formatting failed: {str(e)}\n\nRaw result available in registry."


def _format_from_raw_workflow(workflow_result: Dict[str, Any]) -> str:
    """Format from raw workflow results when FormatFinalResults failed."""
    lines = []

    # Header
    target = workflow_result.get("target", "unknown")
    explain_results = workflow_result.get("explain_results") or {}
    db_engine = explain_results.get("database_engine", "")
    # Get target config for copy-paste test commands
    target_config = workflow_result.get("target_config") or {}
    storage_result = workflow_result.get("storage_result") or {}
    analysis_id = storage_result.get("analysis_id", "")[:12] if storage_result else ""

    engine_display = f"{db_engine.upper()}" if db_engine else "Unknown DB"

    lines.append("╭─────────────────────────────────────────────────────────────╮")
    lines.append("│ RDST Query Analysis                                         │")
    lines.append(f"│ Target: {target:<52}│")
    lines.append(f"│ Engine: {engine_display:<52}│")
    if analysis_id:
        lines.append(f"│ Analysis ID: {analysis_id:<47}│")

    # Add LLM token usage if available
    llm_analysis = workflow_result.get("llm_analysis") or {}
    token_usage = llm_analysis.get("token_usage")
    if token_usage:
        tokens_in = token_usage.get("input", 0)
        tokens_out = token_usage.get("output", 0)
        total = token_usage.get("total", tokens_in + tokens_out)
        cost = token_usage.get("estimated_cost_usd", 0)
        model = llm_analysis.get("llm_model", "claude")
        # Shorten model name for display
        model_short = model.replace("claude-", "").replace("-20250514", "").replace("-20250929", "")
        llm_line = f"│ LLM: {model_short} ({total:,} tokens, ~${cost:.3f})".ljust(61) + "│"
        lines.append(llm_line)

    lines.append("╰─────────────────────────────────────────────────────────────╯")
    lines.append("")

    # Query - use normalized/parameterized version for privacy (no PII)
    query = (
        workflow_result.get("normalized_query") or
        workflow_result.get("parameterized_sql") or
        workflow_result.get("query", "")
    )
    if query:
        lines.append("Query:")
        for line in query.strip().split('\n'):
            lines.append(f"  {line}")
        lines.append("")
        lines.append(_divider())

    # Performance summary with AI analysis if available
    if explain_results and explain_results.get("success"):
        lines.append("⚡ PERFORMANCE SUMMARY")
        lines.append("")

        exec_time = explain_results.get("execution_time_ms", 0)

        # Check if EXPLAIN ANALYZE was skipped (fast mode or user skip)
        explain_skipped = explain_results.get("explain_analyze_skipped", False)
        skip_reason = explain_results.get("skip_reason") or explain_results.get("fallback_reason")

        # Get LLM analysis for rating and score
        llm_analysis = workflow_result.get("llm_analysis") or {}
        if llm_analysis and llm_analysis.get("success"):
            analysis_results = llm_analysis.get("analysis_results") or {}
            performance = analysis_results.get("performance_assessment") or {}
            overall_rating = performance.get("overall_rating", "unknown")
            efficiency_score = performance.get("efficiency_score", 0)
            exec_rating = performance.get("execution_time_rating", "")

            # If EXPLAIN ANALYZE was skipped, show N/A for execution time
            if explain_skipped:
                lines.append(f"Query Execution Time: N/A (EXPLAIN only) | Rating: {overall_rating.upper()} ({efficiency_score}/100)")
            elif exec_rating and exec_rating != "unknown":
                lines.append(f"Query Execution Time: {exec_time:.1f}ms ({exec_rating}) | Rating: {overall_rating.upper()} ({efficiency_score}/100)")
            else:
                lines.append(f"Query Execution Time: {exec_time:.1f}ms | Rating: {overall_rating.upper()} ({efficiency_score}/100)")
        else:
            if explain_skipped:
                lines.append(f"Query Execution Time: N/A (EXPLAIN only - query not executed)")
            else:
                lines.append(f"Query Execution Time: {exec_time:.1f}ms")

        lines.append("")
        lines.append(f"  Rows Examined: {explain_results.get('rows_examined', 0):,}")
        lines.append(f"  Rows Returned: {explain_results.get('rows_returned', 0):,}")
        cost = explain_results.get("cost_estimate", 0)
        if cost > 0:
            lines.append(f"  Cost Estimate: {cost:,.0f}")

        # Primary concerns from LLM
        if llm_analysis and llm_analysis.get("success"):
            concerns = performance.get("primary_concerns") or []
            if concerns:
                lines.append("")
                lines.append("Primary Concerns:")
                for concern in concerns[:3]:
                    wrapped = _wrap_text(concern, width=100, indent="  • ", subsequent_indent="    ")
                    lines.extend(wrapped)
    else:
        # EXPLAIN ANALYZE failed - show clear error and stop
        lines.append("❌ QUERY EXECUTION FAILED")
        lines.append("")
        if explain_results.get("error"):
            clean_error = _clean_error_message(explain_results.get("error"))
            wrapped = _wrap_text(clean_error, width=95, indent="  ", subsequent_indent="  ")
            lines.extend(wrapped)
            lines.append("")

            # Provide helpful hints based on error type
            error_lower = explain_results.get("error", "").lower()
            if "operator does not exist" in error_lower:
                lines.append("  Fix: Check parameter types match the column types (e.g., use 'movie' not 123).")
            elif "column" in error_lower and "does not exist" in error_lower:
                lines.append("  Fix: Check that the column name is correct and exists in the table.")
            elif "relation" in error_lower and "does not exist" in error_lower:
                lines.append("  Fix: Check that the table name is correct.")
            elif "permission denied" in error_lower:
                lines.append("  Fix: Check database user has SELECT permissions.")
            elif "connection" in error_lower or "refused" in error_lower:
                lines.append("  Fix: Check database connectivity with 'rdst configure list'.")
            elif "syntax error" in error_lower:
                lines.append("  Fix: Check SQL syntax.")
            else:
                lines.append("  Fix: Review the query and try again.")

        lines.append("")
        lines.append(_divider())

        # Don't show AI analysis error if EXPLAIN failed - that's the root cause
        return "\n".join(lines)

    lines.append("")
    lines.append(_divider())

    # Show LLM analysis error ONLY if it failed independently (not due to EXPLAIN failure)
    llm_analysis = workflow_result.get("llm_analysis") or {}
    explain_failed = not explain_results.get("success", False)
    if llm_analysis and not llm_analysis.get("success") and llm_analysis.get("error") and not explain_failed:
        lines.append("⚠️  AI ANALYSIS ERROR")
        lines.append("")
        error_msg = _clean_error_message(llm_analysis.get("error", "Unknown error"))
        wrapped = _wrap_text(error_msg, width=100, indent="  ", subsequent_indent="  ")
        lines.extend(wrapped)
        lines.append("")
        lines.append("  Tip: Check your API key and provider settings with 'rdst configure llm'")
        lines.append("")
        lines.append(_divider())

    # Index recommendations from LLM
    if llm_analysis and llm_analysis.get("success"):
        index_recs = llm_analysis.get("index_recommendations") or []
        if index_recs:
            lines.append("🔧 RECOMMENDED INDEXES")
            lines.append("")
            for i, idx in enumerate(index_recs[:3], 1):
                rationale = idx.get("rationale", "")
                sql = idx.get("sql", "")
                impact = idx.get("estimated_impact", "UNKNOWN")

                lines.append(f"{i}. ({impact.upper()} IMPACT)")
                if sql:
                    lines.append(f"   {sql}")
                lines.append("")
                if rationale:
                    wrapped = _wrap_text(f"Why: {rationale}", width=100, indent="   ", subsequent_indent="   ")
                    lines.extend(wrapped)
                lines.append("")

                # Display caveats if present (important context about index limitations)
                caveats = idx.get("caveats", [])
                if caveats:
                    for caveat in caveats:
                        # Format caveat - extract key if present (e.g., "workload_context: message")
                        if ": " in caveat:
                            key, msg = caveat.split(": ", 1)
                            wrapped = _wrap_text(f"Note: {msg}", width=100, indent="   ", subsequent_indent="         ")
                        else:
                            wrapped = _wrap_text(f"Note: {caveat}", width=100, indent="   ", subsequent_indent="         ")
                        lines.extend(wrapped)
                    lines.append("")

            # Show workload analysis tip when there are 2+ index recommendations
            # This indicates complex optimization where holistic analysis would help
            if len(index_recs) >= 2:
                lines.append("Note: These indexes optimize this query. For workload-wide index")
                lines.append("recommendations across multiple queries: rdst analyze --workload (coming soon)")
                lines.append("")

            lines.append(_divider())

    # Tested rewrites
    rewrite_results = workflow_result.get("rewrite_test_results") or {}

    # Check if rewrite testing was skipped due to parameterized query
    if rewrite_results and rewrite_results.get("skipped_reason") == "parameterized_query":
        lines.append("⚠️  REWRITE TESTING SKIPPED")
        lines.append("")
        lines.append("  This query contains parameter placeholders ($1, $2 or ?) without actual values.")
        lines.append("  Query rewrites were suggested but could not be tested.")
        lines.append("")
        lines.append("  This typically happens when:")
        lines.append("    • Query was captured from rdst top using prepared statements")
        lines.append("    • Query was normalized from performance_schema without stored parameters")
        lines.append("")
        lines.append("  To test rewrites with actual execution times:")
        lines.append("    rdst analyze --query \"SELECT ... WHERE id = 123\"")
        lines.append("  Use the original query from your application code with real parameter values.")
        lines.append("")
        lines.append(_divider())
    elif rewrite_results and rewrite_results.get("success"):
        tested_rewrites = rewrite_results.get("rewrite_results", [])
        baseline_skipped = rewrite_results.get("baseline_skipped", False)
        original_perf = rewrite_results.get("original_performance") or {}
        baseline_time = original_perf.get("execution_time_ms", 0)

        if tested_rewrites and not baseline_skipped:
            lines.append("📊 TESTED OPTIMIZATIONS")
            lines.append("")

            successful_rewrites = []
            for result in tested_rewrites:
                if result.get("success") and result.get("recommendation") not in ["advisory_ddl"]:
                    perf = result.get("performance") or {}
                    was_skipped = result.get("was_skipped", False) or perf.get("was_skipped", False)
                    if not was_skipped:
                        successful_rewrites.append(result)

            if successful_rewrites:
                for i, rewrite in enumerate(successful_rewrites[:3], 1):
                    metadata = rewrite.get("suggestion_metadata") or {}
                    explanation = metadata.get("explanation", "Query rewrite")

                    improvement = (rewrite.get("improvement") or {}).get("overall") or {}
                    improvement_pct = improvement.get("improvement_pct", 0)

                    perf = rewrite.get("performance") or {}
                    rewrite_time = perf.get("execution_time_ms", 0)

                    # Clear indicator: positive = FASTER, negative = SLOWER
                    if improvement_pct >= 10:
                        status_icon = "✅"
                        status_text = "FASTER"
                        pct_display = f"+{improvement_pct:.1f}%"
                    elif improvement_pct >= 0:
                        status_icon = "➡️ "
                        status_text = "SIMILAR"
                        pct_display = f"{improvement_pct:+.1f}%"
                    else:
                        status_icon = "❌"
                        status_text = "SLOWER"
                        pct_display = f"{improvement_pct:.1f}%"

                    # Header with clear status
                    lines.append(f"{status_icon} Rewrite #{i}: {status_text} ({pct_display})")
                    lines.append(f"   Time: {baseline_time:.1f}ms → {rewrite_time:.1f}ms")
                    lines.append("")

                    # Explanation
                    wrapped_explanation = _wrap_text(explanation, width=95, indent="   ", subsequent_indent="   ")
                    lines.extend(wrapped_explanation)
                    lines.append("")

                    sql = rewrite.get("sql", "")
                    if sql:
                        lines.append("   SQL:")
                        for sql_line in sql.strip().split('\n'):
                            lines.append(f"   {sql_line}")
                        lines.append("")
                        # Add copy-paste test command (uses env var reference for security)
                        if target_config and db_engine:
                            test_cmd = _generate_db_test_command(sql, target_config, db_engine)
                            if test_cmd:
                                lines.append("   Test it yourself:")
                                lines.append(f"   {test_cmd}")
                    lines.append("")
            else:
                lines.append("  No rewrites were tested successfully")

            lines.append("")
            lines.append(_divider())

    # Additional optimization opportunities from LLM
    if llm_analysis and llm_analysis.get("success"):
        analysis_results = llm_analysis.get("analysis_results") or {}
        opps = analysis_results.get("optimization_opportunities") or []
        if opps:
            lines.append("💡 ADDITIONAL RECOMMENDATIONS")
            lines.append("")
            for i, opp in enumerate(opps[:3], 1):
                description = opp.get("description", "")
                priority = opp.get("priority", "MEDIUM")
                rationale = opp.get("rationale", "")

                # Wrap description
                wrapped_desc = _wrap_text(f"{i}. [{priority}] {description}", width=100, indent="", subsequent_indent="   ")
                lines.extend(wrapped_desc)

                if rationale:
                    wrapped_rationale = _wrap_text(rationale, width=100, indent="   ", subsequent_indent="   ")
                    lines.extend(wrapped_rationale)
                lines.append("")

            lines.append(_divider())

    # Next steps
    lines.append("📝 NEXT STEPS")
    lines.append("")

    # Quick win from tested rewrites
    if rewrite_results and rewrite_results.get("success"):
        baseline_skipped = rewrite_results.get("baseline_skipped", False)
        if not baseline_skipped:
            tested_rewrites = rewrite_results.get("rewrite_results", [])
            best_rewrite = None
            best_improvement = 0

            for result in tested_rewrites:
                if result.get("success") and result.get("recommendation") not in ["advisory_ddl"]:
                    perf = result.get("performance") or {}
                    was_skipped = result.get("was_skipped", False) or perf.get("was_skipped", False)
                    if not was_skipped:
                        improvement = ((result.get("improvement") or {}).get("overall") or {}).get("improvement_pct", 0)
                        if improvement > best_improvement:
                            best_improvement = improvement
                            best_rewrite = result

            if best_rewrite and best_improvement >= 5:
                rewrite_time = (best_rewrite.get("performance") or {}).get("execution_time_ms", 0)
                lines.append(f"• Apply tested rewrite ({rewrite_time:.1f}ms, {best_improvement:+.1f}% faster)")

    # Index suggestions
    if llm_analysis and llm_analysis.get("success"):
        index_recs = llm_analysis.get("index_recommendations") or []
        if index_recs:
            lines.append("• Create recommended indexes above for long-term improvement")

    # Continuation hint
    if analysis_id:
        lines.append("")
        lines.append("Continue this conversation:")
        lines.append(f"  rdst analyze --hash {analysis_id} --interactive")
        lines.append("")
        lines.append("List recent queries:  rdst query list --limit 5")

    return "\n".join(lines)


def _format_header(formatted_output: Dict[str, Any]) -> List[str]:
    """Create top box with key metadata."""
    metadata = formatted_output.get("metadata") or {}
    target = metadata.get("target", "unknown")
    db_engine = metadata.get("database_engine", "")
    analysis_id = metadata.get("analysis_id", "")[:12]  # Truncate for display

    # Build engine display string
    engine_display = f"{db_engine.upper()}" if db_engine else "Unknown DB"

    # Box width = 63 chars total
    # Format: "│ " (2) + content + " │" (2) = 63 total
    # Content area = 59 chars
    return [
        "╭─────────────────────────────────────────────────────────────╮",
        "│ RDST Query Analysis                                         │",
        f"│ Target: {target:<52}│",
        f"│ Engine: {engine_display:<52}│",
        f"│ Analysis ID: {analysis_id:<47}│",
        "╰─────────────────────────────────────────────────────────────╯"
    ]


def _divider() -> str:
    """Visual section separator."""
    return "━" * 65


def _format_query(query: str) -> List[str]:
    """Format query with indentation."""
    lines = ["Query:"]
    for line in query.strip().split('\n'):
        lines.append(f"  {line}")
    return lines


def _format_performance_summary(summary: Dict[str, Any], perf_metrics: Dict[str, Any]) -> List[str]:
    """Compact performance metrics."""
    lines = ["⚡ PERFORMANCE SUMMARY", ""]

    # Execution time with rating
    exec_time = summary.get("execution_time_ms", 0)
    exec_rating = summary.get("execution_time_rating", "")
    overall_rating = summary.get("overall_rating", "unknown")
    efficiency_score = summary.get("efficiency_score", 0)

    # Check if EXPLAIN ANALYZE was skipped
    explain_skipped = summary.get("explain_analyze_skipped", False)

    # Build execution line - show N/A if skipped, otherwise show time
    if explain_skipped:
        exec_line = f"Query Execution Time: N/A (EXPLAIN only) | Rating: {overall_rating.upper()} ({efficiency_score}/100)"
    elif exec_rating and exec_rating != "unknown":
        exec_line = f"Query Execution Time: {exec_time:.1f}ms ({exec_rating}) | Rating: {overall_rating.upper()} ({efficiency_score}/100)"
    else:
        exec_line = f"Query Execution Time: {exec_time:.1f}ms | Rating: {overall_rating.upper()} ({efficiency_score}/100)"

    lines.append(exec_line)
    lines.append("")

    # Row statistics
    rows_processed = summary.get("rows_processed") or {}
    lines.append(f"  Rows Examined: {rows_processed.get('examined', 0):,}")
    lines.append(f"  Rows Returned: {rows_processed.get('returned', 0):,}")

    cost = summary.get("cost_estimate", 0)
    if cost > 0:
        lines.append(f"  Cost Estimate: {cost:,.0f}")

    # Primary concerns
    concerns = summary.get("primary_concerns", [])
    if concerns:
        lines.append("")
        lines.append("Primary Concerns:")
        for concern in concerns[:3]:  # Top 3 only
            lines.append(f"  • {concern}")

    return lines


def _format_tested_optimizations(rewrite_testing: Dict[str, Any],
                                  target_config: Optional[Dict[str, Any]] = None,
                                  db_engine: Optional[str] = None) -> List[str]:
    """Show tested rewrites with clear improvement metrics and copy-paste test commands."""
    lines = ["📊 TESTED OPTIMIZATIONS", ""]

    rewrite_results = rewrite_testing.get("rewrite_results", [])
    original_perf = rewrite_testing.get("original_performance") or {}
    baseline_time = original_perf.get("execution_time_ms", 0)
    baseline_skipped = rewrite_testing.get("baseline_skipped", False)

    if baseline_skipped:
        lines.append("  ⚠️  Original query was skipped (slow execution) - no baseline for comparison")
        lines.append("")

    successful_rewrites = []
    for result in rewrite_results:
        if result.get("success") and result.get("recommendation") not in ["advisory_ddl"]:
            perf = result.get("performance") or {}
            was_skipped = result.get("was_skipped", False) or perf.get("was_skipped", False)
            if not was_skipped and not baseline_skipped:
                successful_rewrites.append(result)

    if not successful_rewrites:
        lines.append("  No rewrites were tested successfully")
        return lines

    for i, rewrite in enumerate(successful_rewrites[:3], 1):  # Top 3
        metadata = rewrite.get("suggestion_metadata") or {}
        explanation = metadata.get("explanation", "Query rewrite")

        improvement = (rewrite.get("improvement") or {}).get("overall") or {}
        improvement_pct = improvement.get("improvement_pct", 0)

        perf = rewrite.get("performance") or {}
        rewrite_time = perf.get("execution_time_ms", 0)

        # Clear indicator: positive = FASTER, negative = SLOWER
        if improvement_pct >= 10:
            status_icon = "✅"
            status_text = "FASTER"
            pct_display = f"+{improvement_pct:.1f}%"
        elif improvement_pct >= 0:
            status_icon = "➡️ "
            status_text = "SIMILAR"
            pct_display = f"{improvement_pct:+.1f}%"
        else:
            status_icon = "❌"
            status_text = "SLOWER"
            pct_display = f"{improvement_pct:.1f}%"

        # Header with clear status
        lines.append(f"{status_icon} Rewrite #{i}: {status_text} ({pct_display})")
        lines.append(f"   Time: {baseline_time:.1f}ms → {rewrite_time:.1f}ms")
        lines.append("")

        # Explanation
        wrapped_explanation = _wrap_text(explanation, width=95, indent="   ", subsequent_indent="   ")
        lines.extend(wrapped_explanation)
        lines.append("")

        # Show FULL SQL (no truncation)
        sql = rewrite.get("sql", "")
        if sql:
            lines.append("   SQL:")
            for sql_line in sql.strip().split('\n'):
                lines.append(f"   {sql_line}")
            lines.append("")

        # Add copy-paste test command (uses env var reference for security)
        if sql and target_config and db_engine:
            test_cmd = _generate_db_test_command(sql, target_config, db_engine)
            if test_cmd:
                lines.append("   Test it yourself:")
                lines.append(f"   {test_cmd}")
                lines.append("")

    return lines


def _format_index_recommendations(recommendations: Dict[str, Any]) -> List[str]:
    """Clear, actionable index suggestions."""
    lines = ["🔧 RECOMMENDED INDEXES", ""]

    index_suggestions = recommendations.get("index_suggestions", [])

    for i, idx in enumerate(index_suggestions[:5], 1):  # Top 5
        idx_type = idx.get("type", "Unknown")
        table = idx.get("table", "")
        expected_benefit = idx.get("expected_benefit", "UNKNOWN")

        lines.append(f"{i}. {idx_type} Index ({expected_benefit} IMPACT)")
        lines.append("")

        # SQL statement
        sql = idx.get("sql_statement", "")
        if sql:
            lines.append(f"   {sql}")
        else:
            columns = idx.get("columns", [])
            if columns and table:
                columns_str = ", ".join(columns)
                lines.append(f"   CREATE INDEX idx_{table}_{'_'.join(columns[:2])} ON {table}({columns_str});")

        lines.append("")

        # Rationale
        rationale = idx.get("rationale", "")
        if rationale:
            wrapped = _wrap_text(f"Why: {rationale}", width=100, indent="   ", subsequent_indent="   ")
            lines.extend(wrapped)

        # Storage impact
        storage_impact = idx.get("storage_impact", "")
        if storage_impact:
            wrapped = _wrap_text(f"Storage: {storage_impact}", width=100, indent="   ", subsequent_indent="   ")
            lines.extend(wrapped)

        lines.append("")

    return lines


def _format_query_rewrite_suggestions(recommendations: Dict[str, Any],
                                       target_config: Optional[Dict[str, Any]] = None,
                                       db_engine: Optional[str] = None) -> List[str]:
    """Format AI-suggested query rewrites (not yet tested) with copy-paste commands."""
    lines = ["SUGGESTED QUERY REWRITES", ""]

    query_rewrites = recommendations.get("query_rewrites", [])

    for i, rewrite in enumerate(query_rewrites[:3], 1):  # Top 3
        rewrite_type = rewrite.get("type", "Unknown")
        priority = rewrite.get("priority", "medium")
        confidence = rewrite.get("confidence", "unknown")

        lines.append(f"{i}. {rewrite_type} ({priority.upper()} priority, {confidence} confidence)")
        lines.append("")

        # Explanation
        explanation = rewrite.get("explanation", "")
        if explanation:
            wrapped = _wrap_text(explanation, width=100, indent="   ", subsequent_indent="   ")
            lines.extend(wrapped)

        # Expected improvement
        expected_improvement = rewrite.get("expected_improvement", "")
        if expected_improvement:
            wrapped = _wrap_text(f"Expected: {expected_improvement}", width=100, indent="   ", subsequent_indent="   ")
            lines.extend(wrapped)

        # SQL
        sql = rewrite.get("sql", "")
        if sql:
            lines.append("")
            for sql_line in sql.strip().split('\n'):
                lines.append(f"   {sql_line}")

        lines.append("")

        # Add copy-paste test command if we have target config
        if sql and target_config and db_engine:
            test_cmd = _generate_db_test_command(sql, target_config, db_engine)
            if test_cmd:
                lines.append("   Test yourself (set DB_PASSWORD first):")
                # Wrap long commands
                if len(test_cmd) > 90:
                    lines.append(f"   {test_cmd[:90]}")
                    lines.append(f"      {test_cmd[90:]}")
                else:
                    lines.append(f"   {test_cmd}")
                lines.append("")

        # Trade-offs
        trade_offs = rewrite.get("trade_offs", "")
        if trade_offs:
            wrapped = _wrap_text(f"Trade-offs: {trade_offs}", width=100, indent="   ", subsequent_indent="   ")
            lines.extend(wrapped)

        lines.append("")

    lines.append("Note: These rewrites have not been tested. Use commands above to test manually.")
    lines.append("")

    return lines


def _format_readyset_cacheability(readyset_analysis: Dict[str, Any],
                                   readyset_cacheability: Dict[str, Any]) -> List[str]:
    """Format ReadySet cacheability results."""
    lines = ["🚀 READYSET CACHEABILITY", ""]

    # Use actual ReadySet analysis if available, otherwise use static analysis
    if readyset_analysis.get("success"):
        final_verdict = readyset_analysis.get("final_verdict") or {}
        cacheable = final_verdict.get("cacheable", False)
        confidence = final_verdict.get("confidence", "unknown")
        method = final_verdict.get("method", "unknown")
        cached = final_verdict.get("cached", False)

        status = "CACHEABLE ✅" if cacheable else "NOT CACHEABLE ❌"
        lines.append(f"Status: {status}")
        lines.append(f"Confidence: {confidence}")
        lines.append(f"Method: {method}")
        lines.append("")

        # Explain result
        explain_result = readyset_analysis.get("explain_cache_result") or {}
        if explain_result:
            explanation = explain_result.get("explanation", "")
            if explanation:
                lines.append(f"Explanation: {explanation}")

            issues = explain_result.get("issues", [])
            if issues:
                lines.append("")
                lines.append("Issues:")
                for issue in issues:
                    lines.append(f"  • {issue}")

            # Cache status
            create_result = readyset_analysis.get("create_cache_result") or {}
            if cacheable and create_result.get("already_cached"):
                lines.append("")
                lines.append("ℹ️  Query already cached in ReadySet")
            elif cacheable and cached:
                lines.append("")
                lines.append("✅ Cache created successfully in ReadySet")
            elif cacheable and create_result:
                error = create_result.get("error", "Unknown error")
                lines.append("")
                lines.append(f"⚠️  Cache creation failed: {error}")

    elif readyset_cacheability.get("checked"):
        cacheable = readyset_cacheability.get("cacheable", False)
        confidence = readyset_cacheability.get("confidence", "unknown")
        method = readyset_cacheability.get("method", "static_analysis")

        status = "CACHEABLE ✅" if cacheable else "NOT CACHEABLE ❌"
        lines.append(f"Status: {status}")
        lines.append(f"Confidence: {confidence}")
        lines.append(f"Method: {method}")

        explanation = readyset_cacheability.get("explanation", "")
        if explanation:
            lines.append("")
            lines.append(f"{explanation}")

    return lines


def _format_additional_recommendations(optimization_insights: Dict[str, Any]) -> List[str]:
    """Brief list of other optimization opportunities."""
    opportunities = optimization_insights.get("optimization_opportunities", [])
    if not opportunities:
        return []

    lines = ["💡 ADDITIONAL RECOMMENDATIONS", ""]

    for i, opp in enumerate(opportunities[:3], 1):  # Top 3
        description = opp.get("description", "")
        priority = opp.get("priority", "MEDIUM")
        rationale = opp.get("rationale", "")

        lines.append(f"{i}. [{priority}] {description}")
        if rationale:
            lines.append(f"   {rationale}")
        lines.append("")

    return lines


def _format_next_steps(formatted_output: Dict[str, Any],
                      rewrite_testing: Dict[str, Any],
                      recommendations: Dict[str, Any],
                      metadata: Dict[str, Any]) -> List[str]:
    """Actionable next steps for the user."""
    lines = ["📝 NEXT STEPS", ""]

    # Quick win from tested rewrites
    if rewrite_testing.get("tested"):
        rewrite_results = rewrite_testing.get("rewrite_results", [])
        baseline_skipped = rewrite_testing.get("baseline_skipped", False)

        if not baseline_skipped and rewrite_results:
            # Find best performing rewrite
            best_rewrite = None
            best_improvement = 0

            for result in rewrite_results:
                if result.get("success") and result.get("recommendation") not in ["advisory_ddl"]:
                    perf = result.get("performance") or {}
                    was_skipped = result.get("was_skipped", False) or perf.get("was_skipped", False)
                    if not was_skipped:
                        improvement = ((result.get("improvement") or {}).get("overall") or {}).get("improvement_pct", 0)
                        if improvement > best_improvement:
                            best_improvement = improvement
                            best_rewrite = result

            if best_rewrite and best_improvement >= 5:
                rewrite_time = (best_rewrite.get("performance") or {}).get("execution_time_ms", 0)
                lines.append(f"• Apply tested rewrite ({rewrite_time:.1f}ms, {best_improvement:+.1f}% faster)")

    # Index suggestions
    if recommendations.get("available"):
        index_suggestions = recommendations.get("index_suggestions", [])
        if index_suggestions:
            lines.append("• Create recommended indexes above for long-term improvement")

    # Analysis ID and continuation hint
    analysis_id = metadata.get("analysis_id", "")
    if analysis_id:
        lines.append("")
        lines.append(f"Continue this conversation:")
        lines.append(f"  rdst analyze --hash {analysis_id} --interactive")
        lines.append("")
        lines.append(f"List recent queries:  rdst query list --limit 5")

    return lines
