"""Bounded read-only database probes for Ask value grounding."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from features.ask.engine.ask3.phases.execute import _execute_mysql, _execute_postgres
from features.ask.sql_validation import validate_sql_for_ask

VALUE_PROBE_MAX_CALLS = 2
VALUE_PROBE_TIMEOUT_SECONDS = 2


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
    diagnostics = {
        "version": "ask-value-probe-budget-v1",
        "max_calls": VALUE_PROBE_MAX_CALLS,
        "timeout_seconds": VALUE_PROBE_TIMEOUT_SECONDS,
        "calls": 0,
        "successful_calls": 0,
        "failed_calls": 0,
        "blocked_calls": 0,
        "exhausted": False,
    }
    ctx.db_probe_diagnostics = diagnostics

    def execute(sql: str, target_config: dict[str, Any]) -> dict[str, Any]:
        if diagnostics["calls"] >= VALUE_PROBE_MAX_CALLS:
            diagnostics["blocked_calls"] += 1
            diagnostics["exhausted"] = True
            return _failed_probe(
                "Ask value probe call budget exhausted",
                "probe_budget_exhausted",
            )
        diagnostics["calls"] += 1
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
                result = db_executor(validated_sql, target_config)
            elif "mysql" in str(ctx.db_type).casefold():
                result = _execute_mysql(
                    validated_sql,
                    target_config,
                    VALUE_PROBE_TIMEOUT_SECONDS,
                    ctx.target,
                )
            else:
                result = _execute_postgres(
                    validated_sql,
                    target_config,
                    VALUE_PROBE_TIMEOUT_SECONDS,
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
        diagnostics["exhausted"] = diagnostics["calls"] >= VALUE_PROBE_MAX_CALLS
        return result

    return execute


__all__ = [
    "VALUE_PROBE_MAX_CALLS",
    "VALUE_PROBE_TIMEOUT_SECONDS",
    "create_value_probe_executor",
]
