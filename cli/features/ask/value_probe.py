"""Bounded read-only database probes for Ask value grounding."""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any

from features.ask.engine.ask3.phases.execute import _execute_mysql, _execute_postgres
from features.ask.sql_validation import validate_sql_for_ask

VALUE_PROBE_MAX_CALLS = 2
VALUE_PROBE_TIMEOUT_SECONDS = 2
MONTH_AXIS_PROBE_MAX_CALLS = 4
MONTH_AXIS_PROBE_TIMEOUT_SECONDS = 4


def _failed_probe(error: str, error_kind: str) -> dict[str, Any]:
    return {
        "success": False,
        "error": error,
        "error_kind": error_kind,
        "rows": [],
        "columns": [],
    }


def create_value_probe_executor(
    ctx: Any,
    db_executor: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
) -> Callable[[str, dict[str, Any]], dict[str, Any]]:
    """Create a two-call, read-only executor for one value-location check."""
    return _create_bounded_probe_executor(
        ctx,
        db_executor,
        max_calls=VALUE_PROBE_MAX_CALLS,
        timeout_seconds=VALUE_PROBE_TIMEOUT_SECONDS,
        diagnostic_attribute="db_probe_diagnostics",
        version="ask-value-probe-budget-v1",
    )


def create_month_axis_probe_executor(
    ctx: Any,
    db_executor: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
) -> Callable[[str, dict[str, Any]], dict[str, Any]]:
    """Create a four-call executor with one shared month-axis deadline."""
    return _create_bounded_probe_executor(
        ctx,
        db_executor,
        max_calls=MONTH_AXIS_PROBE_MAX_CALLS,
        timeout_seconds=MONTH_AXIS_PROBE_TIMEOUT_SECONDS,
        diagnostic_attribute="month_axis_probe_diagnostics",
        version="ask-month-axis-probe-budget-v1",
    )


def create_period_literal_probe_executor(ctx: Any, db_executor=None):
    """Share three seconds across complete storage, independent result and candidate."""
    return _create_bounded_probe_executor(
        ctx,
        db_executor,
        max_calls=3,
        timeout_seconds=3,
        max_rows=1201,
        diagnostic_attribute="period_literal_probe_diagnostics",
        version="ask-period-literal-budget-v1",
    )


def create_metric_source_probe_executor(ctx: Any, db_executor=None):
    """Share four seconds across relationship, period and candidate proofs."""
    return _create_bounded_probe_executor(
        ctx,
        db_executor,
        max_calls=3,
        timeout_seconds=4,
        max_rows=1001,
        diagnostic_attribute="metric_source_probe_diagnostics",
        version="ask-metric-source-budget-v1",
    )


def create_list_membership_probe_executor(
    ctx: Any, db_executor=None, *, require_complete=False
):
    """Share two seconds between exhaustive scoped value counts and candidate."""
    bounded = _create_bounded_probe_executor(
        ctx,
        db_executor,
        max_calls=2,
        timeout_seconds=2,
        max_rows=1001,
        diagnostic_attribute="list_membership_probe_diagnostics",
        version="ask-list-membership-budget-v1",
    )

    if not require_complete:
        return bounded

    def execute(sql, target_config):
        result = bounded(sql, target_config)
        if result.get("truncated"):
            return _failed_probe(
                "Incomplete membership proof or result", "probe_result_incomplete"
            )
        return result

    return execute


def create_outer_rounding_probe_executor(ctx: Any, db_executor=None):
    """Share one two-second budget between the rounding witness and candidate."""
    return _create_bounded_probe_executor(
        ctx,
        db_executor,
        max_calls=2,
        timeout_seconds=2,
        diagnostic_attribute="outer_rounding_probe_diagnostics",
        version="ask-outer-rounding-budget-v1",
    )


def create_integer_mean_probe_executor(
    ctx: Any, db_executor=None, *, require_complete=False
):
    """Share one two-second budget between the mean proof and candidate."""
    bounded = _create_bounded_probe_executor(
        ctx,
        db_executor,
        max_calls=2,
        timeout_seconds=2,
        diagnostic_attribute="integer_mean_probe_diagnostics",
        version="ask-integer-mean-budget-v1",
    )

    if not require_complete:
        return bounded

    def execute(sql, target_config):
        result = bounded(sql, target_config)
        if result.get("truncated"):
            return _failed_probe(
                "Incomplete integer mean proof or result", "probe_result_incomplete"
            )
        return result

    return execute


def create_text_mean_probe_executor(ctx: Any, db_executor=None):
    """Share two calls and two seconds for numeric-text proof and candidate."""
    return _create_bounded_probe_executor(
        ctx,
        db_executor,
        max_calls=2,
        timeout_seconds=2,
        diagnostic_attribute="text_mean_probe_diagnostics",
        version="ask-text-mean-budget-v1",
    )


def create_null_extremum_probe_executor(ctx: Any, db_executor=None):
    """Share four seconds across original, measured and final singleton checks."""
    return _create_bounded_probe_executor(
        ctx,
        db_executor,
        max_calls=3,
        timeout_seconds=4,
        max_rows=2,
        diagnostic_attribute="null_extremum_probe_diagnostics",
        version="ask-null-extremum-budget-v1",
    )


def create_stable_first_probe_executor(ctx: Any, db_executor=None):
    """Share four seconds across ranking, identity, and candidate checks."""
    bounded = _create_bounded_probe_executor(
        ctx,
        db_executor,
        max_calls=3,
        timeout_seconds=4,
        max_rows=2,
        diagnostic_attribute="stable_first_probe_diagnostics",
        version="ask-stable-first-budget-v1",
    )

    def execute(sql, target_config):
        result = bounded(sql, target_config)
        # The ordinary execution phase does not preserve provider truncation.
        if result.get("truncated"):
            return _failed_probe("Incomplete top-one proof or result", "probe_result_incomplete")
        return result

    return execute


def create_state_lookup_probe_executor(
    ctx: Any, db_executor=None, *, require_complete=False
):
    """Bound label/key, scoped subset and candidate checks by four seconds."""
    bounded = _create_bounded_probe_executor(
        ctx,
        db_executor,
        max_calls=3,
        timeout_seconds=4,
        max_rows=2,
        diagnostic_attribute="state_lookup_probe_diagnostics",
        version="ask-state-lookup-budget-v1",
    )

    if not require_complete:
        return bounded

    def execute(sql, target_config):
        result = bounded(sql, target_config)
        if result.get("truncated"):
            return _failed_probe(
                "Incomplete state lookup proof or result", "probe_result_incomplete"
            )
        return result

    return execute


def create_comparison_ratio_probe_executor(ctx: Any, db_executor=None):
    """Share two seconds and two calls for a single comparison pair and ratio."""
    return _create_bounded_probe_executor(
        ctx,
        db_executor,
        max_calls=2,
        timeout_seconds=2,
        max_rows=2,
        diagnostic_attribute="comparison_ratio_probe_diagnostics",
        version="ask-comparison-ratio-budget-v1",
    )


def create_fraction_precision_probe_executor(ctx: Any, db_executor=None):
    """Share two seconds and two calls for fraction proof and projection."""
    return _create_bounded_probe_executor(
        ctx,
        db_executor,
        max_calls=2,
        timeout_seconds=2,
        max_rows=10_000,
        diagnostic_attribute="fraction_precision_probe_diagnostics",
        version="ask-fraction-precision-budget-v1",
    )


def create_count_name_domain_executor(ctx: Any, db_executor=None):
    """Read one complete label catalog within two seconds."""
    bounded = _create_bounded_probe_executor(
        ctx,
        db_executor,
        max_calls=1,
        timeout_seconds=2,
        max_rows=101,
        diagnostic_attribute="count_name_domain_diagnostics",
        version="ask-count-name-domain-budget-v1",
    )

    def execute(sql, target_config):
        result = bounded(sql, target_config)
        if result.get("truncated"):
            return _failed_probe("Incomplete name catalog", "probe_result_incomplete")
        return result

    return execute


def create_count_name_candidate_executor(ctx: Any, db_executor=None):
    """After routing, share two calls and four seconds for counts and candidate.

    Together with the earlier catalog, this permits three read-only queries
    and at most six seconds of database waiting. Model time is separate.
    """
    bounded = _create_bounded_probe_executor(
        ctx,
        db_executor,
        max_calls=2,
        timeout_seconds=4,
        max_rows=1,
        diagnostic_attribute="count_name_candidate_diagnostics",
        version="ask-count-name-candidate-budget-v1",
    )

    def execute(sql, target_config):
        result = bounded(sql, target_config)
        if result.get("truncated"):
            return _failed_probe(
                "Incomplete count proof or result", "probe_result_incomplete"
            )
        return result

    return execute


def create_identifier_quoting_probe_executor(ctx: Any, db_executor=None):
    """Share two calls and two seconds for keyword proof and a complete result."""
    bounded = _create_bounded_probe_executor(
        ctx,
        db_executor,
        max_calls=2,
        timeout_seconds=2,
        max_rows=10_000,
        diagnostic_attribute="identifier_quoting_probe_diagnostics",
        version="ask-identifier-quoting-budget-v1",
    )

    def execute(sql, target_config):
        result = bounded(sql, target_config)
        if result.get("truncated"):
            return _failed_probe(
                "Incomplete identifier quoting proof or result",
                "probe_result_incomplete",
            )
        return result

    return execute


def create_percentage_threshold_probe_executor(
    ctx: Any, db_executor=None, *, require_complete=False
):
    """Share two calls and two seconds for fraction proof and threshold query."""
    bounded = _create_bounded_probe_executor(
        ctx,
        db_executor,
        max_calls=2,
        timeout_seconds=2,
        diagnostic_attribute="percentage_threshold_probe_diagnostics",
        version="ask-percentage-threshold-budget-v1",
    )

    if not require_complete:
        return bounded

    def execute(sql, target_config):
        result = bounded(sql, target_config)
        if result.get("truncated"):
            return _failed_probe(
                "Incomplete percentage threshold proof or result",
                "probe_result_incomplete",
            )
        return result

    return execute


def create_ranked_union_probe_executor(ctx: Any, db_executor=None):
    """Prove two singleton selections and one combined slice in four seconds."""
    return _create_bounded_probe_executor(
        ctx,
        db_executor,
        max_calls=3,
        timeout_seconds=4,
        max_rows=2,
        diagnostic_attribute="ranked_union_probe_diagnostics",
        version="ask-ranked-union-budget-v1",
    )


def create_calendar_day_probe_executor(ctx: Any, db_executor=None):
    """Bound complete storage, date-result and candidate checks to four seconds."""
    return _create_bounded_probe_executor(
        ctx,
        db_executor,
        max_calls=3,
        timeout_seconds=4,
        max_rows=10000,
        diagnostic_attribute="calendar_day_probe_diagnostics",
        version="ask-calendar-day-budget-v1",
    )


def create_projection_order_probe_executor(ctx: Any, db_executor=None):
    """Verify one complete output permutation under the shared two-second limit."""
    return _create_bounded_probe_executor(
        ctx,
        db_executor,
        max_calls=1,
        timeout_seconds=2,
        max_rows=10000,
        diagnostic_attribute="projection_order_probe_diagnostics",
        version="ask-projection-order-budget-v1",
    )


def create_projection_contract_probe_executor(ctx: Any, db_executor=None):
    """Bound one complete projection candidate by two seconds and 10,000 rows."""
    return _create_bounded_probe_executor(
        ctx,
        db_executor,
        max_calls=1,
        timeout_seconds=2,
        max_rows=10_000,
        diagnostic_attribute="projection_contract_probe_diagnostics",
        version="ask-projection-contract-budget-v1",
    )


def create_endpoint_component_probe_executor(ctx: Any, db_executor=None):
    """Bound two read-only calls to two seconds each, excluding model time."""
    diagnostics = {
        "version": "ask-endpoint-component-budget-v2",
        "max_calls": 2,
        "timeout_seconds": 4,
        "per_call_timeout_seconds": 2,
        "calls": 0,
        "successful_calls": 0,
        "failed_calls": 0,
        "blocked_calls": 0,
        "exhausted": False,
        "queries": [],
    }
    ctx.endpoint_component_probe_diagnostics = diagnostics

    def execute(sql, target_config):
        if diagnostics["calls"] >= 2:
            diagnostics["blocked_calls"] += 1
            diagnostics["exhausted"] = True
            return _failed_probe(
                "Endpoint component call budget exhausted", "probe_budget_exhausted"
            )
        diagnostics["calls"] += 1
        bounded = _create_bounded_probe_executor(
            ctx,
            db_executor,
            max_calls=1,
            timeout_seconds=2,
            max_rows=10001,
            diagnostic_attribute="endpoint_component_probe_diagnostics",
            version="ask-endpoint-component-single-query-v1",
        )
        current = ctx.endpoint_component_probe_diagnostics
        try:
            result = bounded(sql, target_config)
            if result.get("truncated"):
                result = _failed_probe(
                    "Incomplete endpoint proof or result", "probe_result_incomplete"
                )
            diagnostics[
                "successful_calls" if result.get("success") else "failed_calls"
            ] += 1
            return result
        finally:
            diagnostics["queries"].append(dict(current))
            diagnostics["exhausted"] = diagnostics["calls"] >= 2
            ctx.endpoint_component_probe_diagnostics = diagnostics

    return execute


def create_month_component_probe_executor(ctx: Any, db_executor=None):
    """Share two calls and four seconds between encoding proof and candidate."""
    bounded = _create_bounded_probe_executor(
        ctx,
        db_executor,
        max_calls=2,
        timeout_seconds=4,
        max_rows=13,
        diagnostic_attribute="month_component_probe_diagnostics",
        version="ask-month-component-budget-v1",
    )

    def execute(sql, target_config):
        result = bounded(sql, target_config)
        if result.get("truncated"):
            return _failed_probe(
                "Incomplete month component proof or result", "probe_result_incomplete"
            )
        return result

    return execute


def _create_bounded_probe_executor(
    ctx: Any,
    db_executor: Callable[[str, dict[str, Any]], dict[str, Any]] | None,
    *,
    max_calls: int,
    timeout_seconds: float,
    diagnostic_attribute: str,
    version: str,
    max_rows: int = 1,
) -> Callable[[str, dict[str, Any]], dict[str, Any]]:
    diagnostics = {
        "version": version,
        "max_calls": max_calls,
        "timeout_seconds": timeout_seconds,
        "calls": 0,
        "successful_calls": 0,
        "failed_calls": 0,
        "blocked_calls": 0,
        "exhausted": False,
    }
    setattr(ctx, diagnostic_attribute, diagnostics)
    deadline = time.monotonic() + timeout_seconds

    def execute(sql: str, target_config: dict[str, Any]) -> dict[str, Any]:
        if diagnostics["calls"] >= max_calls:
            diagnostics["blocked_calls"] += 1
            diagnostics["exhausted"] = True
            return _failed_probe(
                "Ask value probe call budget exhausted",
                "probe_budget_exhausted",
            )
        diagnostics["calls"] += 1
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            diagnostics["failed_calls"] += 1
            diagnostics["exhausted"] = True
            return _failed_probe(
                "Ask value probe deadline exceeded",
                "probe_timeout",
            )
        validation = validate_sql_for_ask(sql, enforce_result_limit=False)
        if not validation.get("is_valid"):
            diagnostics["failed_calls"] += 1
            return _failed_probe(
                "SQL validation failed: " + "; ".join(validation.get("issues", [])),
                "probe_validation_failed",
            )
        validated_sql = str(validation.get("validated_sql") or sql)
        started = time.monotonic()
        try:
            if db_executor is not None:
                pool = ThreadPoolExecutor(max_workers=1)
                future = pool.submit(db_executor, validated_sql, target_config)
                try:
                    result = future.result(timeout=remaining)
                except FutureTimeoutError:
                    future.cancel()
                    result = _failed_probe(
                        "Ask value probe deadline exceeded",
                        "probe_timeout",
                    )
                finally:
                    pool.shutdown(wait=False, cancel_futures=True)
            elif "mysql" in str(ctx.db_type).casefold():
                result = _execute_mysql(
                    validated_sql,
                    target_config,
                    max(0.001, remaining),
                    ctx.target,
                )
            else:
                result = _execute_postgres(
                    validated_sql,
                    target_config,
                    max(0.001, remaining),
                    ctx.target,
                )
        except Exception as exc:
            result = _failed_probe(str(exc), "probe_execution_failed")
        diagnostics["last_latency_ms"] = (time.monotonic() - started) * 1000
        if not isinstance(result, dict):
            result = _failed_probe(
                "Ask value probe returned an invalid response",
                "probe_execution_failed",
            )
        rows = result.get("rows", [])
        if not isinstance(rows, (list, tuple)) or len(rows) > max_rows:
            result = _failed_probe(
                "Ask value probe returned an invalid result shape",
                "probe_result_invalid",
            )
        if result.get("success"):
            diagnostics["successful_calls"] += 1
        else:
            diagnostics["failed_calls"] += 1
        diagnostics["exhausted"] = diagnostics["calls"] >= max_calls
        return result

    return execute


__all__ = [
    "MONTH_AXIS_PROBE_MAX_CALLS",
    "MONTH_AXIS_PROBE_TIMEOUT_SECONDS",
    "VALUE_PROBE_MAX_CALLS",
    "VALUE_PROBE_TIMEOUT_SECONDS",
    "create_month_axis_probe_executor",
    "create_value_probe_executor",
]


def create_grouped_extremum_probe_executor(ctx, db_executor):
    return _create_bounded_probe_executor(
        ctx,
        db_executor,
        max_calls=2,
        timeout_seconds=4,
        max_rows=101,
        diagnostic_attribute="grouped_extremum_probe_diagnostics",
        version="grouped-extremum-budget-v1",
    )


def create_name_format_probe_executor(ctx, db_executor):
    return _create_bounded_probe_executor(
        ctx,
        db_executor,
        max_calls=1,
        timeout_seconds=2,
        max_rows=10000,
        diagnostic_attribute="name_format_probe_diagnostics",
        version="name-format-budget-v1",
    )


def create_output_completion_probe_executor(ctx: Any, db_executor=None):
    """Verify one complete augmented result within two seconds."""
    bounded = _create_bounded_probe_executor(
        ctx,
        db_executor,
        max_calls=1,
        timeout_seconds=2,
        max_rows=10000,
        diagnostic_attribute="output_completion_probe_diagnostics",
        version="ask-output-completion-budget-v1",
    )

    def execute(sql, target_config):
        result = bounded(sql, target_config)
        if result.get("truncated"):
            return {
                "success": False,
                "error": "Incomplete output completion result",
                "rows": [],
            }
        return result

    return execute


def create_matched_percentage_probe_executor(ctx: Any, db_executor=None):
    """Bound one aggregate population witness and one percentage candidate."""
    bounded = _create_bounded_probe_executor(
        ctx,
        db_executor,
        max_calls=2,
        timeout_seconds=4,
        max_rows=1,
        diagnostic_attribute="matched_percentage_probe_diagnostics",
        version="ask-matched-percentage-budget-v1",
    )

    def execute(sql, target_config):
        result = bounded(sql, target_config)
        if result.get("truncated"):
            return {
                "success": False,
                "error": "Incomplete scoped extremum result",
                "rows": [],
            }
        return result

    return execute


def create_scaled_ratio_probe_executor(ctx: Any, db_executor=None):
    """Bound one exact operand probe and one scaled ratio candidate."""
    bounded = _create_bounded_probe_executor(
        ctx,
        db_executor,
        max_calls=2,
        timeout_seconds=4,
        max_rows=1,
        diagnostic_attribute="scaled_ratio_probe_diagnostics",
        version="ask-scaled-ratio-budget-v1",
    )

    def execute(sql, target_config):
        result = bounded(sql, target_config)
        if result.get("truncated"):
            return {
                "success": False,
                "error": "Incomplete scaled ratio result",
                "rows": [],
            }
        return result

    return execute


def create_occurrence_percentage_probe_executor(ctx: Any, db_executor=None):
    """Bound one distinct/occurrence count proof and one candidate."""
    bounded = _create_bounded_probe_executor(
        ctx,
        db_executor,
        max_calls=2,
        timeout_seconds=4,
        max_rows=1,
        diagnostic_attribute="occurrence_percentage_probe_diagnostics",
        version="ask-occurrence-percentage-budget-v1",
    )

    def execute(sql, target_config):
        result = bounded(sql, target_config)
        if result.get("truncated"):
            return {
                "success": False,
                "error": "Incomplete occurrence percentage result",
                "rows": [],
            }
        return result

    return execute
