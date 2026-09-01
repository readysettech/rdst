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


def _create_bounded_probe_executor(
    ctx: Any,
    db_executor: Callable[[str, dict[str, Any]], dict[str, Any]] | None,
    *,
    max_calls: int,
    timeout_seconds: float,
    diagnostic_attribute: str,
    version: str,
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
        if not isinstance(rows, (list, tuple)) or len(rows) > 1:
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
