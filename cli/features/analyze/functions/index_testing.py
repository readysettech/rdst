"""Planner verification of index recommendations with hypopg.

hypopg creates hypothetical indexes that live only in the current session's
memory. EXPLAIN (without ANALYZE) then reveals whether the planner would pick
the index and how the estimated cost changes, without building anything on
the target. This turns an LLM index recommendation into a checked claim.

PostgreSQL only. MySQL has no hypothetical-index facility, so the step is
skipped there.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional

from shared.db_connection import (
    postgres_connection_kwargs,
    rdst_self_marker,
    resolve_connection_params,
)

from .explain_analysis import _resolve_explain_password
from .rewrite_testing import _has_unresolved_placeholders
from .validation import reorder_index_columns

try:
    import psycopg2
except ImportError:  # pragma: no cover - psycopg2 is optional at import time
    psycopg2 = None

logger = logging.getLogger(__name__)

HYPOPG_INSTALL_SQL = "CREATE EXTENSION IF NOT EXISTS hypopg;"

# The planner-check EXPLAINs reference user tables; the lane tags the
# connection and the marker keeps the statements out of registry admission.
ANALYZE_LANE = "rdst/analyze"
_SELF_MARKER = rdst_self_marker(ANALYZE_LANE)

# Skip reasons surfaced to CLI and desktop. Keep these stable: the UI keys off them.
SKIP_NO_RECOMMENDATIONS = "no_recommendations"
SKIP_UNSUPPORTED_ENGINE = "unsupported_engine"
SKIP_PARAMETERIZED_QUERY = "parameterized_query"
SKIP_HYPOPG_NOT_INSTALLED = "hypopg_not_installed"
SKIP_HYPOPG_NOT_AVAILABLE = "hypopg_not_available"
SKIP_CONNECTION_FAILED = "connection_failed"

# Explain nodes that read from an index; the hypothetical index name shows up here.
_INDEX_NODE_TYPES = {"Index Scan", "Index Only Scan", "Bitmap Index Scan"}


def test_index_recommendations(
    original_sql: str,
    index_recommendations: Optional[List[Dict[str, Any]]] = None,
    target: str = None,
    **kwargs,
) -> Dict[str, Any]:
    """Check each recommended index against the planner using hypopg.

    Returns a dict with ``tested`` and, when tested, one result per
    recommendation: whether the planner used the hypothetical index, the
    estimated cost before and after, and the scan node it appeared in.
    """
    recommendations = _coerce_recommendations(index_recommendations)
    if not recommendations:
        return _skipped(SKIP_NO_RECOMMENDATIONS, "No index recommendations to test.")
    # Test the statements the user will see: the same EQR column ordering the
    # output formatter applies is applied here first.
    reorder_index_columns(recommendations, original_sql)

    target_config = _resolve_target_config(kwargs.get("target_config"), target)
    engine = (target_config.get("engine") or "postgresql").lower()
    if engine not in ("postgresql", "postgres"):
        return _skipped(
            SKIP_UNSUPPORTED_ENGINE,
            "Planner verification of indexes requires PostgreSQL (hypopg). "
            "MySQL has no hypothetical-index facility.",
        )

    if _has_unresolved_placeholders(original_sql):
        return _skipped(
            SKIP_PARAMETERIZED_QUERY,
            "Query contains parameter placeholders without values; the planner "
            "cannot estimate a plan for it.",
        )

    if psycopg2 is None:
        return _skipped(SKIP_CONNECTION_FAILED, "psycopg2 is not available.")

    try:
        resolved = resolve_connection_params(
            target=target, target_config=target_config, lane=ANALYZE_LANE
        )
        password = _resolve_explain_password(target_config, resolved["password"])
        conn_params = postgres_connection_kwargs(resolved, password=password)
        conn = psycopg2.connect(**conn_params)
    except Exception as exc:
        return _skipped(SKIP_CONNECTION_FAILED, f"Could not connect for index testing: {exc}")

    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            status = _hypopg_status(cur)
            if status == "installed":
                return _run_tests(cur, original_sql, recommendations)
            if status == "available":
                return _skipped(
                    SKIP_HYPOPG_NOT_INSTALLED,
                    "hypopg is available on this server but not enabled in this "
                    "database. Run the install statement as an administrator and "
                    "re-run analyze to have index recommendations verified against "
                    "the planner.",
                    install_sql=HYPOPG_INSTALL_SQL,
                )
            return _skipped(
                SKIP_HYPOPG_NOT_AVAILABLE,
                "hypopg is not installed on this server. Install the extension "
                "package (for example postgresql-<version>-hypopg) to have index "
                "recommendations verified against the planner.",
                install_sql=HYPOPG_INSTALL_SQL,
            )
    except Exception as exc:
        logger.debug("Index testing failed: %s", exc)
        return _skipped(SKIP_CONNECTION_FAILED, f"Index testing failed: {exc}")
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _run_tests(cur, sql: str, recommendations: List[Dict[str, Any]]) -> Dict[str, Any]:
    cur.execute("SELECT hypopg_reset()")
    baseline_plan = _explain(cur, sql)
    baseline_cost = _plan_cost(baseline_plan)

    results = []
    for rec in recommendations:
        index_sql = _index_statement(rec)
        result = {
            "index_sql": index_sql,
            "table": rec.get("table", ""),
            "columns": rec.get("columns", []),
            "planner_used_index": False,
            "hypothetical_index": None,
            "scan_type": None,
            "cost_before": baseline_cost,
            "cost_after": None,
            "cost_reduction_pct": None,
            "error": None,
        }
        if not index_sql:
            result["error"] = "Recommendation has no CREATE INDEX statement."
            results.append(result)
            continue

        try:
            cur.execute("SELECT indexname FROM hypopg_create_index(%s)", (index_sql,))
            row = cur.fetchone()
            hypo_name = row[0] if row else None
            result["hypothetical_index"] = hypo_name
            plan = _explain(cur, sql)
            after_cost = _plan_cost(plan)
            scan_type = _scan_using_index(plan, hypo_name)
            result["cost_after"] = after_cost
            result["planner_used_index"] = scan_type is not None
            result["scan_type"] = scan_type
            if baseline_cost and after_cost is not None:
                result["cost_reduction_pct"] = round(
                    (baseline_cost - after_cost) / baseline_cost * 100, 1
                )
        except Exception as exc:
            result["error"] = _clean_pg_error(exc)
        finally:
            try:
                cur.execute("SELECT hypopg_reset()")
            except Exception:
                pass
        results.append(result)

    used = [r for r in results if r["planner_used_index"]]
    return {
        "success": True,
        "tested": True,
        "method": "hypopg",
        "baseline_cost": baseline_cost,
        "results": results,
        "summary": _summary(results, used),
    }


def _resolve_target_config(value: Any, target: Optional[str]) -> Dict[str, Any]:
    """The workflow engine hands dict inputs over as JSON text; reload by name if needed."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = None
    if isinstance(value, dict) and value:
        return value
    if target:
        from shared.config.targets import TargetsConfig

        cfg = TargetsConfig()
        cfg.load()
        return cfg.get(target) or {}
    return {}


def _hypopg_status(cur) -> str:
    cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'hypopg'")
    if cur.fetchone():
        return "installed"
    cur.execute("SELECT 1 FROM pg_available_extensions WHERE name = 'hypopg'")
    return "available" if cur.fetchone() else "missing"


def _explain(cur, sql: str) -> Dict[str, Any]:
    cur.execute(f"{_SELF_MARKER}EXPLAIN (FORMAT JSON) {sql}")
    raw = cur.fetchone()[0]
    if isinstance(raw, str):
        raw = json.loads(raw)
    if isinstance(raw, list):
        raw = raw[0]
    return raw.get("Plan", raw) if isinstance(raw, dict) else {}


def _plan_cost(plan: Dict[str, Any]) -> Optional[float]:
    cost = plan.get("Total Cost") if isinstance(plan, dict) else None
    return float(cost) if cost is not None else None


def _scan_using_index(plan: Dict[str, Any], index_name: Optional[str]) -> Optional[str]:
    """Return the node type that reads the hypothetical index, or None."""
    if not index_name or not isinstance(plan, dict):
        return None
    if plan.get("Node Type") in _INDEX_NODE_TYPES and plan.get("Index Name") == index_name:
        return plan.get("Node Type")
    for child in plan.get("Plans", []) or []:
        found = _scan_using_index(child, index_name)
        if found:
            return found
    return None


def _index_statement(rec: Dict[str, Any]) -> str:
    sql = (rec.get("sql") or rec.get("sql_statement") or "").strip().rstrip(";")
    if sql:
        # hypopg plans the index definition only; build modifiers do not apply.
        return re.sub(r"\s+CONCURRENTLY\b", "", sql, flags=re.IGNORECASE)
    table = rec.get("table")
    columns = rec.get("columns") or []
    if table and columns:
        return f"CREATE INDEX ON {table} ({', '.join(columns)})"
    return ""


def _coerce_recommendations(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return []
    if isinstance(value, dict):
        value = value.get("index_recommendations") or []
    return [r for r in (value or []) if isinstance(r, dict)]


def _clean_pg_error(exc: Exception) -> str:
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else repr(exc)
    return re.sub(r"^hypopg:\s*", "", text)


def _summary(results: List[Dict[str, Any]], used: List[Dict[str, Any]]) -> str:
    total = len(results)
    if total == 0:
        return "No index recommendations were tested."
    if not used:
        return f"Planner did not use any of the {total} recommended index(es) for this query."
    best = max(used, key=lambda r: r.get("cost_reduction_pct") or 0)
    pct = best.get("cost_reduction_pct")
    pct_text = f" (estimated cost down {pct}%)" if pct is not None else ""
    return f"Planner uses {len(used)} of {total} recommended index(es){pct_text}."


def _skipped(reason: str, message: str, **extra: Any) -> Dict[str, Any]:
    return {"success": True, "tested": False, "skipped_reason": reason, "message": message, **extra}
