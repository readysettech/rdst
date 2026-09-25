"""Who hears about a finding, and how loudly (NOTIFICATION_MODEL.md).

Load is the execution time RDST recorded for a query (the per-minute pg_stat_statements
deltas in monitor.db) over a window, divided by the recorded time of every query in the
same window. Lifetime pg_stat_statements totals never enter it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable

from shared.monitor.monitor_store import MonitorStore

RELEVANT_RANK = 10
RELEVANT_SHARE = 0.02
DAY_S = 86400
HOUR_S = 3600
DEFAULT_LEARNING_S = 86400
TOP_CHECKS_TO_NOTIFY = 2

# Kinds whose default is the digest: small or good news, reported once a day.
DIGEST_KINDS = {"slow_creep", "query_improved"}
LATENCY_KINDS = {"latency_regression", "plan_change", "table_slowdown"}
DATABASE_KINDS = {"rollback_or_deadlock_spike", "cache_hit_ratio_drop"}


def query_totals(store: MonitorStore, target: str, start: int, end: int) -> dict[str, dict]:
    """Recorded calls and execution time per query in [start, end).

    A complete coarser bucket wins over the finer rows it covers (fine rows may have been
    demoted for the size budget, and counting both would double-count); minute rows stay at
    the live edge, where no coarser bucket is closed yet.
    """
    from features.monitor.api.views import stmt_totals

    return stmt_totals(store, target, start, end)


@dataclass
class LoadWindow:
    """Rank (1 = most recorded time) and share of recorded time per query in one window."""

    rank: dict[str, int] = field(default_factory=dict)
    share: dict[str, float] = field(default_factory=dict)

    @classmethod
    def of(cls, totals: dict[str, float]) -> "LoadWindow":
        db_ms = sum(totals.values())
        ordered = sorted((key for key, value in totals.items() if value > 0),
                         key=lambda key: (-totals[key], key))
        return cls({key: index + 1 for index, key in enumerate(ordered)},
                   {key: totals[key] / db_ms if db_ms else 0.0 for key in totals})

    def top(self, key: str) -> bool:
        return self.rank.get(key, RELEVANT_RANK + 1) <= RELEVANT_RANK

    def heavy(self, key: str) -> bool:
        return self.share.get(key, 0.0) >= RELEVANT_SHARE


@dataclass
class Relevance:
    """The relevance gate over the last 24 h and the last hour of recorded load and calls."""

    day: LoadWindow = field(default_factory=LoadWindow)
    hour: LoadWindow = field(default_factory=LoadWindow)
    calls_day: LoadWindow = field(default_factory=LoadWindow)
    calls_hour: LoadWindow = field(default_factory=LoadWindow)
    # Rules the current cycle's measurements earned (`big_change`, `new_heavy`).
    measured: dict[str, str] = field(default_factory=dict)

    def rule(self, key: str) -> str | None:
        """The first rule that lets a query reach the inbox, or None (digest only).

        top_10_24h / top_10_1h: in the top 10 by share of recorded time in that window.
        share_24h / share_1h: at least 2% of recorded time in that window.
        big_change / new_heavy: this cycle's change is large for the whole database.
        Calls ranks are evidence only: a busy but cheap query never qualifies by its calls.
        """
        if self.day.top(key):
            return "top_10_24h"
        if self.hour.top(key):
            return "top_10_1h"
        if self.day.heavy(key):
            return "share_24h"
        if self.hour.heavy(key):
            return "share_1h"
        return self.measured.get(key)

    def relevant(self, key: str) -> bool:
        return self.rule(key) is not None

    def evidence(self, key: str) -> dict[str, Any]:
        return {"relevance_rule": self.rule(key),
                "load_rank_24h": self.day.rank.get(key),
                "load_share_24h": round(self.day.share.get(key, 0.0), 6),
                "load_rank_1h": self.hour.rank.get(key),
                "load_share_1h": round(self.hour.share.get(key, 0.0), 6),
                "calls_rank_24h": self.calls_day.rank.get(key),
                "calls_rank_1h": self.calls_hour.rank.get(key)}


def relevance(store: MonitorStore, target: str, now: int) -> Relevance:
    """Rank queries by recorded load and calls over the rolling 24 h and 1 h windows."""
    system = store.system_queries(target)
    totals = {seconds: {key: row for key, row in
                        query_totals(store, target, now - seconds, now + 1).items()
                        if key not in system}
              for seconds in (DAY_S, HOUR_S)}

    def window(seconds: int, metric: str) -> LoadWindow:
        return LoadWindow.of({key: float(row[metric] or 0)
                              for key, row in totals[seconds].items()})

    return Relevance(window(DAY_S, "total_ms"), window(HOUR_S, "total_ms"),
                     window(DAY_S, "calls"), window(HOUR_S, "calls"))


# A change counts as big when the extra database time it adds in five minutes is at least
# this share of all recorded time in those five minutes.
BIG_CHANGE_SHARE = 0.05
# Otherwise a query that matters must move this much against its usual level to be judged.
MATERIAL_LATENCY = 2.0
MATERIAL_CALLS = 2.0
MATERIAL_SHARE = 0.01


def extra_ms(row: dict[str, Any]) -> float:
    """Database time the query spent in the current five minutes beyond its usual rate."""
    base = (row.get("baseline") or {}).get("1h") or {}
    return max(0.0, float(row.get("total_ms") or 0) -
               float(base.get("qps") or 0) * 300 * float(base.get("mean_ms") or 0))


def importance(row: dict[str, Any], rel: Relevance, db_ms: float) -> str | None:
    """Why a moving query matters enough to be judged, or None (it only counts as a smaller
    query that also moved). Stuck queries always matter; so does a query in the top 10 by
    database time over the last day or hour (a new query entering the top 10 included), a
    new heavy query, and a change big in absolute terms."""
    key = row["query_key"]
    if row.get("reason") == "stuck":
        return "stuck"
    if rel.day.top(key):
        return "top_10_24h"
    if rel.hour.top(key):
        return "top_10_1h"
    if row.get("reason") == "new_heavy":
        rel.measured[key] = "new_heavy"
        return "new_heavy"
    if db_ms > 0 and extra_ms(row) >= BIG_CHANGE_SHARE * db_ms:
        rel.measured[key] = "big_change"
        return "big_change"
    return None


def learning_seconds(store: MonitorStore | None = None, target: str | None = None) -> int:
    """The learning period: the database's dev_learning_period_s lever, else the env default."""
    if store is not None and target is not None:
        override = store.levers(target)["dev_learning_period_s"]
        if override is not None:
            return override
    try:
        return max(0, int(os.environ.get("RDST_MONITOR_LEARNING_S", DEFAULT_LEARNING_S)))
    except ValueError:
        return DEFAULT_LEARNING_S


def learning_until(store: MonitorStore, target: str) -> int | None:
    """When the first-connection learning period ends, or None once history covers it.

    The period runs from the oldest recorded database reading; before it ends there is no
    baseline to judge change against, so change findings stay in the digest.
    """
    rows = store.read("SELECT MIN(bucket_start) first FROM db_series WHERE target=?", (target,))
    first = rows[0]["first"] if rows else None
    if first is None:
        return None
    return first + learning_seconds(store, target)


def is_learning(store: MonitorStore, target: str, now: int) -> bool:
    until = learning_until(store, target)
    return until is not None and now < until


def discovery_until(store: MonitorStore, target: str) -> int | None:
    """When the first hour of a first run ends (from enabled_at), or None.

    On a first run every query found before then is part of the database's existing
    workload, not new. A database with readings from before enabled_at already has a known
    workload, so its new queries are new.
    """
    # first_hour is empty when readings from before enabled_at exist.
    start = store.first_hour(target)[0]
    return start + HOUR_S if start else None


def change_delivery(kind: str, attention: str, key: str | None, rel: Relevance, *,
                    learning: bool, top_checks: int = 0) -> str:
    """The delivery level for one change finding about one query (or the database).

    - During the learning period every change finding goes to the digest.
    - Only queries in the top 10 by load, or at least 2% of it, reach the inbox.
    - Latency and plan regressions notify when Jev says notify; traffic spikes are worth a
      look (inbox), and so is a stopped query that carried load.
    - A new query notifies once it has been in the top 10 by last-hour load on two
      consecutive checks.
    - Slow creep and improvements are digest-only.
    """
    if learning or kind in DIGEST_KINDS:
        return "digest"
    if kind in DATABASE_KINDS:
        return "notify" if attention == "notify" else "inbox"
    if key is None:
        return "inbox"
    if not rel.relevant(key):
        return "digest"
    if kind == "new_heavy_query":
        if top_checks < TOP_CHECKS_TO_NOTIFY:
            return "digest"
        return "notify" if attention == "notify" else "inbox"
    if kind in LATENCY_KINDS:
        return "notify" if attention == "notify" else "inbox"
    return "inbox"


def group_delivery(members: list[dict[str, Any]], rel: Relevance, *, learning: bool) -> str:
    """A many-queries episode is as loud as its loudest relevant member.

    A new query inside an episode is listed but never pushes on its own: the two-check rule
    belongs to its own item.
    """
    order = ("digest", "inbox", "notify")
    levels = []
    for member in members:
        level = change_delivery(member["kind"], member["attention"], member["query_key"], rel,
                                learning=learning, top_checks=TOP_CHECKS_TO_NOTIFY)
        if member["kind"] == "new_heavy_query" and level == "notify":
            level = "inbox"
        levels.append(level)
    return max(levels, key=order.index, default="digest")


def moved_ms(row: dict[str, Any]) -> float:
    """How far the query's database time in the current five minutes is from its usual rate,
    either way."""
    base = (row.get("baseline") or {}).get("1h") or {}
    return abs(float(row.get("total_ms") or 0) -
               float(base.get("qps") or 0) * 300 * float(base.get("mean_ms") or 0))


def material(row: dict[str, Any], db_ms: float) -> bool:
    """Whether a query's move is worth judging: latency 2x (or half) of its usual level, or
    calls 2x (or half), and the move shifts at least 1% of the database's recorded
    time; or it is big in absolute terms on its own. The usual level is the last hour, else
    the day, else the week."""
    if row.get("reason") in ("stuck", "stopped", "new_heavy"):
        return True
    if db_ms > 0 and extra_ms(row) >= BIG_CHANGE_SHARE * db_ms:
        return True
    if db_ms > 0 and (row.get("baseline") or {}).get("1h") and (
            moved_ms(row) < MATERIAL_SHARE * db_ms):
        return False
    ratios = row.get("ratios") or {}
    for window in ("1h", "1d", "7d"):
        values = ratios.get(window) or {}
        latency, calls = values.get("mean_ms"), values.get("qps")
        if latency is None and calls is None:
            continue
        return bool((latency and (latency >= MATERIAL_LATENCY or
                                  latency <= 1 / MATERIAL_LATENCY)) or
                    (calls and (calls >= MATERIAL_CALLS or calls <= 1 / MATERIAL_CALLS)))
    return False


def attention_gate(rel: Relevance, db_ms: float,
                   recurring: Callable[[dict[str, Any]], Any] | None = None,
                   notes: dict[str, dict[str, Any]] | None = None,
                   intermittent: Callable[[dict[str, Any]], Any] | None = None
                   ) -> Callable[[dict[str, Any]], dict[str, Any] | None]:
    """The shortlist gate, before Jev: a change is judged only when the query matters
    (`importance`) and the change isn't one of its usual recurring bursts.

    Kept-out candidates come back as `{"reason": "small"}` (a query without real load),
    `{"reason": "minor"}` (a query that matters, moving less than `material` asks) or
    `{"reason": "recurring", "pattern": ...}`, or `{"reason": "intermittent"}` for a stopped
    query whose normal pattern is to go quiet and come back. A recurring burst bigger or longer than usual
    stays in, and its pattern goes to `notes` so the item can say so.
    """
    def gate(row: dict[str, Any]) -> dict[str, Any] | None:
        if importance(row, rel, db_ms) is None:
            return {"reason": "small"}
        # Going quiet is only news for a query with a steady rate.
        quiet = intermittent(row) if intermittent and row.get("reason") == "stopped" else None
        if quiet:
            return {"reason": "intermittent", "pattern": quiet}
        if not material(row, db_ms):
            return {"reason": "minor"}
        found = recurring(row) if recurring is not None else None
        if found is not None:
            if found.verdict == "usual":
                return {"reason": "recurring", "pattern": found.evidence()}
            if notes is not None:
                notes[row["query_key"]] = found.evidence()
        return None

    return gate
