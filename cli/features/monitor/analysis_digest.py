"""A card-header summary of one stored deep analysis.

The query row expansion and the report's fix-first cards open with the same header: what the
plan does, what the analyzer found, the fix to copy, and what it's expected to gain. It sits
on top of `analysis_ref`, the complete stored analysis, which the UI renders in full; nothing
here replaces or shortens that record. Numbers come from the stored plan and tests; the
analyzer's own wording is labeled as an estimate.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from functools import lru_cache
from typing import Any

from shared.monitor.monitor_store import MonitorStore

logger = logging.getLogger(__name__)
MODE_LABELS = {"explain": "Plan only (EXPLAIN)", "analyze": "Ran the query (EXPLAIN ANALYZE)"}
MAX_FINDINGS = 4
GOOD_RATINGS = {"excellent", "good"}


def load_analysis(job: dict[str, Any] | None) -> Any | None:
    """The stored AnalysisResult a finished job points at, for the job's own target."""
    if not job or job.get("state") != "done" or not job.get("analysis_ref"):
        return None
    from shared.query_registry.analysis_results import AnalysisResultsRegistry

    ref = job["analysis_ref"].rstrip("/")
    analysis_id = ref.split("/")[-1]
    query_hash = ref.split("/analysis/")[0].split("/")[-1]
    try:
        analysis = AnalysisResultsRegistry().get_analysis_by_id(query_hash, analysis_id)
    except Exception:
        logger.debug("Stored analysis %s unavailable", ref, exc_info=True)
        return None
    if analysis is None or analysis.target != job["target"]:
        return None
    return analysis


def _sentence(text: Any) -> str:
    """Whitespace-collapsed, never shortened: the header shows the analyzer's full words."""
    return " ".join(str(text or "").split())


# Claims that hold only for an index storing columns beyond its key. The index plan builds
# each index on its key columns alone, so an index fix's reasoning never makes them.
_COVERING_CLAIM = re.compile(
    r"\bINCLUDE\b|\bcovering\b|\bindex[- ]only\b|\bheap (?:fetch|lookup|access)|"
    r"\b(?:available|stored|present|kept|contained|carried|read)\s+(?:in|from)\s+the\s+index\b|"
    r"\bindex\s+entr(?:y|ies)\b|"
    r"\bwithout\s+(?:visiting|touching|reading|going\s+back\s+to)\s+the\s+(?:table|heap)\b",
    re.I)


def index_why(text: Any) -> str:
    """An index fix's reasoning, saying only what an index on its key columns does."""
    sentences = re.split(r"(?<=[.!?])\s+", _sentence(text))
    return " ".join(part for part in sentences if part and not _COVERING_CLAIM.search(part))


def _nodes(plan: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    stack = [plan] if isinstance(plan, dict) else []
    while stack:
        node = stack.pop(0)
        found.append(node)
        stack.extend(child for child in node.get("Plans") or [] if isinstance(child, dict))
    return found


def _describe(node: dict[str, Any]) -> str:
    text = str(node.get("Node Type") or "Plan step")
    if node.get("Relation Name"):
        text += f" on {node['Relation Name']}"
    if node.get("Index Name"):
        text += f" using {node['Index Name']}"
    return text


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) \
        else None


def plan_summary(analysis: Any, mode: str | None) -> dict[str, Any] | None:
    explain = analysis.explain_plan if isinstance(analysis.explain_plan, dict) else {}
    metrics = analysis.performance_metrics if isinstance(analysis.performance_metrics, dict) else {}
    root = explain.get("Plan") if isinstance(explain.get("Plan"), dict) else None
    execution = _number(explain.get("Execution Time")) or _number(metrics.get("execution_time_ms"))
    examined = _number(metrics.get("rows_examined"))
    returned = _number(metrics.get("rows_returned"))
    if root is None and execution is None:
        return None
    nodes = _nodes(root) if root else []
    scans = [node for node in nodes if "Scan" in str(node.get("Node Type", ""))]
    main = max(scans, key=lambda node: _number(node.get("Actual Total Time")) or
               _number(node.get("Total Cost")) or 0) if scans else root
    parts = []
    if main is not None:
        parts.append(_describe(main) if main is root or root is None else
                     f"{_describe(root)}, mostly {_describe(main)}")
    if mode == "analyze" and execution is not None:
        parts.append(f"took {execution:.2f} ms" if execution < 10 else f"took {execution:.0f} ms")
    if examined is not None and returned is not None:
        parts.append(f"read {examined:,.0f} rows to return {returned:,.0f}" if mode == "analyze"
                     else f"estimated {examined:,.0f} rows scanned and {returned:,.0f} returned")
    text = "; ".join(parts)
    if mode != "analyze":
        text = "Estimated plan: " + text if text else "Estimated plan"
    return {"text": text[:1].upper() + text[1:] if text else "",
            "root_node": str(root.get("Node Type")) if root else None,
            "execution_ms": execution if mode == "analyze" else None,
            "rows_examined": int(examined) if examined is not None else None,
            "rows_returned": int(returned) if returned is not None else None}


def _index_text(sql: Any) -> str:
    """An index statement as the planner test compares it: case, spacing, a trailing semicolon
    and CONCURRENTLY, which the test drops, never tell two apart."""
    text = " ".join(str(sql or "").split()).rstrip(";").strip().lower()
    return re.sub(r"\s+concurrently\b", "", text)


def _indexed(sql: Any) -> tuple[str | None, list[str]]:
    """The table and the columns, in order, an index statement covers."""
    from features.monitor.db_actions import index_target

    return index_target(str(sql or ""))


def _tests(testing: dict[str, Any]) -> list[dict[str, Any]]:
    return [result for result in testing.get("results") or [] if isinstance(result, dict)] \
        if testing.get("tested") else []


def tested_index(sql: Any, testing: dict[str, Any]) -> dict[str, Any] | None:
    """The planner test of one index statement: the test of the same statement, else the only
    test of an index on the same table and columns in the same order."""
    results = _tests(testing)
    text = _index_text(sql)
    for result in results:
        if text and _index_text(result.get("index_sql")) == text:
            return result
    wanted = _indexed(sql)
    same = [result for result in results if wanted[0] and wanted[1]
            and _indexed(result.get("index_sql")) == wanted]
    return same[0] if len(same) == 1 else None


def as_tested(item: dict[str, Any], testing: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    """A recommended index's statement and planner test. The analysis tests each index with its
    columns in equality-then-range order and stores the statement as the AI wrote it, so the only
    test of the same columns in another order is this one's, and its statement is the index that
    gain belongs to."""
    sql = str(item.get("sql") or "").strip()
    result = tested_index(sql, testing)
    if result is not None:
        return sql, result
    table, columns = _indexed(sql)
    same = [result for result in _tests(testing) if table and columns and result.get("index_sql")
            and _indexed(result["index_sql"])[0] == table
            and sorted(_indexed(result["index_sql"])[1]) == sorted(columns)]
    return (str(same[0]["index_sql"]).strip(), same[0]) if len(same) == 1 else (sql, None)


def _index_gain(recommendation: dict[str, Any], result: dict[str, Any] | None
                ) -> dict[str, Any] | None:
    if result is not None:
        pct = _number(result.get("cost_reduction_pct"))
        if result.get("planner_used_index") and pct is not None:
            return {"measured": False, "text": f"Planner estimate: cost down {pct:.0f}% with a "
                                              "hypothetical index",
                    "before_ms": None, "after_ms": None, "cost_reduction_pct": pct}
        if result.get("planner_used_index") is False and not result.get("error"):
            return {"measured": False, "text": "Planner estimate: the planner didn't use this index",
                    "before_ms": None, "after_ms": None, "cost_reduction_pct": 0.0}
    impact = str(recommendation.get("estimated_impact") or "").strip().lower()
    if impact in ("high", "medium", "low"):
        return {"measured": False, "text": f"Estimated impact: {impact}", "before_ms": None,
                "after_ms": None, "cost_reduction_pct": None}
    return None


def rewrite_test_gain(result: Any) -> dict[str, Any] | None:
    """A tested rewrite's gain: the measured run time when it ran, the planner's cost when it
    was only planned. None unless the test found it significantly faster."""
    improvement = result.get("improvement") if isinstance(result, dict) else None
    if not isinstance(improvement, dict) or not result.get("success", True):
        return None
    overall = improvement.get("overall") or {}
    if not overall.get("is_better") or not overall.get("significant"):
        return None
    timed = improvement.get("execution_time") or {}
    measured = _number(timed.get("improvement_pct")) is not None
    # A rewrite that ran no faster never counts, whatever the planner thought of it.
    pct = _number(timed.get("improvement_pct") if measured
                  else (improvement.get("cost_estimate") or {}).get("improvement_pct"))
    if pct is None or pct <= 0:
        return None
    return {"improvement_pct": pct, "measured": measured,
            "before_ms": _number(timed.get("baseline_ms")) if measured else None,
            "after_ms": _number(timed.get("rewrite_ms")) if measured else None}


_SQL_QUOTED = re.compile(r"'(?:[^'\\]|''|\\.)*'|\"(?:[^\"]|\"\")*\"|`[^`]*`|(\$\w*\$).*?\1",
                         re.DOTALL)


def _sql_shape(sql: Any) -> str:
    """The statement with case and spacing folded outside literals and quoted names, which
    keep their exact text, and without a trailing semicolon."""
    text, parts, last = str(sql or "").strip().rstrip(";").strip(), [], 0
    for match in _SQL_QUOTED.finditer(text):
        parts += [re.sub(r"\s+", " ", text[last:match.start()].lower()), match.group(0)]
        last = match.end()
    parts.append(re.sub(r"\s+", " ", text[last:].lower()))
    return "".join(parts)


def _same_sql(left: Any, right: Any) -> bool:
    return _sql_shape(left) == _sql_shape(right)


def _rewrite_gain(item: dict[str, Any], testing: dict[str, Any]) -> dict[str, Any] | None:
    sql = item["rewritten_sql"]
    tested = [result for result in [*(testing.get("rewrite_results") or []),
                                     testing.get("best_rewrite")]
              if isinstance(result, dict) and result.get("success", True)
              and isinstance(result.get("improvement"), dict)
              and _same_sql(result.get("rewritten_sql") or result.get("sql"), sql)
              ] if testing.get("tested") else []
    if tested:
        gain = rewrite_test_gain(tested[0])
        if gain is None:
            return {"measured": False, "text": "Tested: no faster than the original",
                    "before_ms": None, "after_ms": None, "cost_reduction_pct": 0.0}
        pct = gain["improvement_pct"]
        return {"measured": gain["measured"],
                "text": f"Tested: {pct:.0f}% faster" if gain["measured"]
                else f"Planner estimate: cost down {pct:.0f}%",
                "before_ms": gain["before_ms"], "after_ms": gain["after_ms"],
                "cost_reduction_pct": pct}
    estimate = str(item.get("expected_improvement") or "").strip()
    return {"measured": False, "text": _sentence("Estimated: " + estimate), "before_ms": None,
            "after_ms": None, "cost_reduction_pct": None} if estimate else None


def _fixes(analysis: Any) -> list[dict[str, Any]]:
    llm = analysis.llm_analysis if isinstance(analysis.llm_analysis, dict) else {}
    display = analysis.display_payload if isinstance(analysis.display_payload, dict) else {}
    testing = display.get("index_testing") if isinstance(display.get("index_testing"), dict) else {}
    rewrite_testing = display.get("rewrite_testing") \
        if isinstance(display.get("rewrite_testing"), dict) else {}
    fixes: list[dict[str, Any]] = []
    indexes = [item for item in (llm.get("index_recommendations") or analysis.index_suggestions
                                 or []) if isinstance(item, dict) and item.get("sql")]
    for item in indexes:
        sql, tested = as_tested(item, testing)
        columns = ", ".join(str(column) for column in
                            (tested or {}).get("columns") or item.get("columns") or [])
        table = str(item.get("table") or "").split(".")[-1]
        fixes.append({"kind": "index",
                      "title": f"Add an index on {table}({columns})" if table and columns
                      else "Add an index",
                      "sql": sql, "why": index_why(item.get("rationale")),
                      "expected_gain": _index_gain(item, tested)})
    for item in llm.get("rewrite_suggestions") or analysis.rewrite_suggestions or []:
        if not isinstance(item, dict) or not item.get("rewritten_sql"):
            continue
        fixes.append({"kind": "rewrite", "title": "Rewrite the query",
                      "sql": str(item["rewritten_sql"]).strip(),
                      "why": _sentence(item.get("explanation")),
                      "expected_gain": _rewrite_gain(item, rewrite_testing)})
    for item in llm.get("optimization_opportunities") or []:
        if isinstance(item, dict) and str(item.get("type") or "").lower() == "configuration" \
                and str(item.get("priority") or "").lower() == "high":
            fixes.append({"kind": "config", "title": "Change a setting", "sql": None,
                          "why": _sentence(item.get("description")), "expected_gain": None})
    return fixes


def _findings(analysis: Any) -> list[str]:
    llm = analysis.llm_analysis if isinstance(analysis.llm_analysis, dict) else {}
    assessment = llm.get("performance_assessment") or {}
    execution = llm.get("execution_analysis") or {}
    found: list[str] = []
    for text in [*(assessment.get("primary_concerns") or []), *(execution.get("bottlenecks") or [])]:
        sentence = _sentence(text)
        if sentence and sentence not in found:
            found.append(sentence)
    return found[:MAX_FINDINGS]


def headline(fix: dict[str, Any] | None, rating: str | None, findings: list[str]) -> str:
    if fix is not None and fix["kind"] == "index":
        return fix["title"].replace("Add an index on", "Needs an index on", 1)
    if fix is not None and fix["kind"] == "rewrite":
        return "A rewrite could make it faster"
    if fix is not None:
        return "A setting change could help"
    if rating in GOOD_RATINGS or not findings:
        return "Already efficient"
    return "No fix found"


ACTIONABLE = {"index", "rewrite", "config"}


def fix_ready(fix: dict[str, Any] | None) -> bool:
    """A fix is ready only when the analysis recommends a change with SQL to run."""
    return bool(fix and fix.get("kind") in ACTIONABLE and (fix.get("sql") or "").strip())


def digest_from_analysis(key: str, job: dict[str, Any], analysis: Any) -> dict[str, Any]:
    llm = analysis.llm_analysis if isinstance(analysis.llm_analysis, dict) else {}
    assessment = llm.get("performance_assessment") or {}
    rating = str(assessment.get("overall_rating") or "").lower() or None
    score = assessment.get("efficiency_score")
    fixes = _fixes(analysis)
    findings = _findings(analysis)
    display = analysis.display_payload if isinstance(analysis.display_payload, dict) else {}
    plan_format = (display.get("explain_results") or {}).get("plan_format")
    mode = "explain" if plan_format and plan_format != "analyze" else job.get("mode")
    if mode is None and plan_format == "analyze":
        mode = "analyze"
    return {"query_key": key, "analysis_ref": job.get("analysis_ref"),
            "analyzed_at": job.get("finished_at") or _timestamp(analysis.timestamp),
            "mode": mode, "mode_label": MODE_LABELS.get(mode or ""), "rating": rating,
            "efficiency_score": score if isinstance(score, (int, float)) else None,
            "plan_summary": plan_summary(analysis, mode), "findings": findings,
            "fix": fixes[0] if fixes else None, "other_fixes": fixes[1:4],
            "headline": headline(fixes[0] if fixes else None, rating, findings),
            "fix_ready": fix_ready(fixes[0] if fixes else None)}


def _timestamp(value: Any) -> int | None:
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def latest_done_job(store: MonitorStore, target: str, key: str) -> dict[str, Any] | None:
    rows = store.read("SELECT * FROM analyze_job WHERE target=? AND query_key=? AND state='done' "
                      "AND analysis_ref IS NOT NULL ORDER BY finished_at DESC,id DESC LIMIT 1",
                      (target, key))
    return rows[0] if rows else None


def library_analysis_jobs(target: str, keys: list[str]) -> dict[str, dict[str, Any]]:
    """Read saved analyses by shared identity, including analyses from earlier versions."""
    from shared.query_registry import QueryRegistry

    rows = QueryRegistry().library_store.latest_analysis_refs(target, keys)
    return {row["query_hash"]: {
        "id": None, "target": target, "query_key": row["query_hash"], "state": "done",
        "analysis_ref": f"/api/query-registry/{row['query_hash']}/analysis/{row['analysis_id']}",
        "finished_at": _timestamp(row["created_at"]), "mode": None,
        "param_source": None, "error": None,
    } for row in rows}


def latest_digest(store: MonitorStore, target: str, key: str) -> dict[str, Any] | None:
    job = latest_done_job(store, target, key) or library_analysis_jobs(target, [key]).get(key)
    analysis = load_analysis(job)
    return digest_from_analysis(key, job, analysis) if analysis is not None and job else None


def job_digest(job: dict[str, Any] | None) -> dict[str, Any] | None:
    analysis = load_analysis(job)
    return digest_from_analysis(job["query_key"], job, analysis) if analysis is not None \
        and job else None


@lru_cache(maxsize=1024)
def digest_for_ref(target: str, key: str, ref: str, mode: str | None,
                   finished_at: int | None = None) -> dict[str, Any] | None:
    """Digest of a stored analysis by its ref; stored analyses never change, so it caches."""
    job = {"state": "done", "analysis_ref": ref, "target": target, "query_key": key,
           "mode": mode, "finished_at": finished_at}
    analysis = load_analysis(job)
    return digest_from_analysis(key, job, analysis) if analysis is not None else None


def summary_for_job(job: dict[str, Any] | None) -> dict[str, Any] | None:
    """The row's analysis summary: headline, fix kind and where the full record is."""
    if not job or not job.get("analysis_ref"):
        return None
    digest = digest_for_ref(job["target"], job["query_key"], job["analysis_ref"], job.get("mode"),
                            job.get("finished_at"))
    if digest is None:
        return None
    return {"analysis_ref": job["analysis_ref"],
            "analysis_id": job["analysis_ref"].rstrip("/").split("/")[-1],
            "analyzed_at": job.get("finished_at"), "mode": digest["mode"],
            "mode_label": digest["mode_label"], "headline": digest["headline"],
            "rating": digest["rating"], "efficiency_score": digest["efficiency_score"],
            "fix_kind": (digest["fix"] or {}).get("kind") if digest["fix"] else None,
            # The 'Fix ready' chip: an actionable recommendation with SQL, else 'Analyzed'.
            "fix_ready": digest["fix_ready"]}
