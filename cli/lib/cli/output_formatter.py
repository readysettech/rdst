"""
RDST Clean Output Formatter

Provides Claude Code-style clean, scannable formatting for RDST analyze results.
Removes runtime progress noise and presents information in a hierarchical, actionable format.

Uses the RDST UI theme system for consistent styling across all output.
"""

from typing import Dict, Any, List, Optional
import textwrap

from lib.ui import (
    get_console,
    StyleTokens,
    AnalysisHeader,
    SectionBox,
    QueryPanel,
    StatusLine,
    Rule,
    Icons,
    Text,
    Group,
    MessagePanel,
    NoticePanel,
    NextStepsBuilder,
)
from lib.ui.theme import duration_style, impact_style


def _generate_db_test_command(
    sql: str, target_config: Dict[str, Any], db_engine: str
) -> Optional[str]:
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
    sql_clean = sql.strip().rstrip(";")
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
    for line in error.split("\n"):
        line = line.strip()
        # Skip LINE 1: and ^ pointer lines
        if line.startswith("LINE 1:") or line.startswith("^"):
            continue
        # Skip file paths
        if line.startswith('File "') or line.startswith("cursor."):
            continue
        # Keep HINT lines but clean them
        if line.startswith("HINT:"):
            clean_lines.append(line)
        elif line:
            clean_lines.append(line)

    result = " ".join(clean_lines[:2])  # Max 2 meaningful lines

    # Extract just the error type and message for PostgreSQL errors
    # e.g., "UndefinedFunction: operator does not exist: text = integer"
    if ": " in result and (
        "Error" in result or "Exception" in result or "Function" in result
    ):
        # Keep just the error description
        parts = result.split(": ", 1)
        if len(parts) > 1:
            return parts[1][:200]

    return result[:300]  # Limit length


def _wrap_text(
    text: str, width: int = 100, indent: str = "", subsequent_indent: str = ""
) -> List[str]:
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
        break_on_hyphens=False,
    )

    return wrapped.split("\n")


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
        if (
            not formatted_output
            or not isinstance(formatted_output, dict)
            or not formatted_output.get("success", True)
        ):
            return _format_from_raw_workflow(workflow_result)

        lines = []

        # Header box
        lines.extend(_format_header(formatted_output))
        lines.append("")

        # Query - use normalized/parameterized version for privacy (no PII)
        metadata = formatted_output.get("metadata") or {}
        # Prefer normalized_query > parameterized_sql > query
        query = (
            metadata.get("normalized_query")
            or metadata.get("parameterized_sql")
            or metadata.get("query", "")
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
            lines.extend(
                _format_tested_optimizations(rewrite_testing, target_config, db_engine)
            )
            lines.append(_divider())
        elif rewrite_testing.get("skipped_reason") == "parameterized_query":
            lines.append(
                _capture(
                    NoticePanel(
                        title="REWRITE TESTING SKIPPED",
                        description="This query contains parameter placeholders ($1, $2 or ?) without actual values.\nQuery rewrites were suggested but could not be tested.",
                        variant="warning",
                        bullets=[
                            "Query was captured from rdst top using prepared statements",
                            "Query was normalized from performance_schema without stored parameters",
                        ],
                        bullets_header="This typically happens when:",
                        action_hint="To test rewrites with actual execution times:",
                        action_command='rdst analyze --query "SELECT ... WHERE id = 123"',
                    )
                )
            )
            lines.append("")
            lines.append(_divider())

        # Index recommendations (clear, actionable)
        recommendations = formatted_output.get("recommendations") or {}
        if recommendations.get("available") and recommendations.get(
            "index_suggestions"
        ):
            lines.extend(_format_index_recommendations(recommendations))
            lines.append(_divider())

        # Query rewrite suggestions (AI recommended, not yet tested)
        if recommendations.get("available") and recommendations.get("query_rewrites"):
            # Only show if not already in tested optimizations
            if not (
                rewrite_testing.get("tested") and rewrite_testing.get("rewrite_results")
            ):
                lines.extend(
                    _format_query_rewrite_suggestions(
                        recommendations, target_config, db_engine
                    )
                )
                lines.append(_divider())

        # Readyset cacheability
        readyset_analysis = workflow_result.get("readyset_analysis") or {}
        readyset_cacheability = formatted_output.get("readyset_cacheability") or {}
        if readyset_analysis.get("success") or readyset_cacheability.get("checked"):
            lines.extend(
                _format_readyset_cacheability(readyset_analysis, readyset_cacheability)
            )
            lines.append(_divider())

        # Optimization insights (additional recommendations)
        optimization_insights = formatted_output.get("optimization_insights") or {}
        if optimization_insights.get("available"):
            lines.extend(_format_additional_recommendations(optimization_insights))
            lines.append(_divider())

        # Next steps (actionable)
        readyset_checked = readyset_cacheability.get("checked", False)
        lines.extend(
            _format_next_steps(
                formatted_output,
                rewrite_testing,
                recommendations,
                metadata,
                readyset_checked,
            )
        )

        return "\n".join(lines)

    except Exception as e:
        # Last resort fallback
        return f"Analysis completed but formatting failed: {str(e)}\n\nRaw result available in registry."


def _format_from_raw_workflow(workflow_result: Dict[str, Any]) -> str:
    """Format from raw workflow results when FormatFinalResults failed."""
    lines = []

    target = workflow_result.get("target", "unknown")
    explain_results = workflow_result.get("explain_results") or {}
    db_engine = explain_results.get("database_engine", "")
    target_config = workflow_result.get("target_config") or {}
    storage_result = workflow_result.get("storage_result") or {}
    analysis_id = (
        (storage_result.get("analysis_id") or "")[:12] if storage_result else ""
    )

    llm_analysis = workflow_result.get("llm_analysis") or {}
    llm_info = None
    token_usage = llm_analysis.get("token_usage")
    if token_usage:
        tokens_in = token_usage.get("input", 0)
        tokens_out = token_usage.get("output", 0)
        total = token_usage.get("total", tokens_in + tokens_out)
        cost = token_usage.get("estimated_cost_usd", 0)
        model = llm_analysis.get("llm_model", "claude")
        llm_info = {"model": model, "tokens": total, "cost": cost}

    lines.append(
        _capture(
            AnalysisHeader(
                target=target,
                engine=db_engine,
                analysis_id=analysis_id if analysis_id else None,
                llm_info=llm_info,
            )
        )
    )
    lines.append("")

    query = (
        workflow_result.get("normalized_query")
        or workflow_result.get("parameterized_sql")
        or workflow_result.get("query", "")
    )
    if query:
        lines.extend(_format_query(query))
        lines.append(_divider())

    if explain_results and explain_results.get("success"):
        llm_analysis = workflow_result.get("llm_analysis") or {}
        lines.extend(
            _format_performance_summary_from_workflow(explain_results, llm_analysis)
        )
    else:
        error_text = explain_results.get("error", "")
        clean_error = (
            _clean_error_message(error_text) if error_text else "Unknown error"
        )

        error_lower = error_text.lower()
        if "operator does not exist" in error_lower:
            hint = "Check parameter types match the column types (e.g., use 'movie' not 123)"
        elif "column" in error_lower and "does not exist" in error_lower:
            hint = "Check that the column name is correct and exists in the table"
        elif "relation" in error_lower and "does not exist" in error_lower:
            hint = "Check that the table name is correct"
        elif "permission denied" in error_lower:
            hint = "Check database user has SELECT permissions"
        elif "connection" in error_lower or "refused" in error_lower:
            hint = "Check database connectivity with 'rdst configure list'"
        elif "syntax error" in error_lower:
            hint = "Check SQL syntax"
        else:
            hint = "Review the query and try again"

        lines.append(
            _capture(
                MessagePanel(
                    clean_error,
                    variant="error",
                    title="QUERY EXECUTION FAILED",
                    hint=hint,
                )
            )
        )
        lines.append("")
        lines.append(_divider())
        return "\n".join(lines)

    lines.append("")
    lines.append(_divider())

    llm_analysis = workflow_result.get("llm_analysis") or {}
    explain_failed = not explain_results.get("success", False)
    if (
        llm_analysis
        and not llm_analysis.get("success")
        and llm_analysis.get("error")
        and not explain_failed
    ):
        error_msg = _clean_error_message(llm_analysis.get("error", "Unknown error"))
        lines.append(
            _capture(
                MessagePanel(
                    error_msg,
                    variant="warning",
                    title="AI ANALYSIS ERROR",
                    hint="Check your API key and provider settings with 'rdst configure llm'",
                )
            )
        )
        lines.append("")
        lines.append(_divider())

    if llm_analysis and llm_analysis.get("success"):
        index_recs = llm_analysis.get("index_recommendations") or []
        if index_recs:
            lines.extend(_format_index_recommendations_from_llm(index_recs))
            lines.append(_divider())

    # Tested rewrites
    rewrite_results = workflow_result.get("rewrite_test_results") or {}

    if (
        rewrite_results
        and rewrite_results.get("skipped_reason") == "parameterized_query"
    ):
        lines.append(
            _capture(
                NoticePanel(
                    title="REWRITE TESTING SKIPPED",
                    description="This query contains parameter placeholders ($1, $2 or ?) without actual values.\nQuery rewrites were suggested but could not be tested.",
                    variant="warning",
                    bullets=[
                        "Query was captured from rdst top using prepared statements",
                        "Query was normalized from performance_schema without stored parameters",
                    ],
                    bullets_header="This typically happens when:",
                    action_hint="To test rewrites with actual execution times:",
                    action_command='rdst analyze --query "SELECT ... WHERE id = 123"',
                )
            )
        )
        lines.append("")
        lines.append(_divider())
    elif rewrite_results and rewrite_results.get("success"):
        lines.extend(
            _format_tested_optimizations(rewrite_results, target_config, db_engine)
        )
        lines.append(_divider())

    if llm_analysis and llm_analysis.get("success"):
        analysis_results = llm_analysis.get("analysis_results") or {}
        opps = analysis_results.get("optimization_opportunities") or []
        if opps:
            lines.extend(
                _format_additional_recommendations({"optimization_opportunities": opps})
            )
            lines.append(_divider())

    next_steps = NextStepsBuilder(title=f"{Icons.MEMO} NEXT STEPS")

    if rewrite_results and rewrite_results.get("success"):
        baseline_skipped = rewrite_results.get("baseline_skipped", False)
        if not baseline_skipped:
            tested_rewrites = rewrite_results.get("rewrite_results", [])
            best_rewrite = None
            best_improvement = 0

            for result in tested_rewrites:
                if result.get("success") and result.get("recommendation") not in [
                    "advisory_ddl"
                ]:
                    perf = result.get("performance") or {}
                    was_skipped = result.get("was_skipped", False) or perf.get(
                        "was_skipped", False
                    )
                    if not was_skipped:
                        improvement = (
                            (result.get("improvement") or {}).get("overall") or {}
                        ).get("improvement_pct", 0)
                        if improvement > best_improvement:
                            best_improvement = improvement
                            best_rewrite = result

            if best_rewrite and best_improvement >= 5:
                rewrite_time = (best_rewrite.get("performance") or {}).get(
                    "execution_time_ms", 0
                )
                next_steps.add_step(
                    "Apply tested rewrite",
                    f"{rewrite_time:.1f}ms, {best_improvement:+.1f}% faster",
                )

    if llm_analysis and llm_analysis.get("success"):
        index_recs = llm_analysis.get("index_recommendations") or []
        if index_recs:
            next_steps.add_step(
                "Create recommended indexes",
                "Long-term improvement",
            )

    if analysis_id:
        readyset_analysis = workflow_result.get("readyset_analysis") or {}
        if not readyset_analysis.get("success"):
            next_steps.add_step(
                f"rdst analyze --hash {analysis_id} --readyset-cache",
                "Test Readyset caching",
            )
        next_steps.add_step(
            f"rdst analyze --hash {analysis_id} --interactive",
            "Ask follow-up questions",
        )
        next_steps.add_step(
            f"rdst query show --hash {analysis_id}",
            "View saved query details",
        )

    lines.append(_capture(next_steps))
    return "\n".join(lines)


def _format_header(formatted_output: Dict[str, Any]) -> List[str]:
    """Create top box with key metadata using AnalysisHeader component."""
    metadata = formatted_output.get("metadata") or {}
    target = metadata.get("target", "unknown")
    db_engine = metadata.get("database_engine", "")
    analysis_id = metadata.get("analysis_id", "")
    llm_info = metadata.get("llm_info", {})

    return [
        _capture(
            AnalysisHeader(
                target=target,
                engine=db_engine,
                analysis_id=analysis_id,
                llm_info=llm_info,
            )
        )
    ]


def _capture(renderable) -> str:
    console = get_console()
    with console.capture() as capture:
        console.print(renderable)
    return capture.get().rstrip()


def _divider() -> str:
    """Visual section separator using Rule component."""
    return _capture(Rule())


def _format_query(query: str) -> List[str]:
    return [_capture(QueryPanel(query.strip(), title="Query"))]


def _format_performance_summary_from_workflow(
    explain_results: Dict[str, Any],
    llm_analysis: Dict[str, Any],
) -> List[str]:
    exec_time = explain_results.get("execution_time_ms", 0)
    explain_skipped = explain_results.get("explain_analyze_skipped", False)

    analysis_results = (
        llm_analysis.get("analysis_results") or {}
        if llm_analysis.get("success")
        else {}
    )
    performance = analysis_results.get("performance_assessment") or {}

    if llm_analysis and llm_analysis.get("success"):
        overall_rating = performance.get("overall_rating", "unknown")
        efficiency_score = performance.get("efficiency_score", 0)
        exec_rating = performance.get("execution_time_rating", "")

        if explain_skipped:
            line = (
                "Query Execution Time: N/A (EXPLAIN only) | Rating: "
                f"{overall_rating.upper()} ({efficiency_score}/100)"
            )
        elif exec_rating and exec_rating != "unknown":
            line = (
                f"Query Execution Time: {exec_time:.1f}ms ({exec_rating}) | "
                f"Rating: {overall_rating.upper()} ({efficiency_score}/100)"
            )
        else:
            line = (
                f"Query Execution Time: {exec_time:.1f}ms | Rating: "
                f"{overall_rating.upper()} ({efficiency_score}/100)"
            )
    else:
        if explain_skipped:
            line = "Query Execution Time: N/A (EXPLAIN only - query not executed)"
        else:
            line = f"Query Execution Time: {exec_time:.1f}ms"

    content_parts: List[Any] = [line, Text("")]
    content_parts.append(
        StatusLine("Rows Examined", f"{explain_results.get('rows_examined', 0):,}")
    )
    content_parts.append(
        StatusLine("Rows Returned", f"{explain_results.get('rows_returned', 0):,}")
    )
    cost = explain_results.get("cost_estimate", 0)
    if cost > 0:
        content_parts.append(StatusLine("Cost Estimate", f"{cost:,.0f}"))

    concerns = performance.get("primary_concerns") or []
    if llm_analysis and llm_analysis.get("success") and concerns:
        content_parts.append(Text(""))
        content_parts.append(
            f"[{StyleTokens.SECONDARY}]Primary Concerns:[/{StyleTokens.SECONDARY}]"
        )
        for concern in concerns[:3]:
            for wrapped_line in _wrap_text(
                concern, width=100, indent="  • ", subsequent_indent="    "
            ):
                content_parts.append(wrapped_line)

    return [
        _capture(
            SectionBox(
                f"{Icons.LIGHTNING} Performance Summary",
                content=Group(*content_parts),
            )
        )
    ]


def _format_performance_summary(
    summary: Dict[str, Any], perf_metrics: Dict[str, Any]
) -> List[str]:
    """Compact performance metrics using UI components."""
    exec_time = summary.get("execution_time_ms", 0)
    exec_rating = summary.get("execution_time_rating", "")
    overall_rating = summary.get("overall_rating", "unknown")
    efficiency_score = summary.get("efficiency_score", 0)
    explain_skipped = summary.get("explain_analyze_skipped", False)
    rows_processed = summary.get("rows_processed") or {}
    concerns = summary.get("primary_concerns", [])

    # Build execution time + rating line
    time_text = Text()
    time_text.append("Query Execution Time: ", style="bold")

    if explain_skipped:
        time_text.append("N/A (EXPLAIN only)", style=StyleTokens.MUTED)
    else:
        time_text.append(f"{exec_time:.1f}ms", style=duration_style(exec_time))
        if exec_rating and exec_rating != "unknown":
            time_text.append(f" ({exec_rating})", style=StyleTokens.MUTED)

    time_text.append(" | ", style=StyleTokens.MUTED)
    time_text.append("Rating: ", style="bold")

    # Color code rating
    rating_upper = overall_rating.upper()
    rating_style = (
        StyleTokens.SUCCESS
        if rating_upper in ("EXCELLENT", "GOOD")
        else StyleTokens.WARNING
        if rating_upper in ("FAIR", "MODERATE")
        else StyleTokens.ERROR
    )
    time_text.append(rating_upper, style=rating_style)
    time_text.append(f" ({efficiency_score}/100)", style=StyleTokens.MUTED)

    content_parts: List[Any] = [time_text, Text("")]

    # Row statistics using StatusLine component
    rows_examined = Text("  ")
    rows_examined.append_text(
        StatusLine("Rows Examined", f"{rows_processed.get('examined', 0):,}")
    )
    content_parts.append(rows_examined)

    rows_returned = Text("  ")
    rows_returned.append_text(
        StatusLine("Rows Returned", f"{rows_processed.get('returned', 0):,}")
    )
    content_parts.append(rows_returned)

    cost = summary.get("cost_estimate", 0)
    if cost > 0:
        cost_line = Text("  ")
        cost_line.append_text(StatusLine("Cost Estimate", f"{cost:,.0f}"))
        content_parts.append(cost_line)

    # Primary concerns
    if concerns:
        content_parts.append(Text(""))
        content_parts.append(
            f"[{StyleTokens.WARNING}]Primary Concerns:[/{StyleTokens.WARNING}]"
        )
        for concern in concerns[:3]:
            content_parts.append(
                f"  [{StyleTokens.WARNING}]•[/{StyleTokens.WARNING}] {concern}"
            )

    return [
        _capture(
            SectionBox(
                f"{Icons.LIGHTNING} Performance Summary",
                content=Group(*content_parts),
            )
        )
    ]


def _format_tested_optimizations(
    rewrite_testing: Dict[str, Any],
    target_config: Optional[Dict[str, Any]] = None,
    db_engine: Optional[str] = None,
) -> List[str]:
    """Show tested rewrites with clear improvement metrics and copy-paste test commands."""
    rewrite_results = rewrite_testing.get("rewrite_results", [])
    original_perf = rewrite_testing.get("original_performance") or {}
    baseline_time = original_perf.get("execution_time_ms", 0)
    baseline_skipped = rewrite_testing.get("baseline_skipped", False)

    successful_rewrites = []
    for result in rewrite_results:
        if result.get("success") and result.get("recommendation") not in [
            "advisory_ddl"
        ]:
            perf = result.get("performance") or {}
            was_skipped = result.get("was_skipped", False) or perf.get(
                "was_skipped", False
            )
            if not was_skipped and not baseline_skipped:
                successful_rewrites.append(result)

    content_parts: List[Any] = []

    if baseline_skipped:
        content_parts.append(
            MessagePanel(
                "Original query was skipped (slow execution) - no baseline for comparison",
                variant="warning",
            )
        )

    if not successful_rewrites:
        content_parts.append(
            MessagePanel(
                "No rewrites were tested successfully",
                variant="info",
            )
        )
    else:
        for i, rewrite in enumerate(successful_rewrites[:3], 1):
            metadata = rewrite.get("suggestion_metadata") or {}
            explanation = metadata.get("explanation", "Query rewrite")

            improvement = (rewrite.get("improvement") or {}).get("overall") or {}
            improvement_pct = improvement.get("improvement_pct", 0)

            perf = rewrite.get("performance") or {}
            rewrite_time = perf.get("execution_time_ms", 0)

            # Status with colors
            if improvement_pct >= 10:
                status_icon = (
                    f"[{StyleTokens.SUCCESS}]{Icons.CHECK}[/{StyleTokens.SUCCESS}]"
                )
                status_text = f"[{StyleTokens.SUCCESS}]FASTER[/{StyleTokens.SUCCESS}]"
                pct_display = f"[{StyleTokens.SUCCESS}]+{improvement_pct:.1f}%[/{StyleTokens.SUCCESS}]"
            elif improvement_pct >= 0:
                status_icon = (
                    f"[{StyleTokens.MUTED}]{Icons.ARROW}[/{StyleTokens.MUTED}]"
                )
                status_text = f"[{StyleTokens.MUTED}]SIMILAR[/{StyleTokens.MUTED}]"
                pct_display = f"[{StyleTokens.MUTED}]+{improvement_pct:.1f}%[/{StyleTokens.MUTED}]"
            else:
                status_icon = (
                    f"[{StyleTokens.ERROR}]{Icons.CROSS}[/{StyleTokens.ERROR}]"
                )
                status_text = f"[{StyleTokens.ERROR}]SLOWER[/{StyleTokens.ERROR}]"
                pct_display = (
                    f"[{StyleTokens.ERROR}]{improvement_pct:.1f}%[/{StyleTokens.ERROR}]"
                )

            content_parts.append(
                f"{i}. {status_icon} {status_text} ({pct_display}) - {rewrite_time:.1f}ms"
            )
            content_parts.append(
                f"   [{StyleTokens.MUTED}]{explanation}[/{StyleTokens.MUTED}]"
            )
            content_parts.append(Text(""))

            # SQL preview
            sql = rewrite.get("sql", "")
            if sql:
                for line in sql.strip().split("\n"):
                    content_parts.append(
                        f"   [{StyleTokens.SQL}]{line}[/{StyleTokens.SQL}]"
                    )
                content_parts.append(Text(""))

            # Test command
            if sql and target_config and db_engine:
                test_cmd = _generate_db_test_command(sql, target_config, db_engine)
                if test_cmd:
                    content_parts.append(
                        f"   [{StyleTokens.MUTED}]Test it yourself:[/{StyleTokens.MUTED}]"
                    )
                    content_parts.append(
                        f"   [{StyleTokens.SECONDARY}]{test_cmd}[/{StyleTokens.SECONDARY}]"
                    )
                    content_parts.append(Text(""))

    return [
        _capture(
            SectionBox(
                f"{Icons.CHART} Tested Optimizations", content=Group(*content_parts)
            )
        )
    ]


def _format_index_recommendations_from_llm(
    index_recs: List[Dict[str, Any]],
) -> List[str]:
    if not index_recs:
        return []

    content_parts: List[Any] = []

    for i, idx in enumerate(index_recs[:3], 1):
        rationale = idx.get("rationale", "")
        sql = idx.get("sql", "")
        impact = idx.get("estimated_impact", "UNKNOWN")
        impact_color = impact_style(impact)

        content_parts.append(
            f"{i}. ([{impact_color}]{impact.upper()} IMPACT[/{impact_color}])"
        )
        if sql:
            for line in sql.strip().split("\n"):
                content_parts.append(
                    f"   [{StyleTokens.SQL}]{line}[/{StyleTokens.SQL}]"
                )
        content_parts.append(Text(""))

        if rationale:
            for line in _wrap_text(
                f"Why: {rationale}",
                width=100,
                indent="   ",
                subsequent_indent="   ",
            ):
                content_parts.append(line)

        caveats = idx.get("caveats", [])
        if caveats:
            for caveat in caveats:
                note = caveat.split(": ", 1)[1] if ": " in caveat else caveat
                for line in _wrap_text(
                    f"Note: {note}",
                    width=100,
                    indent="   ",
                    subsequent_indent="         ",
                ):
                    content_parts.append(
                        f"[{StyleTokens.MUTED}]{line}[/{StyleTokens.MUTED}]"
                    )
            content_parts.append(Text(""))

    if len(index_recs) >= 2:
        content_parts.append(
            MessagePanel(
                "These indexes optimize this query. For workload-wide index recommendations across multiple queries: rdst analyze --workload (coming soon)",
                variant="info",
            )
        )

    return [
        _capture(
            SectionBox(
                f"{Icons.TOOL} Recommended Indexes", content=Group(*content_parts)
            )
        )
    ]


def _format_index_recommendations(recommendations: Dict[str, Any]) -> List[str]:
    """Format index recommendations with emphasis on actionable steps."""
    index_suggestions = recommendations.get("index_suggestions", [])
    if not index_suggestions:
        return []

    content_parts: List[Any] = []

    for i, idx in enumerate(index_suggestions[:5], 1):
        idx_type = idx.get("type", "Unknown")
        table = idx.get("table", "")
        expected_benefit = idx.get("expected_benefit", "UNKNOWN").upper()

        # Color code impact
        impact_color = impact_style(expected_benefit)
        content_parts.append(
            f"{i}. [{StyleTokens.SECONDARY}]{idx_type} Index[/{StyleTokens.SECONDARY}] ([{impact_color}]{expected_benefit} IMPACT[/{impact_color}])"
        )
        content_parts.append(Text(""))

        # SQL statement
        sql = idx.get("sql_statement", "")
        if not sql:
            columns = idx.get("columns", [])
            if columns and table:
                columns_str = ", ".join(columns)
                sql = f"CREATE INDEX idx_{table}_{'_'.join(columns[:2])} ON {table}({columns_str});"

        if sql:
            content_parts.append(f"   [{StyleTokens.SQL}]{sql}[/{StyleTokens.SQL}]")
            content_parts.append(Text(""))

        # Rationale
        rationale = idx.get("rationale", "")
        if rationale:
            content_parts.append(
                f"   [{StyleTokens.MUTED}]Why:[/{StyleTokens.MUTED}] {rationale}"
            )

        # Storage impact
        storage_impact = idx.get("storage_impact", "")
        if storage_impact:
            content_parts.append(
                f"   [{StyleTokens.MUTED}]Storage:[/{StyleTokens.MUTED}] {storage_impact}"
            )

        content_parts.append(Text(""))

    return [
        _capture(
            SectionBox(
                f"{Icons.TOOL} Recommended Indexes", content=Group(*content_parts)
            )
        )
    ]


def _format_query_rewrite_suggestions(
    recommendations: Dict[str, Any],
    target_config: Optional[Dict[str, Any]] = None,
    db_engine: Optional[str] = None,
) -> List[str]:
    query_rewrites = recommendations.get("query_rewrites", [])

    content_parts: List[Any] = []

    for i, rewrite in enumerate(query_rewrites[:3], 1):
        rewrite_type = rewrite.get("type", "Unknown")
        priority = rewrite.get("priority", "medium")
        confidence = rewrite.get("confidence", "unknown")

        content_parts.append(
            f"{i}. [{StyleTokens.SECONDARY}]{rewrite_type}[/{StyleTokens.SECONDARY}] "
            f"([{StyleTokens.ACCENT}]{priority.upper()}[/{StyleTokens.ACCENT}] priority, "
            f"{confidence} confidence)"
        )
        content_parts.append(Text(""))

        explanation = rewrite.get("explanation", "")
        if explanation:
            for line in _wrap_text(
                explanation, width=100, indent="   ", subsequent_indent="   "
            ):
                content_parts.append(line)

        expected_improvement = rewrite.get("expected_improvement", "")
        if expected_improvement:
            for line in _wrap_text(
                f"Expected: {expected_improvement}",
                width=100,
                indent="   ",
                subsequent_indent="   ",
            ):
                content_parts.append(
                    f"[{StyleTokens.MUTED}]{line}[/{StyleTokens.MUTED}]"
                )

        sql = rewrite.get("sql", "")
        if sql:
            content_parts.append(Text(""))
            for sql_line in sql.strip().split("\n"):
                content_parts.append(
                    f"   [{StyleTokens.SQL}]{sql_line}[/{StyleTokens.SQL}]"
                )

        content_parts.append(Text(""))

        if sql and target_config and db_engine:
            test_cmd = _generate_db_test_command(sql, target_config, db_engine)
            if test_cmd:
                content_parts.append(
                    f"   [{StyleTokens.MUTED}]Test yourself (set DB_PASSWORD first):[/{StyleTokens.MUTED}]"
                )
                content_parts.append(
                    f"   [{StyleTokens.SECONDARY}]{test_cmd}[/{StyleTokens.SECONDARY}]"
                )
                content_parts.append(Text(""))

        trade_offs = rewrite.get("trade_offs", "")
        if trade_offs:
            for line in _wrap_text(
                f"Trade-offs: {trade_offs}",
                width=100,
                indent="   ",
                subsequent_indent="   ",
            ):
                content_parts.append(
                    f"[{StyleTokens.MUTED}]{line}[/{StyleTokens.MUTED}]"
                )

        content_parts.append(Text(""))

    content_parts.append(
        MessagePanel(
            "These rewrites have not been tested. Use commands above to test manually.",
            variant="info",
        )
    )

    return [
        _capture(
            SectionBox(
                f"{Icons.BULB} Suggested Query Rewrites", content=Group(*content_parts)
            )
        )
    ]


def _format_readyset_cacheability(
    readyset_analysis: Dict[str, Any], readyset_cacheability: Dict[str, Any]
) -> List[str]:
    content_parts: List[Any] = []

    if readyset_analysis.get("success"):
        final_verdict = readyset_analysis.get("final_verdict") or {}
        cacheable = final_verdict.get("cacheable", False)
        confidence = final_verdict.get("confidence", "unknown")
        method = final_verdict.get("method", "unknown")
        cached = final_verdict.get("cached", False)

        status_style = StyleTokens.SUCCESS if cacheable else StyleTokens.ERROR
        status_icon = Icons.CHECK if cacheable else Icons.CROSS
        status_text = "CACHEABLE" if cacheable else "NOT CACHEABLE"

        content_parts.append(
            StatusLine(
                "Status",
                f"[{status_style}]{status_text} {status_icon}[/{status_style}]",
            )
        )
        content_parts.append(StatusLine("Confidence", confidence))
        content_parts.append(StatusLine("Method", method))
        content_parts.append(Text(""))

        explain_result = readyset_analysis.get("explain_cache_result") or {}
        if explain_result:
            explanation = explain_result.get("explanation", "")
            if explanation:
                content_parts.append(
                    f"[{StyleTokens.MUTED}]Explanation:[/{StyleTokens.MUTED}] {explanation}"
                )

            issues = explain_result.get("issues", [])
            if issues:
                content_parts.append(Text(""))
                content_parts.append(
                    f"[{StyleTokens.WARNING}]Issues:[/{StyleTokens.WARNING}]"
                )
                for issue in issues:
                    content_parts.append(
                        f"  [{StyleTokens.MUTED}]•[/{StyleTokens.MUTED}] {issue}"
                    )

    # Also include LLM explanation from cacheability analysis
    if readyset_cacheability.get("success"):
        status_style = (
            StyleTokens.SUCCESS
            if readyset_cacheability.get("cacheable", False)
            else StyleTokens.ERROR
        )
        status_icon = (
            Icons.CHECK
            if readyset_cacheability.get("cacheable", False)
            else Icons.CROSS
        )
        status_text = (
            "CACHEABLE"
            if readyset_cacheability.get("cacheable", False)
            else "NOT CACHEABLE"
        )
        confidence = readyset_cacheability.get("confidence", "unknown")
        method = readyset_cacheability.get("method", "unknown")

        content_parts.append(
            StatusLine(
                "Status",
                f"[{status_style}]{status_text} {status_icon}[/{status_style}]",
            )
        )
        content_parts.append(StatusLine("Confidence", confidence))
        content_parts.append(StatusLine("Method", method))

        explanation = readyset_cacheability.get("explanation", "")
        if explanation:
            content_parts.append(Text(""))
            content_parts.append(
                f"[{StyleTokens.MUTED}]{explanation}[/{StyleTokens.MUTED}]"
            )

    return [
        _capture(
            SectionBox(
                f"{Icons.ROCKET} Readyset Cacheability", content=Group(*content_parts)
            )
        )
    ]


def _format_additional_recommendations(
    optimization_insights: Dict[str, Any],
) -> List[str]:
    """Brief list of other optimization opportunities with theme colors."""
    opportunities = optimization_insights.get("optimization_opportunities", [])
    if not opportunities:
        return []

    content_parts: List[Any] = []

    for i, opp in enumerate(opportunities[:3], 1):
        description = opp.get("description", "")
        priority = opp.get("priority", "MEDIUM").upper()
        rationale = opp.get("rationale", "")

        # Color code priority
        if priority == "HIGH":
            priority_style = StyleTokens.ERROR
        elif priority == "MEDIUM":
            priority_style = StyleTokens.WARNING
        else:
            priority_style = StyleTokens.MUTED

        content_parts.append(
            f"{i}. [{priority_style}][{priority}][/{priority_style}] {description}"
        )
        if rationale:
            content_parts.append(
                f"   [{StyleTokens.MUTED}]{rationale}[/{StyleTokens.MUTED}]"
            )
        content_parts.append(Text(""))

    return [
        _capture(
            SectionBox(
                f"{Icons.BULB} Additional Recommendations",
                content=Group(*content_parts),
            )
        )
    ]


def _format_next_steps(
    formatted_output: Dict[str, Any],
    rewrite_testing: Dict[str, Any],
    recommendations: Dict[str, Any],
    metadata: Dict[str, Any],
    readyset_checked: bool = False,
) -> List[str]:
    """Actionable next steps for the user."""
    analysis_id = metadata.get("analysis_id", "")
    if not analysis_id:
        return []

    next_steps = NextStepsBuilder(title=f"{Icons.MEMO} NEXT STEPS")

    # Quick win from tested rewrites (>= 5% improvement)
    if rewrite_testing.get("tested"):
        rewrite_results = rewrite_testing.get("rewrite_results", [])
        baseline_skipped = rewrite_testing.get("baseline_skipped", False)

        if not baseline_skipped and rewrite_results:
            best_rewrite = None
            best_improvement = 0

            for result in rewrite_results:
                if result.get("success") and result.get("recommendation") not in [
                    "advisory_ddl"
                ]:
                    perf = result.get("performance") or {}
                    was_skipped = result.get("was_skipped", False) or perf.get(
                        "was_skipped", False
                    )
                    if not was_skipped:
                        improvement = (
                            (result.get("improvement") or {}).get("overall") or {}
                        ).get("improvement_pct", 0)
                        if improvement > best_improvement:
                            best_improvement = improvement
                            best_rewrite = result

            if best_rewrite and best_improvement >= 5:
                rewrite_time = (best_rewrite.get("performance") or {}).get(
                    "execution_time_ms", 0
                )
                next_steps.add_step(
                    "Apply tested rewrite",
                    f"{rewrite_time:.1f}ms, {best_improvement:+.1f}% faster",
                )

    # Index suggestions
    if recommendations.get("available"):
        index_suggestions = recommendations.get("index_suggestions", [])
        if index_suggestions:
            next_steps.add_step(
                "Create recommended indexes",
                "Long-term improvement",
            )

    if not readyset_checked:
        next_steps.add_step(
            f"rdst analyze --hash {analysis_id} --readyset-cache",
            "Test Readyset caching",
        )
    next_steps.add_step(
        f"rdst analyze --hash {analysis_id} --interactive",
        "Ask follow-up questions",
    )
    next_steps.add_step(
        f"rdst query show {analysis_id}",
        "View saved query details",
    )

    return [_capture(next_steps)]
