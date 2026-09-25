"""Pure comparisons and shortlist selection for the attention cycle."""

from __future__ import annotations

from typing import Any, Callable

WINDOWS = {"1h": 3600, "1d": 86400, "7d": 7 * 86400}


def summarize(rows: list[dict[str, Any]], seconds: int) -> dict[str, dict[str, float]]:
    totals: dict[str, dict[str, float]] = {}
    for row in rows:
        item = totals.setdefault(row["query_key"], {"calls": 0, "total_ms": 0, "temp_blks": 0})
        item["calls"] += row["calls"]
        item["total_ms"] += row["total_ms"]
        item["temp_blks"] += row.get("temp_blks", 0)
    db_ms = sum(value["total_ms"] for value in totals.values())
    return {key: {**value, "mean_ms": value["total_ms"] / value["calls"] if value["calls"] else 0,
                  "qps": value["calls"] / seconds,
                  "db_time_share": value["total_ms"] / db_ms if db_ms else 0}
            for key, value in totals.items()}


def baselines(current: list[dict[str, Any]], history: dict[str, list[dict[str, Any]]],
              *, current_seconds: int = 300,
              window_seconds: dict[str, int] | None = None) -> list[dict[str, Any]]:
    """Compare a current slice with each query's own available prior windows.

    `window_seconds` overrides a window's length with the time RDST was collecting in it,
    so a gap in monitoring doesn't read as a drop in calls.
    """
    now = summarize(current, current_seconds)
    past = {name: summarize(history.get(name, []), (window_seconds or {}).get(name, seconds))
            for name, seconds in WINDOWS.items()}
    result = []
    for key in sorted(set(now) | set().union(*(set(v) for v in past.values()))):
        values = now.get(key, {"calls": 0, "total_ms": 0, "mean_ms": 0,
                               "qps": 0, "db_time_share": 0, "temp_blks": 0})
        ratios = {}
        for name, data in past.items():
            old = data.get(key)
            ratios[name] = {metric: values[metric] / old[metric] if old and old[metric] else None
                            for metric in ("mean_ms", "qps", "db_time_share")}
        result.append({"query_key": key, **values, "baseline": {name: past[name].get(key)
                       for name in WINDOWS}, "ratios": ratios})
    return result


def shortlist(rows: list[dict[str, Any]], stuck_keys: set[str] | None = None,
              *, stopped_keys: set[str] | None = None, limit: int = 15,
              gate: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
              gated: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """The queries worth judging this cycle, most important first.

    `gate` sees each candidate that moved and returns why it stays out (for example
    `{"reason": "small"}`), or None to keep it; kept-out candidates land in `gated`.
    """
    stuck_keys = stuck_keys or set()
    candidates = []
    for row in rows:
        ratios = row["ratios"]
        old = row["baseline"]
        share = row["db_time_share"]
        # A shape that was quiet for an hour can start a fresh heavy episode.
        new_heavy = share >= 0.05 and not old["1h"]
        stopped = row["calls"] == 0 and (stopped_keys is None or row["query_key"] in stopped_keys) and any(
            base and base["qps"] >= 1 / 60 for base in old.values())
        if row["calls"] == 0 and not stopped and row["query_key"] not in stuck_keys:
            continue
        movement = max((abs(ratio - 1) for metrics in ratios.values()
                        for name, ratio in metrics.items()
                        if name in ("mean_ms", "qps") and ratio is not None), default=0)
        typical_qps = next((old[name]["qps"] for name in ("7d", "1d", "1h") if old[name]), 0)
        if (not new_heavy and not stopped and row["query_key"] not in stuck_keys
                and typical_qps <= 3 / 86400):
            continue
        if not (new_heavy or stopped or row["query_key"] in stuck_keys or movement >= 0.2):
            continue
        score = movement * max(share, max((b["db_time_share"] for b in old.values() if b), default=0))
        if new_heavy:
            score += share
        if stopped:
            score += 1
        if row["query_key"] in stuck_keys:
            score += 2
        candidate = {**row, "reason": "stuck" if row["query_key"] in stuck_keys else
                     "stopped" if stopped else "new_heavy" if new_heavy else "mover",
                     "score": score}
        held = gate(candidate) if gate else None
        if held is not None:
            if gated is not None:
                gated[row["query_key"]] = {**held, "score": round(score, 6)}
            continue
        candidates.append(candidate)
    return sorted(candidates, key=lambda row: (-row["score"], row["query_key"]))[:limit]


def group_many(changes: list[dict[str, Any]], event_scope: str = "separate") -> list[dict[str, Any]]:
    qualifying = [item for item in changes if item.get("attention") in ("inbox", "notify")]
    query_count = len({item["query_key"] for item in qualifying})
    if query_count < 2 or (query_count <= 5 and event_scope != "database_wide"):
        return qualifying
    return [{"type": "many_queries_changed", "subject": "database", "queries": qualifying,
             "attention": "notify" if any(i["attention"] == "notify" for i in qualifying) else "inbox"}]
