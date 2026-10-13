"""Facts for the index plan, read from monitor.db and the stored deep analyses."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any

from features.monitor.analysis_digest import as_tested, latest_done_job, load_analysis
from features.monitor.indexes import index_shape
from shared.monitor.write_lookup import (column_roles, lookup_for, order_index_columns,
                                           write_shape)
from shared.monitor.monitor_store import EVERYTHING_ELSE, MonitorStore
from shared.monitor.statements import _tables as query_tables, statement_kind

WINDOW_S = 7 * 86400
# Below this much observation, write rates come from the engine's lifetime counters.
MIN_OBSERVED_S = 600
TOP_QUERIES = 25
_USING = re.compile(r"\bUSING\s+(\w+)", re.I)


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@lru_cache(maxsize=512)
def _suggestions(target: str, key: str, ref: str) -> tuple[dict[str, Any], ...]:
    """A stored analysis never changes, so its suggestions cache by ref."""
    analysis = load_analysis({"state": "done", "analysis_ref": ref, "target": target,
                              "query_key": key})
    if analysis is None:
        return ()
    llm = analysis.llm_analysis if isinstance(analysis.llm_analysis, dict) else {}
    display = analysis.display_payload if isinstance(analysis.display_payload, dict) else {}
    testing = display.get("index_testing") if isinstance(display.get("index_testing"), dict) else {}
    found = []
    for item in llm.get("index_recommendations") or analysis.index_suggestions or []:
        if not isinstance(item, dict) or not item.get("table") or not item.get("columns"):
            continue
        sql, tested = as_tested(item, testing)
        result = tested or {}
        used = result.get("planner_used_index")
        pct = _number(result.get("cost_reduction_pct")) if used else 0.0 if used is False else None
        found.append({"table": item["table"],
                      "columns": list(result.get("columns") or item["columns"]),
                      "method": item.get("index_type") or "btree", "sql": sql,
                      "gain_pct": pct, "verified": used is not None and not result.get("error"),
                      "impact": item.get("estimated_impact")})
    return tuple(found)


def _existing(store: MonitorStore, target: str, start: int) -> list[dict[str, Any]]:
    stats = {(row["schema"], row["table"], row["index_name"]): row for row in store.read(
        "SELECT * FROM index_state WHERE target=?", (target,))}
    window = {(row["schema"], row["table"], row["index_name"]): row["scans"] for row in store.read(
        'SELECT schema,"table",index_name,SUM(scans) scans FROM index_activity WHERE target=? '
        'AND bucket_start>=? GROUP BY schema,"table",index_name', (target, start // 3600 * 3600))}
    existing = []
    for row in store.read("SELECT * FROM index_snapshot WHERE target=?", (target,)):
        key = (row["schema"], row["table"], row["index_name"])
        state = stats.get(key)
        method = _USING.search(row["definition"])
        shape = index_shape(row["definition"])
        existing.append({
            "schema": row["schema"], "table": row["table"], "index_name": row["index_name"],
            "definition": row["definition"],
            "columns": shape["columns"], "partial": shape["partial"],
            "include": shape["include"], "valid": bool(state["is_valid"]) if state else True,
            "first_seen": state["first_seen"] if state else None,
            "method": method.group(1).lower() if method else "btree",
            # Without statistics an index's use is unknown, which never reads as unused.
            "scans": state["scans"] if state else None,
            "window_scans": window.get(key, 0),
            "unique": bool(state["is_unique"]) if state else True,
            "primary": bool(state["is_primary"]) if state else row["index_name"] == "PRIMARY",
            "size_bytes": state["size_bytes"] if state else 0,
            "last_scan_at": state["last_scan_at"] if state else None})
    return existing


def _tables(store: MonitorStore, target: str, start: int, now: int, observed_s: int,
            counter_age_s: int, updated: dict[str, set[str]]) -> dict[str, dict[str, Any]]:
    hours = store.read('SELECT "table",bucket_start,ins+upd+del writes,upd,hot_upd,seconds '
                       'FROM table_activity WHERE target=? AND bucket_start>=?',
                       (target, start // 3600 * 3600))
    window: dict[str, dict[str, float]] = {}
    for row in hours:
        entry = window.setdefault(row["table"].lower(), {"writes": 0, "upd": 0, "hot_upd": 0,
                                                         "peak": 0.0})
        entry["writes"] += row["writes"]
        entry["upd"] += row["upd"]
        entry["hot_upd"] += row["hot_upd"]
        if row["seconds"]:
            entry["peak"] = max(entry["peak"], row["writes"] / row["seconds"])
    tables = {}
    span = max(1, now - start)
    for row in store.read("SELECT * FROM table_state WHERE target=?", (target,)):
        name = row["table"].lower()
        lifetime = row["ins"] + row["upd"] + row["del"]
        measured = window.get(name)
        if observed_s >= MIN_OBSERVED_S and measured is not None:
            writes_per_s = measured["writes"] / span
            upd, hot = measured["upd"], measured["hot_upd"]
            peak = measured["peak"]
        elif observed_s >= MIN_OBSERVED_S:
            writes_per_s, upd, hot, peak = 0.0, 0, 0, 0.0
        else:
            writes_per_s = lifetime / max(1, counter_age_s)
            upd, hot, peak = row["upd"], row["hot_upd"], writes_per_s
        tables[name] = {"writes_per_s": round(writes_per_s, 3), "peak_writes_per_s": round(peak, 3),
                        "hot_ratio": hot / upd if upd else None,
                        "live_rows": row["live_rows"], "size_bytes": row["size_bytes"],
                        "updated_columns": sorted(updated.get(name, set()))}
    return tables


def _replicas(store: MonitorStore, target: str) -> bool:
    rows = store.read("SELECT report_json FROM report_run WHERE target=? AND state='done' "
                      "AND report_json IS NOT NULL ORDER BY ts DESC,id DESC LIMIT 1", (target,))
    try:
        report = json.loads(rows[0]["report_json"]) if rows else {}
    except ValueError:
        return False
    replication = ((report.get("computed") or {}).get("health_collectors") or {}).get("replication")
    return bool(isinstance(replication, dict) and replication.get("peers"))


def gather(store: MonitorStore, target: str, now: int, engine: str,
           names: dict[str, str] | None = None) -> dict[str, Any]:
    """Everything build_plan needs for one database at `now`."""
    from features.monitor.api.views import stmt_totals

    meta = store.read("SELECT * FROM stats_meta WHERE target=?", (target,))
    observed_since = meta[0]["observed_since"] if meta else now
    counter_since = meta[0]["counter_since"] if meta else now
    observed_s = max(0, now - observed_since)
    start = max(now - WINDOW_S, observed_since if observed_s >= MIN_OBSERVED_S else now - WINDOW_S)

    totals = stmt_totals(store, target, start, now + 1)
    texts = {row["query_key"]: row["text"] for row in store.read(
        "SELECT query_key,text FROM query_text WHERE target=?", (target,))}
    system = store.system_queries(target)
    workload = {key: row for key, row in totals.items()
                if key != EVERYTHING_ELSE and key not in system}
    total_ms = sum(row["total_ms"] for row in totals.values()) or 1.0
    span = max(1, now - start)

    # Columns the observed queries filter or sort on: an index on one isn't idle by design.
    filtered: dict[str, set[str]] = {}
    for key in workload:
        text = texts.get(key, "")
        lookup = lookup_for(text, engine) if statement_kind(text) == "write" else None
        roles = column_roles(lookup["sql"] if lookup else text, engine)
        for table in query_tables(text):
            filtered.setdefault(table.lower(), set()).update(roles)

    updated: dict[str, set[str]] = {}
    # Each table's write statements: every index on the table is upkeep they pay for.
    writers: dict[str, list[dict[str, Any]]] = {}
    for key in workload:
        shape = write_shape(texts.get(key, ""), engine) if statement_kind(texts.get(key, "")) == "write" \
            else None
        if shape and shape["verb"] == "update" and shape["table"]:
            updated.setdefault(shape["table"], set()).update(shape["set_columns"])
        if shape and shape["table"]:
            writers.setdefault(shape["table"].lower(), []).append({
                "key": key, "name": (names or {}).get(key), "verb": shape["verb"],
                "set_columns": [_bare(column) for column in shape["set_columns"]],
                "share": workload[key]["total_ms"] / total_ms,
                "calls_per_s": workload[key]["calls"] / span})

    queries = []
    for key in sorted(workload, key=lambda k: (-workload[k]["total_ms"], k))[:TOP_QUERIES]:
        job = latest_done_job(store, target, key)
        suggestions = list(_suggestions(target, key, job["analysis_ref"])) if job else []
        text = texts.get(key, "")
        lookup = lookup_for(text, engine) if statement_kind(text) == "write" else None
        roles = column_roles(lookup["sql"] if lookup else text, engine)
        # Equality columns lead, then sort, then range: analyses don't always order them so.
        # A reordered index isn't the one the planner checked, so its check no longer applies.
        reordered = []
        for suggestion in suggestions:
            columns = order_index_columns(list(suggestion["columns"]), roles)
            changed = columns != list(suggestion["columns"])
            reordered.append({**suggestion, "columns": columns,
                              "roles": {column: roles[_bare(column)] for column in columns
                                        if _bare(column) in roles},
                              "verified": suggestion["verified"] and not changed,
                              "gain_pct": None if changed else suggestion["gain_pct"]})
        suggestions = reordered
        queries.append({"key": key, "name": (names or {}).get(key),
                        "share": workload[key]["total_ms"] / total_ms,
                        "calls_per_s": workload[key]["calls"] / span,
                        "kind": statement_kind(texts.get(key, "")), "suggestions": suggestions})

    return {"engine": engine, "now": now, "observed_s": observed_s,
            "counter_age_s": max(0, now - counter_since),
            "existing": _existing(store, target, start),
            "tables": _tables(store, target, start, now, observed_s, max(0, now - counter_since),
                              updated),
            "queries": queries, "replicas": _replicas(store, target),
            "filtered": {table: sorted(columns) for table, columns in filtered.items()},
            "writers": writers,
            "load": _load(workload, texts, total_ms, queries)}


def _load(workload: dict[str, Any], texts: dict[str, str], total_ms: float,
          queries: list[dict[str, Any]]) -> dict[str, Any]:
    """How the database's time splits between reads and writes, and what the plan looked at."""
    def share(kind: str) -> float:
        return sum(row["total_ms"] for key, row in workload.items()
                   if statement_kind(texts.get(key, "")) == kind) / total_ms

    return {"read_share": round(share("select"), 4), "write_share": round(share("write"), 4),
            "queries": len(queries),
            "queries_share": round(sum(query["share"] for query in queries), 4)}


def _bare(column: str) -> str:
    """A column as column_roles names it: unqualified, unquoted, lowercase."""
    return str(column).split(".")[-1].strip('"`').lower()


def write_cost(store: MonitorStore, target: str, text: str, engine: str, now: int) -> dict[str, Any] | None:
    """What one write statement's table pays per write: its write rate and indexes kept.

    The last day of RDST's readings when it has watched long enough, else the engine's own
    totals since its counters began.
    """
    shape = write_shape(text, engine)
    meta = store.read("SELECT * FROM stats_meta WHERE target=?", (target,))
    if not shape or not shape["table"] or not meta:
        return None
    table = shape["table"]
    start = max(now - 86400, meta[0]["observed_since"])
    if now - start >= MIN_OBSERVED_S:
        row = store.read('SELECT COALESCE(SUM(ins+upd+del),0) writes,COALESCE(SUM(upd),0) upd,'
                         'COALESCE(SUM(hot_upd),0) hot FROM table_activity WHERE target=? '
                         'AND lower("table")=? AND bucket_start>=?',
                         (target, table, start // 3600 * 3600))[0]
        span = now - start
    else:
        rows = store.read('SELECT ins+upd+del writes,upd,hot_upd hot FROM table_state '
                          'WHERE target=? AND lower("table")=?', (target, table))
        if not rows:
            return None
        row, span = rows[0], now - meta[0]["counter_since"]
    indexes = store.read('SELECT COUNT(*) n FROM index_snapshot WHERE target=? AND lower("table")=?',
                         (target, table))[0]["n"]
    return {"table": table, "verb": shape["verb"], "set_columns": shape["set_columns"],
            "table_writes_per_s": round(row["writes"] / max(1, span), 3),
            "indexes_maintained": indexes,
            "hot_ratio": row["hot"] / row["upd"] if row["upd"] and engine == "postgresql" else None}
