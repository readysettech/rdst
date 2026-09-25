"""Report JSON v2 (DESIGN_V4 section 4): verdict, fix-first cards with their deep analysis,
health with what to do, and what moved since yesterday, as numbers.

Every number is computed here from the report's stored measurements (`computed`); the model's
narrative supplies only titles and wording. Reports stored before v2 carry the same
measurements, so they are presented in this shape too.
"""

from __future__ import annotations

import re
from typing import Any

from features.monitor.analysis_digest import digest_for_ref
from features.monitor.presentation import display_name, format_ms, format_pct

REPORT_VERSION = 3
MAJOR_SHARE = 0.5
SPREAD_SHARE = 0.25
MOVERS = 3
MOVE_MIN_RATIO = 0.2
MOVE_MIN_SHARE = 0.02
NUMBER_WORDS = {1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five"}
# What to do when a health check is not ok and the narrative gave no step, by words in its name.
HEALTH_ACTIONS = (
    (("vacuum", "bloat"), "Run VACUUM (ANALYZE) on the bloated tables and check that autovacuum "
                          "keeps up with their write rate."),
    (("unused", "duplicate", "index_health", "index health"),
     "Drop indexes nothing uses and merge duplicates; each one slows every write."),
    (("connection",), "Lower the application's pool size or add a connection pooler before the "
                      "server runs out of connections."),
    (("config",), "Review the flagged settings against the server's memory and workload."),
    (("replica",), "Check the replica's lag and that its replication slot is still consumed."),
    (("cache",), "Look for a new large scan in the query list; if the working set grew, give "
                 "the database more memory."),
    (("rollback", "deadlock"), "Check application logs for failed transactions from when "
                               "rollbacks rose."),
)


def _name(entry: dict[str, Any]) -> str:
    text = entry.get("text") or ""
    return entry.get("display_name") or (
        display_name(text) if text else f"Query {entry.get('query_key', '')}")


def digests(target: str, computed: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Deep analysis digests for the report's analyzed queries; stored analyses never change."""
    found: dict[str, dict[str, Any]] = {}
    for item in computed.get("analyses") or []:
        if not isinstance(item, dict) or not item.get("analysis_ref"):
            continue
        digest = digest_for_ref(target, item["query_key"], item["analysis_ref"], item.get("mode"))
        if digest is not None:
            found[item["query_key"]] = digest
    return found


def _names(keys: list[str], names: dict[str, str]) -> str:
    labels = [names.get(key, key) for key in keys]
    return labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " and " + labels[-1]


def verdict(computed: dict[str, Any], analysis: dict[str, dict[str, Any]],
            health: list[dict[str, Any]]) -> dict[str, Any]:
    """One sentence that leads with the finding, from the measured load shares."""
    top = [row for row in computed.get("top_queries") or [] if isinstance(row, dict)]
    names = {row["query_key"]: _name(row) for row in top}
    bad = any(item.get("status") == "bad" for item in health)
    warn = any(item.get("status") == "warn" for item in health)
    if not top or not top[0].get("db_time_share"):
        return {"sentence": "No query load was recorded in the last 24 hours.", "query_key": None,
                "share": None, "tone": "warning" if bad or warn else "neutral"}
    first = top[0]
    share = float(first.get("db_time_share") or 0)
    key = first["query_key"]
    fix = (analysis.get(key) or {}).get("fix")
    if share >= MAJOR_SHARE:
        sentence = f"One query is {format_pct(share)} of your load: {names[key]}"
    else:
        count, total = 0, 0.0
        for row in top[:3]:
            count += 1
            total += float(row.get("db_time_share") or 0)
            if total >= MAJOR_SHARE:
                break
        if total >= MAJOR_SHARE and count > 1:
            keys = [row["query_key"] for row in top[:count]]
            sentence = (f"{NUMBER_WORDS[count]} queries are {format_pct(total)} of your load: "
                        f"{_names(keys, names)}")
        elif share >= SPREAD_SHARE:
            sentence = f"{names[key]} is the biggest share of your load at {format_pct(share)}"
        else:
            sentence = (f"Load is spread out: the biggest query, {names[key]}, is "
                        f"{format_pct(share)}")
    if fix is not None and fix.get("kind") == "index":
        sentence += f". {fix['title'].replace('Add an index', 'An index', 1)} is the fix"
    tone = ("negative" if bad or (fix is not None and share >= SPREAD_SHARE) else
            "warning" if warn or fix is not None else
            "positive" if all((analysis.get(row["query_key"]) or {}).get("headline") ==
                              "Already efficient" for row in top[:3]) and analysis else "neutral")
    return {"sentence": sentence + ".", "query_key": key, "share": share, "tone": tone}


_INDEX_WORDS = re.compile(r"\bindex(es)?\b", re.I)
_REWRITE_WORDS = re.compile(r"\b(rewrite|order by|limit|select list|duplicat\w*|collapse|"
                            r"deduplicat\w*|subquer\w*|join|query text|aggregate)", re.I)
_CONFIG_WORDS = re.compile(r"\b(work_mem|setting|config\w*|shared_buffers|parameter)\b", re.I)


def paired_fix(item: dict[str, Any], digest: dict[str, Any] | None) -> dict[str, Any] | None:
    """The analysis fix that addresses this finding, or None when none of them does.

    A finding about an index takes an index fix, one about the statement a rewrite, one
    about a setting a config change; an index fix is also matched on its table and columns
    when the finding names them. A card never shows SQL for a different problem.
    """
    if not digest:
        return None
    fixes = [fix for fix in [digest.get("fix"), *(digest.get("other_fixes") or [])] if fix]
    # The title names the finding; the action only decides when the title names no fix kind.
    title = str(item.get("title") or "")
    text = title if any(pattern.search(title) for pattern in
                        (_INDEX_WORDS, _REWRITE_WORDS, _CONFIG_WORDS)) else \
        f"{title} {item.get('action', '')}"
    if _INDEX_WORDS.search(text) and not re.search(r"\b(stop|remove|drop)\b.*index", text, re.I):
        candidates = [fix for fix in fixes if fix["kind"] == "index"]
        named = [fix for fix in candidates
                 if any(word.lower() in text.lower()
                        for word in re.findall(r"\w+", fix.get("title", ""))[4:])]
        return (named or candidates or [None])[0]
    if _CONFIG_WORDS.search(text):
        return next((fix for fix in fixes if fix["kind"] == "config"), None)
    if _REWRITE_WORDS.search(text):
        return next((fix for fix in fixes if fix["kind"] == "rewrite"), None)
    return None


def fix_first(items: list[dict[str, Any]], computed: dict[str, Any],
              analysis: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Cards ranked by the load share each query carries in the report's 24 h."""
    top = {row["query_key"]: row for row in computed.get("top_queries") or []
           if isinstance(row, dict)}
    totals = ((computed.get("deltas") or {}).get("24h") or {}).get("queries") or {}
    cards = []
    for item in items:
        key = item.get("query_key", "")
        row = top.get(key, {})
        measured = totals.get(key) or {}
        calls = float(((measured.get("calls") or {}).get("current")) or 0)
        total_ms = float(row.get("total_ms") or ((measured.get("total_ms") or {}).get("current"))
                         or 0)
        digest = analysis.get(key)
        fix = paired_fix(item, digest)
        gain = (fix or {}).get("expected_gain") or {}
        cards.append({**item, "display_name": _name(row) if row else f"Query {key}",
                      "share": float(row.get("db_time_share") or 0), "db_time_ms": total_ms,
                      "calls": calls, "mean_ms": total_ms / calls if calls else None,
                      "fix": fix, "fix_sql": (fix or {}).get("sql"),
                      "expected_gain": gain.get("text"),
                      "analysis_ref": (digest or {}).get("analysis_ref"), "analysis": digest})
    cards.sort(key=lambda card: -card["share"])
    return cards


_PERCENT = re.compile(r"\d+(?:\.\d+)?\s?%")


def measured_summary(summary: str) -> str:
    """The narrative without its own percentages: shares come from code, in the verdict and
    on the cards, so the page never shows two different numbers for one thing."""
    sentences = re.split(r"(?<=[.!?])\s+", summary.strip()) if summary.strip() else []
    return " ".join(sentence for sentence in sentences if not _PERCENT.search(sentence))


def shared_fix_kind(action: str) -> str:
    """What a shared fix does, read from its own action text."""
    if _INDEX_WORDS.search(action) and not re.search(r"\b(stop|remove|drop)\b.*index",
                                                     action, re.I):
        return "index"
    if _CONFIG_WORDS.search(action):
        return "config"
    if _REWRITE_WORDS.search(action) or re.search(r"\b(application|app-side|code)\b",
                                                  action, re.I):
        return "rewrite"
    return "other"


def one_fix_several(items: list[dict[str, Any]], computed: dict[str, Any]) -> list[dict[str, Any]]:
    """Shared fixes with their load share and a title taken from the same fix as the body."""
    shares = {row["query_key"]: float(row.get("db_time_share") or 0)
              for row in computed.get("top_queries") or [] if isinstance(row, dict)}
    result = []
    for item in items:
        keys = item.get("query_keys") or []
        kind = shared_fix_kind(str(item.get("action") or ""))
        columns = ", ".join(str(column) for column in item.get("columns") or [])
        count = f"{len(keys)} {'query' if len(keys) == 1 else 'queries'}"
        title = {"index": (f"One index on {item.get('table')}({columns}) helps {count}"
                           if columns else f"One index on {item.get('table')} helps {count}"),
                 "rewrite": f"One rewrite fixes {count}",
                 "config": f"One setting change helps {count}"}.get(
            kind, f"One change helps {count}")
        result.append({**item, "share": sum(shares.get(key, 0.0) for key in keys),
                       "kind": kind, "title": title})
    return result


def health_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for item in items:
        matters = item.get("status") in ("warn", "bad")
        action = str(item.get("action") or "").strip() or None
        if matters and action is None:
            check = f"{item.get('check', '')} {item.get('label', '')}".lower()
            action = next((text for words, text in HEALTH_ACTIONS
                           if any(word in check for word in words)), None)
        result.append({**item, "action": action if matters else None, "matters": matters})
    return result


def _change(before: float, after: float) -> float | None:
    return after / before - 1 if before else None


def since_yesterday(computed: dict[str, Any]) -> list[dict[str, Any]]:
    """Last 24 h against the 24 h before, for the database and the queries that moved most."""
    window = (computed.get("deltas") or {}).get("24h") or {}
    database = window.get("database") or {}
    entries = []
    for field, metric, label, unit in (("total_ms", "load", "Database load", "ms"),
                                       ("calls", "calls", "Calls", "calls"),
                                       ("mean_ms", "latency", "Average latency", "ms_per_call")):
        values = database.get(field) or {}
        before, after = values.get("prior"), values.get("current")
        if isinstance(before, (int, float)) and isinstance(after, (int, float)) and (before or after):
            entries.append({"label": label, "query_key": None, "metric": metric,
                            "before": float(before), "after": float(after),
                            "change": _change(float(before), float(after)), "unit": unit})
    top = [row for row in computed.get("top_queries") or [] if isinstance(row, dict)]
    queries = window.get("queries") or {}
    moved = []
    for row in top:
        values = (queries.get(row["query_key"]) or {}).get("total_ms") or {}
        before, after = float(values.get("prior") or 0), float(values.get("current") or 0)
        change = _change(before, after)
        if row.get("db_time_share", 0) < MOVE_MIN_SHARE:
            continue
        if change is None and after:
            moved.append((after, row, before, after, None))
        elif change is not None and abs(change) >= MOVE_MIN_RATIO:
            moved.append((abs(after - before), row, before, after, change))
    moved.sort(key=lambda entry: (-entry[0], entry[1]["query_key"]))
    for _, row, before, after, change in moved[:MOVERS]:
        entries.append({"label": f"{_name(row)}{' (new)' if change is None else ''}",
                        "query_key": row["query_key"], "metric": "load", "before": before,
                        "after": after, "change": change, "unit": "ms"})
    return entries


def since_yesterday_lines(entries: list[dict[str, Any]]) -> list[str]:
    """The same numbers as sentences, for clients that read `changed_since_yesterday`."""
    lines = []
    for entry in entries:
        if entry["unit"] == "calls":
            values = f"{entry['before']:,.0f} -> {entry['after']:,.0f}"
        else:
            values = f"{format_ms(entry['before'])} -> {format_ms(entry['after'])}"
        change = entry["change"]
        if change is None and not entry["before"]:
            after = (f"{entry['after']:,.0f}" if entry["unit"] == "calls"
                     else format_ms(entry["after"]))
            lines.append(f"{entry['label']}: {after} in the last 24 hours.")
            continue
        moved = "" if change is None else f" ({'+' if change >= 0 else ''}{change * 100:.0f}%)"
        lines.append(f"{entry['label']}: {values}{moved}.")
    return lines


def build(report: dict[str, Any], target: str) -> dict[str, Any]:
    """The v2 fields for a stored report (either version)."""
    computed = report.get("computed") or {}
    analysis = digests(target, computed) if computed else {}
    health = health_items([item for item in report.get("health") or [] if isinstance(item, dict)])
    moved = since_yesterday(computed) if computed else []
    return {"version": REPORT_VERSION, "verdict": verdict(computed, analysis, health),
            "fix_first": fix_first([item for item in report.get("fix_first") or []
                                    if isinstance(item, dict)], computed, analysis),
            "one_fix_several_queries": one_fix_several(
                [item for item in report.get("one_fix_several_queries") or []
                 if isinstance(item, dict)], computed),
            "health": health, "since_yesterday": moved}


def verdict_sentence(report: dict[str, Any], target: str) -> str:
    """The history picker's line: the same sentence the report opens with."""
    computed = report.get("computed") or {}
    if not computed:
        return ""
    top = (computed.get("top_queries") or [{}])[0]
    analysis = {}
    for item in computed.get("analyses") or []:
        if isinstance(item, dict) and item.get("query_key") == top.get("query_key") and \
                item.get("analysis_ref"):
            digest = digest_for_ref(target, item["query_key"], item["analysis_ref"],
                                    item.get("mode"))
            if digest is not None:
                analysis[item["query_key"]] = digest
    health = health_items([item for item in report.get("health") or [] if isinstance(item, dict)])
    return verdict(computed, analysis, health)["sentence"]
