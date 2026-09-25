"""Recurring bursts: a query that jumps on a schedule is normal, not news.

Each query's recorded five-minute series over the last week is split into bursts: runs of
buckets well above the query's own typical level (calls, or mean latency). A burst is
recurring when earlier bursts repeat at a steady period (of the last six gaps between
onsets, at least three and at least two thirds within 40% of their median) or at the same
time of day on at least three earlier days. A recurring
burst stays quiet unless it is bigger (peak over 1.5x the largest earlier one) or longer
(over 1.5x the longest earlier one) than usual.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any

from shared.monitor.monitor_store import MonitorStore

LOOKBACK_S = 7 * 86400
BUCKET_S = 300
MIN_REPEATS = 3
MAX_GAPS = 6
PERIOD_TOLERANCE = 0.4
STEADY_SHARE = 2 / 3
MIN_PERIOD_S = 15 * 60
MAX_PERIOD_S = 26 * 3600
DAILY_TOLERANCE_S = 30 * 60
BIGGER = 1.5
LONGER = 1.5
CALLS_RISE = 2.0
LATENCY_RISE = 1.5
# The live burst must reach this close to now to be the one being judged.
LIVE_EDGE_S = 2 * BUCKET_S
DAY_S = 86400


@dataclass(frozen=True)
class Burst:
    start: int
    end: int
    peak: float

    @property
    def duration(self) -> int:
        return self.end - self.start


@dataclass
class Pattern:
    """How a query's bursts recur, and how the live one compares."""

    metric: str
    kind: str  # "period" or "daily"
    repeats: int
    period_s: int
    last_onset: int
    usual_peak: float
    usual_duration_s: int
    verdict: str = "usual"  # "usual", "bigger" or "longer"

    def evidence(self) -> dict[str, Any]:
        return {"metric": self.metric, "kind": self.kind, "repeats": self.repeats,
                "period_s": self.period_s, "last_onset": self.last_onset,
                "usual_peak": round(self.usual_peak, 3),
                "usual_duration_s": self.usual_duration_s, "verdict": self.verdict}


def bursts(points: list[tuple[int, float | None]], metric: str) -> list[Burst]:
    """Runs of buckets well above the series' own typical level; one quiet bucket inside a run
    doesn't end it."""
    values = [value for _, value in points if value is not None]
    if metric == "latency":
        values = [value for value in values if value > 0]
    if len(values) < 12:
        return []
    typical = statistics.median(values)
    if metric == "calls":
        level = max(typical * CALLS_RISE, typical + 1)
    else:
        level = typical * LATENCY_RISE
    found: list[Burst] = []
    start = end = None
    peak = 0.0
    quiet = 0
    for ts, value in points:
        high = value is not None and value > level
        if high:
            if start is None:
                start, peak = ts, 0.0
            end, peak, quiet = ts + BUCKET_S, max(peak, float(value)), 0
        elif start is not None:
            quiet += 1
            if quiet > 1:
                found.append(Burst(start, end, peak))
                start = None
    if start is not None:
        found.append(Burst(start, end, peak))
    return found


def _steady(gaps: list[int]) -> tuple[int, int] | None:
    """(period, repeats) when most recent gaps sit near their median."""
    gaps = gaps[-MAX_GAPS:]
    if len(gaps) < MIN_REPEATS:
        return None
    period = statistics.median(gaps)
    if not MIN_PERIOD_S <= period <= MAX_PERIOD_S:
        return None
    near = sum(abs(gap - period) <= PERIOD_TOLERANCE * period for gap in gaps)
    if near >= MIN_REPEATS and near >= STEADY_SHARE * len(gaps):
        return int(period), near
    return None


def _periodic(prior: list[Burst], current: Burst) -> tuple[int, int] | None:
    """(period, repeats) when onsets repeat at a steady period and the live one is on time."""
    onsets = [burst.start for burst in prior] + [current.start]
    gaps = [later - earlier for earlier, later in zip(onsets, onsets[1:])]
    found = _steady(gaps)
    if found is None or abs(gaps[-1] - found[0]) > PERIOD_TOLERANCE * found[0]:
        return None
    return found


def _daily(prior: list[Burst], current: Burst) -> int:
    """Earlier days with a burst starting within half an hour of the live one's time of day."""
    days = set()
    for burst in prior:
        offset = (current.start - burst.start) % DAY_S
        if current.start - burst.start >= DAY_S - DAILY_TOLERANCE_S and (
                offset <= DAILY_TOLERANCE_S or offset >= DAY_S - DAILY_TOLERANCE_S):
            days.add(round((current.start - burst.start) / DAY_S))
    return len(days)


def pattern(points: list[tuple[int, float | None]], now: int, metric: str) -> Pattern | None:
    """The recurring pattern the live burst belongs to, with its verdict, or None."""
    found = bursts(points, metric)
    if not found or found[-1].end < now - LIVE_EDGE_S:
        return None
    current, prior = found[-1], found[:-1]
    periodic = _periodic(prior, current)
    daily = _daily(prior, current)
    if periodic:
        period, repeats = periodic
        used = prior[-MAX_GAPS:]
        # A steady 24 hours is a daily job, said as a time of day.
        kind = "daily" if abs(period - DAY_S) <= DAILY_TOLERANCE_S else "period"
    elif daily >= MIN_REPEATS:
        period, repeats, kind = DAY_S, daily, "daily"
        used = [burst for burst in prior if current.start - burst.start >= DAY_S -
                DAILY_TOLERANCE_S and min((current.start - burst.start) % DAY_S,
                                          DAY_S - (current.start - burst.start) % DAY_S)
                <= DAILY_TOLERANCE_S]
    else:
        return None
    usual_peak = max(burst.peak for burst in used)
    usual_duration = max(burst.duration for burst in used)
    verdict = "usual"
    if current.peak > usual_peak * BIGGER:
        verdict = "bigger"
    elif current.duration > usual_duration * LONGER + BUCKET_S:
        verdict = "longer"
    return Pattern(metric, kind, repeats, period, current.start, usual_peak, usual_duration,
                   verdict)


def quiet_between_bursts(points: list[tuple[int, float | None]], now: int) -> Pattern | None:
    """A query that runs only in recurring bursts and is between two of them: its stopping is
    the usual end of a burst, as long as the silence isn't longer than its usual gaps."""
    found = bursts(points, "calls")
    if len(found) < MIN_REPEATS + 1 or found[-1].end >= now - LIVE_EDGE_S:
        return None
    onsets = [burst.start for burst in found]
    steady = _steady([later - earlier for earlier, later in zip(onsets, onsets[1:])])
    if steady is None:
        return None
    period, repeats = steady
    if now - found[-1].start > period * (1 + PERIOD_TOLERANCE) * LONGER:
        return None
    return Pattern("calls", "period", repeats, period, found[-1].start,
                   max(burst.peak for burst in found[-MAX_GAPS:]),
                   max(burst.duration for burst in found[-MAX_GAPS:]))


# A query is intermittent when it ran in fewer than half of the five-minute readings since it
# first ran, or went silent for half an hour or more and came back at least 3 times while
# running in under 70% of them. A steady query that stopped once or twice before still
# counts as steady.
SILENT_RUN = 6
INTERMITTENT_RETURNS = 3
BUSY_SHARE = 0.5
RETURNING_BUSY_SHARE = 0.7


def intermittent(points: list[tuple[int, float | None]], now: int) -> dict[str, Any] | None:
    """Evidence that a query's normal pattern is to go quiet and come back, or None when it
    runs at a steady rate (only then is a silence news)."""
    seen = [(ts, value) for ts, value in points if ts < now - SILENT_RUN * BUCKET_S]
    first = next((index for index, (_, value) in enumerate(seen) if value), None)
    if first is None:
        return None
    seen = seen[first:]
    returns, silent = 0, 0
    for _, value in seen:
        if value:
            returns += silent >= SILENT_RUN
            silent = 0
        else:
            silent += 1
    busy = sum(1 for _, value in seen if value) / len(seen)
    if len(seen) >= 12 and (busy < BUSY_SHARE or (returns >= INTERMITTENT_RETURNS and
                                                  busy < RETURNING_BUSY_SHARE)):
        return {"kind": "intermittent", "returns": returns, "busy_share": round(busy, 3)}
    return None


@dataclass
class RecurringCheck:
    """Reads each query's week of five-minute readings once per cycle and says whether its
    live change is a recurring burst."""

    store: MonitorStore
    target: str
    now: int
    _observed: set[int] | None = field(default=None, init=False)

    def observed(self) -> set[int]:
        """Five-minute buckets in which RDST was collecting from this database."""
        if self._observed is None:
            rows = self.store.read(
                "SELECT DISTINCT bucket_start/300*300 AS b FROM db_series WHERE target=? "
                "AND res IN (60,300) AND bucket_start>=? AND bucket_start<?",
                (self.target, self.now - LOOKBACK_S, self.now + 1))
            self._observed = {row["b"] for row in rows}
        return self._observed

    def series(self, key: str, metric: str) -> list[tuple[int, float | None]]:
        rows = self.store.read(
            "SELECT res,bucket_start,calls,total_ms FROM stmt_series WHERE target=? AND "
            "query_key=? AND res IN (60,300) AND bucket_start>=? AND bucket_start<?",
            (self.target, key, self.now - LOOKBACK_S, self.now + 1))
        coarse = {row["bucket_start"]: row for row in rows if row["res"] == BUCKET_S}
        fine: dict[int, list[float]] = {}
        for row in rows:
            if row["res"] == 60 and row["bucket_start"] // BUCKET_S * BUCKET_S not in coarse:
                entry = fine.setdefault(row["bucket_start"] // BUCKET_S * BUCKET_S, [0, 0.0])
                entry[0] += row["calls"]
                entry[1] += row["total_ms"]
        totals = {ts: (row["calls"], row["total_ms"]) for ts, row in coarse.items()}
        totals.update({ts: (calls, ms) for ts, (calls, ms) in fine.items()})
        points: list[tuple[int, float | None]] = []
        for ts in sorted(self.observed() | set(totals)):
            calls, total = totals.get(ts, (0, 0.0))
            if metric == "calls":
                points.append((ts, float(calls)))
            else:
                points.append((ts, total / calls if calls else None))
        return points

    def intermittent(self, row: dict[str, Any]) -> dict[str, Any] | None:
        """For a stopped query: evidence that going quiet is its normal pattern."""
        if row.get("reason") != "stopped":
            return None
        return intermittent(self.series(row["query_key"], "calls"), self.now)

    def check(self, row: dict[str, Any]) -> Pattern | None:
        """The pattern that explains this shortlist row's change, or None.

        Every metric the query moved on must be recurring; the pattern of the last one checked
        comes back, with a verdict other than "usual" when this burst is bigger or longer.
        """
        key = row["query_key"]
        reason = row.get("reason")
        if reason == "stuck":
            return None
        if reason == "stopped":
            return quiet_between_bursts(self.series(key, "calls"), self.now)
        metrics = ["calls"] if reason == "new_heavy" else _moved(row)
        found = None
        for metric in metrics:
            current = pattern(self.series(key, metric), self.now, metric)
            if current is None:
                return None
            if found is None or current.verdict != "usual":
                found = current
        return found


def _moved(row: dict[str, Any]) -> list[str]:
    moved = []
    ratios = row.get("ratios") or {}
    for metric, name in (("calls", "qps"), ("latency", "mean_ms")):
        if any(abs(value[name] - 1) >= 0.2 for value in ratios.values()
               if value and value.get(name) is not None):
            moved.append(metric)
    return moved or ["calls"]
