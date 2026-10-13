"""Report JSON v2 (DESIGN_V4 section 4): verdict, fix-first cards with their deep analysis,
health with what to do, and what moved since yesterday, as numbers.

Every number is computed here from the report's stored measurements (`computed`); the model's
narrative supplies only titles and wording. Reports stored before v2 carry the same
measurements, so they are presented in this shape too.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from features.monitor.actions import BUFFER_SETTINGS, files_action
from features.monitor.analysis_digest import digest_for_ref
from features.monitor.db_actions import concurrent_index_sql, index_target
from features.monitor.presentation import (
    clock_text, display_name, format_ms, format_pct, latency_text,
)

REPORT_VERSION = 3
# Index fixes below this share of the day's database time fold into "N smaller fixes".
SMALLER_FIX_SHARE = 0.05
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
                      "share": clamp_share(row.get("db_time_share")), "db_time_ms": total_ms,
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


def clamp_share(value: Any) -> float:
    """A share of database time as a fraction in [0, 1]; a percentage over 1 is scaled."""
    try:
        share = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    if share > 1:
        share = share / 100
    return min(1.0, max(0.0, share))


def dedupe(items: list[dict[str, Any]], key: Any) -> list[dict[str, Any]]:
    """The first item for each key, in order."""
    seen: set[Any] = set()
    kept = []
    for item in items:
        marker = key(item)
        if marker in seen:
            continue
        seen.add(marker)
        kept.append(item)
    return kept


def one_fix_several(items: list[dict[str, Any]], computed: dict[str, Any]) -> list[dict[str, Any]]:
    """Shared fixes with their load share, a group id, and a title taken from the same fix
    as the body. The same table and columns merge into one entry."""
    shares = {row["query_key"]: clamp_share(row.get("db_time_share"))
              for row in computed.get("top_queries") or [] if isinstance(row, dict)}
    result = []
    for item in dedupe(items, lambda item: (str(item.get("table") or "").lower(),
                                            tuple(str(column).lower()
                                                  for column in item.get("columns") or []),
                                            shared_fix_kind(str(item.get("action") or "")))):
        keys = list(dict.fromkeys(item.get("query_keys") or []))
        kind = shared_fix_kind(str(item.get("action") or ""))
        columns = ", ".join(str(column) for column in item.get("columns") or [])
        count = f"{len(keys)} {'query' if len(keys) == 1 else 'queries'}"
        title = {"index": (f"One index on {item.get('table')}({columns}) helps {count}"
                           if columns else f"One index on {item.get('table')} helps {count}"),
                 "rewrite": f"One rewrite fixes {count}",
                 "config": f"One setting change helps {count}"}.get(
            kind, f"One change helps {count}")
        result.append({**item, "query_keys": keys,
                       "share": min(1.0, sum(shares.get(key, 0.0) for key in keys)),
                       "kind": kind, "title": title,
                       "group": (f"{item.get('table')} ({columns})" if kind == "index" and columns
                                 else None)})
    return result


def health_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for item in dedupe(items, lambda item: str(item.get("check") or "").lower()):
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
    if not any((database.get(field) or {}).get("prior") for field in ("total_ms", "calls")):
        # No readings the 24 h before: nothing moved against them.
        return []
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
            "fix_first": fix_first(dedupe([item for item in report.get("fix_first") or []
                                           if isinstance(item, dict)],
                                          lambda item: (item.get("query_key"),
                                                        str(item.get("title") or "").lower())),
                                   computed, analysis),
            "one_fix_several_queries": one_fix_several(
                [item for item in report.get("one_fix_several_queries") or []
                 if isinstance(item, dict)], computed),
            "health": health, "since_yesterday": moved}


# The Report page (ca-redesign-plan.md section 3.4): verdict, the day, Fix first, Health, run.

CHECK_LABELS = {"cache": "Cache hit", "connections": "Connections", "vacuum": "Vacuum and bloat",
                "indexes": "Indexes", "settings": "Settings", "replication": "Replication",
                "transactions": "Rollbacks and deadlocks", "sequential_scans": "Full table scans"}
_INDEX_SQL = re.compile(r"^\s*CREATE\s+(?:UNIQUE\s+)?INDEX\b", re.I)
NO_PRIOR_DAY = "No data for the day before"
NEEDS_YOU = ("warn", "bad")


def checks_in_actions(actions: list[dict[str, Any]]) -> set[str]:
    """The checks whose finding Actions lists as a change. One merely off a rule of thumb
    stays in the report alone."""
    acted = set()
    for action in actions:
        if files_action(action):
            acted.add(str(action.get("check")))
            # A memory setting is the fix a low cache hit points to.
            if (action.get("setting") or {}).get("name") in BUFFER_SETTINGS:
                acted.add("cache")
    return acted


def compared_with_prior(day: dict[str, Any]) -> bool:
    """Whether the day before has readings to compare with; reports written before that was
    recorded always had them."""
    period = day.get("period") or {}
    return "prior_start" not in period or period["prior_start"] is not None


def _month_day(ts: int) -> str:
    moment = datetime.fromtimestamp(ts)
    return f"{moment.strftime('%B')} {moment.day}"


def _rate(value: float) -> str:
    return f"{value:,.0f}" if value >= 100 else f"{value:.1f}"


def _index_title(table: str | None, columns: list[str], fallback: str) -> str:
    return f"Index {table} ({', '.join(columns)})" if table and columns else fallback


def _empty_view(signed_in: bool) -> dict[str, Any]:
    return {"verdict": None, "day": None, "fix_first": [], "health": [], "health_fine": [],
            "run": None, "narrative_missing": not signed_in, "copy_count": 0, "detailed": None}


def day_tiles(day: dict[str, Any], actions: list[dict[str, Any]],
              max_conns: float | None) -> list[dict[str, Any]]:
    numbers = {row["metric"]: row for row in day.get("numbers") or []
               if isinstance(row, dict) and row.get("metric")}
    peak = day.get("peak") or {}

    def value(metric: str) -> float | None:
        found = (numbers.get(metric) or {}).get("value")
        return float(found) if isinstance(found, (int, float)) else None

    def change(metric: str) -> str | None:
        moved = (numbers.get(metric) or {}).get("change")
        if not isinstance(moved, (int, float)):
            return None
        return f"{'+' if moved >= 0 else ''}{moved * 100:.0f}% vs the day before"

    qps, mean, conns, cache = value("qps"), value("mean_ms"), value("active_conns_peak"), \
        value("cache_hit_ratio_min")
    p95 = value("p95_ms")
    deadlocks = value("deadlocks") or 0
    peak_text = None
    if isinstance(peak.get("qps"), (int, float)) and isinstance(peak.get("ts"), int):
        peak_text = f"peak {_rate(float(peak['qps']))} at {clock_text(peak['ts']).split(' ')[0]}"
    buffers = next((action for action in actions
                    if action.get("id") in ("setting_shared_buffers",
                                            "setting_innodb_buffer_pool_size", "cache_hit")), None)
    reason = None
    if cache is not None and cache < 0.9:
        finding = str((buffers or {}).get("finding") or "")
        reason = ("shared_buffers is the default" if "default" in finding.lower()
                  else finding.rstrip(".") or None)
    return [
        {"metric": "qps", "label": "Queries per second", "value": qps, "unit": "qps",
         "detail": peak_text, "state": "ok" if qps is not None else "collecting", "reason": None},
        {"metric": "mean_ms", "label": "Average latency", "value": mean, "unit": "ms",
         "detail": f"p95 {latency_text(p95)}" if p95 is not None else change("mean_ms"),
         "state": "ok" if mean is not None else "collecting", "reason": None},
        {"metric": "connections", "label": "Peak connections", "value": conns, "unit": "count",
         "detail": f"{deadlocks:.0f} deadlocks" if conns is not None else None,
         "state": "ok" if conns is not None else "collecting", "reason": None,
         "limit": max_conns},
        {"metric": "cache_hit", "label": "Lowest cache hit", "value": cache, "unit": "ratio",
         "detail": None if reason else change("cache_hit_ratio_min"),
         "state": ("collecting" if cache is None else "bad" if cache < 0.8
                   else "warn" if cache < 0.9 else "ok"), "reason": reason},
    ]


def _entry(**fields: Any) -> dict[str, Any]:
    base = {"rank": None, "title": "", "detail": None, "sql": None, "share": None,
            "state": "ready", "kind": "other", "group": None, "query_keys": [],
            "restart": False, "fixed_at": None, "before_ms": None, "after_ms": None,
            "_checks": [], "_order": (9, 0.0)}
    return {**base, **fields}


def fix_list(body: dict[str, Any], actions: list[dict[str, Any]],
             fixed: list[dict[str, Any]], engine: str) -> list[dict[str, Any]]:
    """Fix first as one ranked list: what needs attention, the index fixes by share (one
    row per shared index), what is worth doing, what got fixed, then the smaller fixes."""
    cards = [card for card in body.get("fix_first") or [] if isinstance(card, dict)]
    groups = [group for group in body.get("one_fix_several_queries") or []
              if isinstance(group, dict) and group.get("kind") == "index"]
    grouped = {key for group in groups for key in group.get("query_keys") or []}
    entries: list[dict[str, Any]] = []
    for action in actions:
        if action.get("severity") != "bad":
            continue
        why = str(action.get("why") or "").strip()
        detail = f"Restart needed. {why}".strip() if action.get("restart") else why
        entries.append(_entry(title=action.get("title") or "", detail=detail or None,
                              sql=action.get("sql"), state="needs_attention",
                              kind="config" if action.get("setting") else "other",
                              query_keys=list(action.get("query_keys") or []),
                              restart=bool(action.get("restart")),
                              _checks=[action.get("check")], _order=(0, 0.0)))
    for group in groups:
        table = str(group.get("table") or "").lower() or None
        columns = [str(column).lower() for column in group.get("columns") or []]
        keys = list(group.get("query_keys") or [])
        share = clamp_share(group.get("share"))
        action = str(group.get("action") or "")
        entries.append(_entry(
            title=_index_title(table, columns, group.get("title") or action),
            detail=f"{len(keys)} queries, {format_pct(share)} of time", share=share,
            sql=concurrent_index_sql(action, engine) if _INDEX_SQL.match(action) else None,
            kind="index", group=group.get("group") or f"{table} ({', '.join(columns)})",
            query_keys=keys, _order=(1, -share)))
    for card in cards:
        fix = card.get("fix") or {}
        sql = card.get("fix_sql") or fix.get("sql")
        if card.get("query_key") in grouped or not sql:
            continue
        share = clamp_share(card.get("share"))
        if fix.get("kind") == "index":
            table, columns = index_target(sql)
            entries.append(_entry(
                title=_index_title(table, columns, str(card.get("title") or "")),
                detail=f"{format_pct(share)} of time", share=share,
                sql=concurrent_index_sql(sql, engine), kind="index",
                group=f"{table} ({', '.join(columns)})" if table and columns else None,
                query_keys=[card["query_key"]], _order=(1, -share)))
        elif fix.get("kind") in ("rewrite", "config"):
            entries.append(_entry(title=str(card.get("title") or fix.get("title") or ""),
                                  detail=f"{format_pct(share)} of time", share=share, sql=sql,
                                  kind=fix["kind"], query_keys=[card["query_key"]],
                                  _order=(1, -share)))
    for action in actions:
        if action.get("severity") != "warn" or action.get("sql"):
            continue
        entries.append(_entry(title=action.get("title") or "",
                              detail=str(action.get("finding") or "").strip() or None,
                              state="worth_doing", query_keys=list(action.get("query_keys") or []),
                              _checks=[action.get("check")], _order=(2, 0.0)))
    for item in fixed:
        try:
            fixed_body = json.loads(item["body_json"]) if isinstance(item.get("body_json"), str) \
                else dict(item.get("body_json") or {})
        except ValueError:
            continue
        before, after = fixed_body.get("before_ms"), fixed_body.get("after_ms")
        when = item.get("resolved_at") or item.get("updated_at")
        detail = f"Fixed {clock_text(when)}" if when else "Fixed"
        if isinstance(before, (int, float)) and isinstance(after, (int, float)):
            detail += f", {latency_text(before)} to {latency_text(after)}"
        entries.append(_entry(
            title=_index_title(fixed_body.get("table"), list(fixed_body.get("columns") or []),
                               f"Index {fixed_body.get('index_name') or ''}".strip()),
            detail=detail, state="fixed", kind="index",
            query_keys=[fixed_body["query_key"]] if fixed_body.get("query_key") else [],
            fixed_at=when, before_ms=before, after_ms=after, _order=(3, 0.0)))
    for entry in entries:
        if entry["state"] == "ready" and entry["kind"] == "index" and \
                (entry["share"] or 0) < SMALLER_FIX_SHARE:
            entry["state"] = "smaller"
            entry["_order"] = (4, entry["_order"][1])
    for action in actions:
        if action.get("severity") == "warn" and action.get("sql") and \
                str(action.get("check")) == "indexes":
            entries.append(_entry(title=action.get("title") or "",
                                  detail=str(action.get("finding") or "").strip() or None,
                                  sql=action.get("sql"), state="smaller",
                                  _checks=[action.get("check")], _order=(4, 1.0)))
    entries.sort(key=lambda entry: entry["_order"])
    rank = 0
    for entry in entries:
        if entry["state"] != "fixed":
            rank += 1
            entry["rank"] = rank
        entry.pop("_order")
    return entries


def health_rows(day: dict[str, Any], actions: list[dict[str, Any]],
                ranks: dict[str, int]) -> tuple[list[dict[str, Any]], list[str]]:
    """Checks that warn, each with its finding, the fix that addresses it and whether Actions
    lists that fix; the fine ones by name. Every warning needs you, as the page says."""
    acted = checks_in_actions(actions)
    by_check: dict[str, list[dict[str, Any]]] = {}
    for action in actions:
        by_check.setdefault(str(action.get("check")), []).append(action)
    rows, fine = [], []
    for check in (day.get("overview") or {}).get("checks") or []:
        name = str(check.get("check") or "")
        label = str(check.get("label") or CHECK_LABELS.get(name, name))
        status = check.get("status")
        found = by_check.get(name, [])
        if status in NEEDS_YOU:
            value = str(check.get("value") or "")
            finding = (str(found[0].get("finding") or "").rstrip(".") if found else
                       f"{label} {value[:1].lower()}{value[1:]}" if value else label)
            rank = ranks.get(name)
            if rank is None and name == "cache":
                rank = ranks.get("settings")
            rows.append({"check": name, "label": label, "state": status, "finding": finding,
                         "detail": str(found[0].get("why") or "").strip() or None if found
                         else None, "fix_rank": rank, "needs_you": True,
                         "in_actions": name in acted})
        elif status == "ok":
            fine.append(label)
        else:
            rows.append({"check": name, "label": label, "state": "unavailable",
                         "finding": f"{label} not checked", "detail": str(check.get("value") or "")
                         or None, "fix_rank": None, "needs_you": False, "in_actions": False})
    rows.sort(key=lambda row: {"bad": 0, "warn": 1}.get(row["state"], 2))
    return rows, fine


def verdict_text(body: dict[str, Any], fixes_ready: int, needs_you: int,
                 checks: int, *, slower: int = 0, new_heavy: int = 0,
                 compared: bool = True) -> dict[str, str]:
    """The verdict sentence split into its lead and the rest, with the counts the page
    reads once. "Everything else held" only when What changed has no slower or new heavy
    query, so Quick and Detailed never disagree, and only against a day with readings."""
    sentence = str((body.get("verdict") or {}).get("sentence") or "").strip()
    moved = ([f"{slower} {'query' if slower == 1 else 'queries'} got slower"] if slower else []) \
        + ([f"{new_heavy} new heavy {'query' if new_heavy == 1 else 'queries'}"]
           if new_heavy else [])
    tail = " and ".join(moved) if moved else "everything else held" if compared else ""
    counts = (f"{fixes_ready} {'fix is' if fixes_ready == 1 else 'fixes are'} ready, "
              f"{needs_you} of {checks} checks need you{', ' + tail if tail else ''}.")
    if not sentence:
        return {"lead": counts, "rest": ""}
    if ": " in sentence:
        lead, rest = sentence.split(": ", 1)
        return {"lead": f"{lead}:", "rest": f"{rest.strip()} {counts}".strip()}
    return {"lead": sentence, "rest": counts}


def run_facts(row: dict[str, Any], report: dict[str, Any], body: dict[str, Any],
              store: Any) -> dict[str, Any]:
    number = store.read("SELECT COUNT(*) n FROM report_run WHERE target=? AND state='done' "
                        "AND sample=0 AND id<=?", (row["target"], row["id"]))[0]["n"] or 1
    computed = report.get("computed") or {}
    analyses = [item for item in computed.get("analyses") or []
                if isinstance(item, dict) and not item.get("provisional")]
    rewrites = 0
    for card in body.get("fix_first") or []:
        digest = card.get("analysis") if isinstance(card, dict) else None
        fixes = [digest.get("fix"), *(digest.get("other_fixes") or [])] if digest else []
        rewrites += any(fix and fix.get("kind") == "rewrite" for fix in fixes)
    return {"number": int(number), "trigger": "nightly" if row["trigger"] == "nightly"
            else "on_request", "ran_at": int(row["ts"]),
            "model": str(report.get("model") or "") or None,
            "analyses": len(analyses), "rewrites": rewrites}


def view(row: dict[str, Any], report: dict[str, Any] | None, body: dict[str, Any] | None,
         store: Any, *, signed_in: bool = True, fixed: list[dict[str, Any]] | None = None,
         engine: str = "postgresql", max_conns: float | None = None,
         detailed: bool = False) -> dict[str, Any]:
    """The Report page's fields for one stored run; numbers only when signed out. With
    `detailed`, the Detailed sections too, from the same numbers."""
    if not isinstance(report, dict) or not body:
        return _empty_view(signed_in)
    day = body.get("snapshot") or {}
    actions = [action for action in day.get("actions") or [] if isinstance(action, dict)]
    fixes = fix_list(body, actions, fixed or [], engine)
    ranks: dict[str, int] = {}
    for fix in fixes:
        for check in fix.pop("_checks", []):
            if check and fix["rank"] is not None:
                ranks.setdefault(str(check), fix["rank"])
    health, fine = health_rows(day, actions, ranks)
    if not signed_in:
        for fix in fixes:
            fix["sql"] = None
    period = day.get("period") or {}
    start = period.get("start")
    compared = compared_with_prior(day)
    versus = "vs the day before" if compared else "no data for the day before"
    label = (f"{_month_day(int(start))}, {versus}" if isinstance(start, int)
             and not period.get("partial") else "today so far" if period.get("partial")
             else f"the day, {versus}")
    ready = sum(1 for fix in fixes if fix["state"] in ("ready", "smaller"))
    needs = sum(1 for check in health if check["needs_you"])
    changed = what_changed(day)
    page = {"verdict": None if not signed_in else
            verdict_text(body, ready, needs, len(health) + len(fine),
                         slower=sum(1 for item in changed if item["kind"] == "slower"),
                         new_heavy=sum(1 for item in changed if item["kind"] == "new_heavy"),
                         compared=compared),
            "day": {"label": label, "tiles": day_tiles(day, actions, max_conns),
                    "chart": day.get("chart"), "spikes": day.get("spikes") or [],
                    "query_count": len(day.get("queries") or []),
                    "compared": compared} if day else None,
            "fix_first": fixes, "health": health, "health_fine": fine,
            "run": run_facts(row, report, body, store),
            "narrative_missing": not signed_in or bool(body.get("narrative_error")),
            # "Copy all N as SQL": the rows that carry SQL, on both views.
            "copy_count": sum(1 for fix in fixes if fix["sql"] and fix["state"] != "fixed"),
            "detailed": None}
    if detailed and day:
        page["detailed"] = detailed_view(row, report, day, actions, fixed or [], store,
                                         max_conns=max_conns, ranks=ranks, changed=changed,
                                         run=page["run"])
    return page


# The Detailed report (report-detailed.html): the day's numbers against the day before, the
# busy stretches and the queries behind them, what changed, confirmed fixes, every health
# check, the settings audit, index changes, what RDST did and the totals.

NEW_HEAVY_SHARE = 0.05
CHANGED_LIMIT = 5
BUSIEST = 2
BUSY_LEVEL = 0.8
BUSY_REACH = 6
RDST_DID_LIMIT = 40
ANALYSIS_BATCH_S = 600


def what_changed(day: dict[str, Any]) -> list[dict[str, Any]]:
    """Queries against the day before, by average time per call: the ones that got slower or
    faster by a fifth or more, new queries carrying 5% of the day's time or more, and plan
    changes the day filed."""
    queries = [query for query in day.get("queries") or [] if isinstance(query, dict)]
    prior_total = sum(float(query.get("prior_calls") or 0) * float(query.get("prior_mean_ms") or 0)
                      for query in queries)
    added = [event for event in day.get("events") or [] if event.get("type") == "index_added"]
    found: dict[str, list[dict[str, Any]]] = {"slower": [], "faster": [], "new_heavy": [],
                                              "plan_changed": []}
    for query in queries:
        share = float(query.get("share") or 0)
        mean, prior = query.get("mean_ms"), query.get("prior_mean_ms")
        prior_share = (float(query.get("prior_calls") or 0) * float(prior or 0) / prior_total
                       if prior_total else 0.0)
        entry = {"query_key": query.get("query_key"), "display_name": query.get("display_name")
                 or "", "share": share, "before_ms": prior, "after_ms": mean,
                 "change": query.get("latency_change"), "detail": None, "ts": None}
        change = query.get("latency_change")
        if query.get("new") and share >= NEW_HEAVY_SHARE:
            found["new_heavy"].append(entry)
        elif isinstance(change, (int, float)) and change >= MOVE_MIN_RATIO and \
                share >= MOVE_MIN_SHARE:
            found["slower"].append(entry)
        elif isinstance(change, (int, float)) and change <= -MOVE_MIN_RATIO and \
                max(share, prior_share) >= MOVE_MIN_SHARE:
            text = str(query.get("text") or "").lower()
            index = next((event for event in added if re.search(
                rf"\b{re.escape(str(event.get('title') or '').split(' on ')[-1].lower())}\b",
                text)), None)
            if index is not None:
                entry["detail"] = f"After the index added at {clock_text(index['ts'])}"
            found["faster"].append(entry)
    for event in day.get("events") or []:
        if event.get("type") == "plan_change" and event.get("kind") == "created":
            keys = event.get("query_keys") or []
            found["plan_changed"].append({
                "query_key": keys[0] if keys else None,
                "display_name": (day.get("names") or {}).get(keys[0], "") if keys else "",
                "share": None, "before_ms": None, "after_ms": None, "change": None,
                "detail": event.get("detail") or None, "ts": event.get("ts")})
    rows = []
    for kind, entries in found.items():
        entries.sort(key=lambda entry: -(entry["share"] or 0))
        rows += [{"kind": kind, **entry} for entry in entries[:CHANGED_LIMIT]]
    return rows


def detailed_tiles(day: dict[str, Any], max_conns: float | None) -> list[dict[str, Any]]:
    """Six numbers, each with the day before's, or a note when the day before has none."""
    numbers = {row["metric"]: row for row in day.get("numbers") or []
               if isinstance(row, dict) and row.get("metric")}
    note = None if compared_with_prior(day) else NO_PRIOR_DAY

    def pair(metric: str) -> tuple[float | None, float | None, float | None]:
        row = numbers.get(metric) or {}
        value, prior, change = row.get("value"), row.get("prior"), row.get("change")
        return (float(value) if isinstance(value, (int, float)) else None,
                float(prior) if isinstance(prior, (int, float)) else None,
                float(change) if isinstance(change, (int, float)) else None)

    def tile(metric: str, source: str, label: str, unit: str, **extra: Any) -> dict[str, Any]:
        value, prior, change = pair(source)
        return {"metric": metric, "label": label, "value": value, "prior": prior,
                "change": change, "unit": unit,
                "state": "collecting" if value is None else "ok", "limit": None,
                "second": None, "second_prior": None, "prior_note": note, **extra}

    cache = tile("cache_hit", "cache_hit_ratio_min", "Lowest cache hit", "ratio")
    if cache["value"] is not None:
        cache["state"] = "bad" if cache["value"] < 0.8 else "warn" if cache["value"] < 0.9 else "ok"
    deadlocks, deadlocks_prior, _ = pair("deadlocks")
    return [tile("qps", "qps", "Queries per second", "qps"),
            tile("mean_ms", "mean_ms", "Average latency", "ms"),
            tile("p95_ms", "p95_ms", "p95 latency", "ms"),
            tile("connections", "active_conns_peak", "Peak connections", "count",
                 limit=max_conns),
            cache,
            tile("rollbacks_deadlocks", "xact_rollback", "Rollbacks, deadlocks", "count",
                 second=deadlocks, second_prior=deadlocks_prior)]


def _in_window(vitals: dict[str, Any], field: str, start: int, end: int) -> float | None:
    values = [value for ts, value in zip(vitals.get("xs") or [], vitals.get(field) or [])
              if start <= ts < end and isinstance(value, (int, float))]
    return float(sum(values)) if values else None


def busy_stretches(day: dict[str, Any]) -> list[dict[str, Any]]:
    """The spikes the day crossed, or when none did, its busiest stretches; each with the
    queries that carried its time and the rollbacks inside it."""
    names = day.get("names") or {}
    vitals = day.get("vitals") or {}
    stretches = []
    for spike in day.get("spikes") or []:
        stretches.append({
            "kind": "spike", "metric": spike.get("metric"), "start": spike["start"],
            "end": spike["end"], "peak": spike.get("peak"), "usual": spike.get("usual"),
            "ratio": spike.get("ratio"), "summary": spike.get("summary"),
            "queries": [{"query_key": query["query_key"],
                         "display_name": names.get(query["query_key"], ""), "share": None,
                         "before": query.get("before"), "during": query.get("during"),
                         "ratio": query.get("ratio")} for query in spike.get("queries") or []],
            "rollbacks": _in_window(vitals, "xact_rollback", spike["start"], spike["end"])})
    if stretches:
        return stretches
    chart = day.get("chart") or {}
    xs, qps, spent, bands = (chart.get("xs") or [], chart.get("qps") or [],
                             chart.get("ms") or [], chart.get("bands") or [])
    if len(xs) < 3 or not qps:
        return []
    totals = [sum(values[i] or 0 for values in qps) for i in range(len(xs))]
    measured = sorted(total for total in totals if total)
    usual = measured[len(measured) // 2] if measured else 0.0
    res = int(chart.get("res") or 60)
    taken: list[range] = []
    for peak in sorted(range(len(xs)), key=lambda i: -totals[i]):
        if len(stretches) >= BUSIEST or not totals[peak]:
            break
        if any(peak in window for window in taken):
            continue
        first = last = peak
        while first > 0 and peak - first < BUSY_REACH and \
                totals[first - 1] >= totals[peak] * BUSY_LEVEL:
            first -= 1
        while last < len(xs) - 1 and last - peak < BUSY_REACH and \
                totals[last + 1] >= totals[peak] * BUSY_LEVEL:
            last += 1
        window = range(first, last + 1)
        # A second stretch starts clear of the first, with a quiet bucket between them.
        taken.append(range(first - 1, last + 2))
        shares = []
        all_ms = sum(sum(values[i] or 0 for i in window) for values in spent)
        for position, band in enumerate(bands):
            if band.get("kind") != "query" or position >= len(spent):
                continue
            ms = sum(spent[position][i] or 0 for i in window)
            if ms and all_ms:
                shares.append((ms / all_ms, band))
        shares.sort(key=lambda item: -item[0])
        start, end = xs[first], xs[last] + res
        stretches.append({
            "kind": "busiest", "metric": "traffic", "start": start, "end": end,
            "peak": round(totals[peak], 4), "usual": round(usual, 4),
            "ratio": round(totals[peak] / usual, 2) if usual else None, "summary": None,
            "queries": [{"query_key": band["key"], "display_name": band.get("display_name")
                         or names.get(band["key"], ""), "share": round(share, 4),
                         "before": None, "during": None, "ratio": None}
                        for share, band in shares[:3]],
            "rollbacks": _in_window(vitals, "xact_rollback", start, end)})
    return sorted(stretches, key=lambda stretch: stretch["start"])


def _check_number(check: str, numbers: dict[str, Any], collectors: dict[str, Any],
                  day: dict[str, Any], max_conns: float | None) -> str | None:
    """The one number a health check row shows at its right."""
    def value(metric: str) -> float | None:
        found = (numbers.get(metric) or {}).get("value")
        return float(found) if isinstance(found, (int, float)) else None

    problems = {problem.get("check"): problem for problem in
                (day.get("health") or {}).get("problems") or [] if isinstance(problem, dict)}
    if check == "cache":
        lowest = value("cache_hit_ratio_min")
        return f"{lowest * 100:.1f}% lowest" if lowest is not None else None
    if check == "connections":
        peak = value("active_conns_peak")
        if peak is None:
            return None
        return f"{peak:.0f} of {max_conns:.0f}" if max_conns else f"{peak:.0f}"
    if check == "transactions":
        rollbacks, deadlocks = value("xact_rollback"), value("deadlocks")
        return (f"{rollbacks:,.0f} and {deadlocks or 0:.0f}" if rollbacks is not None else None)
    if check == "settings":
        audit = settings_audit(collectors, day)
        changes = sum(1 for row in audit if row["in_actions"])
        if changes:
            return f"{changes} to change"
        return f"{len(audit)} off recommendation" if audit else "none to change"
    if check == "vacuum":
        rows = (problems.get("vacuum_bloat") or {}).get("rows") or []
        return f"{len(rows)} {'table' if len(rows) == 1 else 'tables'}"
    if check == "indexes":
        total = (collectors.get("index_health") or {}).get("total_indexes")
        return f"{total} indexes" if isinstance(total, int) else None
    if check == "replication":
        replication = collectors.get("replication") or {}
        lag = replication.get("upstream_lag_seconds")
        if isinstance(lag, (int, float)):
            return f"{lag:.0f} s behind"
        return "none" if not replication.get("peers") else f"{len(replication['peers'])} replicas"
    if check == "sequential_scans":
        rows = [problem for problem in (day.get("health") or {}).get("problems") or []
                if isinstance(problem, dict) and problem.get("check") == "sequential_scans"]
        return f"{len(rows)} {'table' if len(rows) == 1 else 'tables'}"
    return None


def health_checks(day: dict[str, Any], collectors: dict[str, Any], ranks: dict[str, int],
                  max_conns: float | None) -> list[dict[str, Any]]:
    """Every health check with its state, finding, number, whether it needs you and whether
    Actions lists its fix, worst first."""
    numbers = {row["metric"]: row for row in day.get("numbers") or []
               if isinstance(row, dict) and row.get("metric")}
    acted = checks_in_actions([action for action in day.get("actions") or []
                               if isinstance(action, dict)])
    rows = []
    for check in (day.get("overview") or {}).get("checks") or []:
        name = str(check.get("check") or "")
        status = check.get("status")
        rank = ranks.get(name)
        if rank is None and name == "cache":
            rank = ranks.get("settings")
        rows.append({"check": name,
                     "label": str(check.get("label") or CHECK_LABELS.get(name, name)),
                     "state": status if status in ("ok", "warn", "bad") else "unavailable",
                     "finding": str(check.get("value") or ""),
                     "number": _check_number(name, numbers, collectors, day, max_conns),
                     "fix_rank": rank if status in NEEDS_YOU else None,
                     "needs_you": status in NEEDS_YOU,
                     "in_actions": status in NEEDS_YOU and name in acted})
    rows.sort(key=lambda row: {"bad": 0, "warn": 1, "ok": 2}.get(row["state"], 3))
    return rows


def settings_audit(collectors: dict[str, Any], day: dict[str, Any]) -> list[dict[str, Any]]:
    """The settings off the recommendation: current, recommended and when a change takes
    effect."""
    from features.monitor.db_actions import MYSQL_RESTART, RESTART

    mysql = str(collectors.get("engine") or day.get("engine") or "").lower() == "mysql"
    restart = MYSQL_RESTART if mysql else RESTART
    config = collectors.get("config_audit")
    rows = []
    if isinstance(config, dict):
        for setting in config.get("settings") or []:
            if isinstance(setting, dict) and setting.get("status") in ("warn", "crit"):
                rows.append((str(setting.get("parameter") or ""), str(setting.get("current") or ""),
                             str(setting.get("recommended") or ""), setting.get("note")))
    else:
        for problem in (day.get("health") or {}).get("problems") or []:
            if isinstance(problem, dict) and problem.get("check") == "config_audit":
                for row in problem.get("rows") or []:
                    current, _, recommended = str(row.get("value") or "").partition(
                        ", recommended ")
                    rows.append((str(row.get("name") or ""), current, recommended,
                                 row.get("detail")))
    acted = {str(action["setting"].get("name")) for action in day.get("actions") or []
             if isinstance(action, dict) and isinstance(action.get("setting"), dict)
             and files_action(action)}
    return [{"parameter": name, "current": current, "recommended": recommended,
             "takes_effect": "after a restart" if name in restart else "on reload",
             "note": str(note) if note else None, "in_actions": name in acted}
            for name, current, recommended, note in rows]


def fixes_confirmed(fixed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fixes measured as working in the report's day, with their before and after."""
    rows = []
    for item in fixed:
        try:
            body = json.loads(item["body_json"]) if isinstance(item.get("body_json"), str) \
                else dict(item.get("body_json") or {})
        except ValueError:
            continue
        rows.append({"title": _index_title(body.get("table"), list(body.get("columns") or []),
                                           str(body.get("action_title") or item.get("title")
                                               or "")),
                     "applied_at": body.get("detected_at"),
                     "confirmed_at": item.get("resolved_at") or item.get("updated_at"),
                     "before_ms": body.get("before_ms"), "after_ms": body.get("after_ms"),
                     "query_keys": list(body.get("query_keys") or ([body["query_key"]]
                                                                   if body.get("query_key")
                                                                   else []))})
    return rows


def index_changes_seen(store: Any, target: str, start: int, end: int) -> list[dict[str, Any]]:
    """Indexes added or dropped in the day: whether each came from an RDST action, how its
    action measured, and how often queries read it since."""
    changes = store.read('SELECT ts,kind,"table",index_name,definition FROM index_change '
                         "WHERE target=? AND ts>=? AND ts<? ORDER BY ts,id", (target, start, end))
    if not changes:
        return []
    from features.monitor.actions import APPLIED_STATUS, _applied_index

    by_index: dict[str, dict[str, Any]] = {}
    for row in store.actions(target):
        if row["state"] not in APPLIED_STATUS:
            continue
        name = row["payload"].get("index_name") or _applied_index(store, row["id"])
        if name:
            by_index[str(name)] = row
    scans = {row["index_name"]: row["scans"] for row in store.read(
        "SELECT index_name,scans FROM index_state WHERE target=?", (target,))}
    found = []
    for change in changes:
        action = by_index.get(change["index_name"])
        found.append({"ts": change["ts"], "kind": change["kind"],
                      "index_name": change["index_name"], "table": change["table"],
                      "definition": change["definition"], "from_rdst": action is not None,
                      "action_id": action["id"] if action else None,
                      "status": APPLIED_STATUS[action["state"]] if action else None,
                      "scans": scans.get(change["index_name"])
                      if change["kind"] == "added" else None})
    return found


def what_rdst_did(store: Any, target: str, start: int, end: int,
                  report_id: int) -> list[dict[str, Any]]:
    """RDST's own day, timed: reports filed, changes seen and measured, analyses run, Inbox
    items filed and the checks that ran."""
    rows: list[dict[str, Any]] = []

    def add(ts: int, text: str, detail: str | None = None, *, until: int | None = None,
            item_id: int | None = None, link_report: int | None = None) -> None:
        rows.append({"ts": int(ts), "until": until, "text": text, "detail": detail,
                     "item_id": item_id, "report_id": link_report})

    for run in store.read("SELECT id,trigger,period_start,finished_at,ts FROM report_run "
                          "WHERE target=? AND state='done' AND sample=0 AND id!=? "
                          "AND COALESCE(finished_at,ts)>=? AND COALESCE(finished_at,ts)<?",
                          (target, report_id, start, end)):
        day = run["period_start"] or run["ts"] - 86400
        add(run["finished_at"] or run["ts"],
            f"Filed the report for {_month_day(int(day))}" if run["trigger"] == "nightly"
            else "Ran a report on request", link_report=run["id"])
    for event in store.read("SELECT e.ts,e.kind,e.detail_json,a.payload_json FROM action_event e "
                            "JOIN action a ON a.id=e.action_id WHERE a.target=? AND e.ts>=? "
                            "AND e.ts<? AND e.kind IN ('applied','confirmed','no_effect') "
                            "ORDER BY e.ts,e.id", (target, start, end)):
        payload = json.loads(event["payload_json"] or "{}")
        detail = json.loads(event["detail_json"] or "{}")
        title = str(payload.get("title") or "a change")
        count = len(payload.get("queries") or [])
        if event["kind"] == "applied":
            add(event["ts"], f"Saw {title[:1].lower()}{title[1:]} land and started measuring",
                f"{count} {'query' if count == 1 else 'queries'}" if count else None)
        elif event["kind"] == "confirmed":
            before, after = detail.get("before_ms"), detail.get("after_ms")
            add(event["ts"], f"Confirmed: {title}",
                f"{latency_text(before)} to {latency_text(after)}")
        else:
            add(event["ts"], f"Measured {title[:1].lower()}{title[1:]}: no change yet")
    jobs = store.read("SELECT finished_at FROM analyze_job WHERE target=? AND state='done' "
                      "AND finished_at>=? AND finished_at<? ORDER BY finished_at",
                      (target, start, end))
    batch: list[int] = []
    for job in [*jobs, None]:
        if batch and (job is None or job["finished_at"] - batch[-1] > ANALYSIS_BATCH_S):
            add(batch[0], f"Analyzed {len(batch)} {'query' if len(batch) == 1 else 'queries'}",
                until=batch[-1] if len(batch) > 1 else None)
            batch = []
        if job is not None:
            batch.append(int(job["finished_at"]))
    for item in store.read("SELECT e.ts,i.id,i.title,i.type FROM inbox_event e JOIN inbox_item i "
                           "ON i.id=e.item_id WHERE i.target=? AND e.ts>=? AND e.ts<? "
                           "AND e.kind='created' AND i.delivery!='digest' AND i.type NOT IN "
                           "('daily_summary','report_ready','fix_confirmed','no_change_yet') "
                           "ORDER BY e.ts,e.id", (target, start, end)):
        add(item["ts"], f"Filed an Inbox item: {item['title']}", item_id=item["id"])
    checks = store.read("SELECT COUNT(*) n,MIN(ts) first FROM monitor_pulse WHERE target=? "
                        "AND kind='attention' AND ts>=? AND ts<?", (target, start, end))[0]
    if checks["n"]:
        add(checks["first"], "Ran the attention checks",
            f"{checks['n']} {'run' if checks['n'] == 1 else 'runs'}", until=end)
    rows.sort(key=lambda row: row["ts"])
    return rows[:RDST_DID_LIMIT]


def detailed_view(row: dict[str, Any], report: dict[str, Any], day: dict[str, Any],
                  actions: list[dict[str, Any]], fixed: list[dict[str, Any]], store: Any, *,
                  max_conns: float | None, ranks: dict[str, int],
                  changed: list[dict[str, Any]], run: dict[str, Any]) -> dict[str, Any]:
    """The Detailed sections for one stored run."""
    from shared.monitor.monitor_store import keep_readings_days

    collectors = (report.get("computed") or {}).get("health_collectors") or {}
    period = day.get("period") or {}
    start = int(period.get("start") or row["ts"] - 86400)
    end = int(period.get("end") or row["ts"])
    observed_from, observed_to = period.get("observed_from"), period.get("observed_to")
    readings = (max(0, (int(observed_to) - int(observed_from)) // 60)
                if isinstance(observed_from, int) and isinstance(observed_to, int) else 0)
    return {"tiles": detailed_tiles(day, max_conns), "spikes": busy_stretches(day),
            "compared": compared_with_prior(day), "what_changed": changed,
            "fixes_confirmed": fixes_confirmed(fixed),
            "health_checks": health_checks(day, collectors, ranks, max_conns),
            "settings_audit": settings_audit(collectors, day),
            "index_changes_seen": index_changes_seen(store, row["target"], start, end),
            "what_rdst_did": what_rdst_did(store, row["target"], start, end, row["id"]),
            "totals": {"queries_seen": len(day.get("queries") or []), "readings": readings,
                       "data_kept_days": keep_readings_days(),
                       "data_bytes": int(store.target_size_bytes(row["target"])),
                       "report_number": run["number"], "ran_at": run["ran_at"]}}


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
