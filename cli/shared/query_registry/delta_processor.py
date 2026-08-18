"""Epoch-aware counter delta processing for engine statement statistics.

Pure functions that turn consecutive snapshots of cumulative statement
counters (pg_stat_statements, performance_schema summary tables) into
per-window deltas that are honest about resets, evictions, and epoch
boundaries. Design source: docs/architecture/rdst-query-observation-
deep-research.md, section Q9 ("Rules that prevent invalid deltas").

The module is deliberately dependency-light (stdlib only) and clock-free:
every timestamp is passed in by the caller, so the logic is trivially
testable and reusable by any collector.

Completeness vocabulary (one value per result):
- 'complete'            valid window; deltas are real (may be zero).
- 'counter_regression'  a monotonic counter went backward; delta dropped
                        (Datadog behavior), lifetime attribution follows
                        PMM (full current counter).
- 'reinserted'          the engine entry was recreated (definitive via
                        stats_since / FIRST_SEEN, or heuristic); current
                        row becomes the new baseline, no delta.
- 'absent'              key present in the baseline but not in the
                        snapshot; eviction, reset, and inactivity are
                        indistinguishable, so nothing is concluded and
                        the baseline entry is retained.
- 'indeterminate'       zero delta while the caller signalled a possible
                        reset; idle and reset-plus-identical-count are
                        indistinguishable (PMM's admission, Q9 rule 12).
- 'epoch_changed'       baseline and snapshot come from different stats
                        epochs; diffing across them is invalid.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable, Mapping, Optional

COMPLETE = "complete"
COUNTER_REGRESSION = "counter_regression"
REINSERTED = "reinserted"
ABSENT = "absent"
INDETERMINATE = "indeterminate"
EPOCH_CHANGED = "epoch_changed"

COMPLETENESS_VALUES = frozenset(
    {COMPLETE, COUNTER_REGRESSION, REINSERTED, ABSENT, INDETERMINATE, EPOCH_CHANGED}
)


@dataclass(frozen=True)
class CounterRow:
    """One engine statistics row at a point in time.

    engine_key is the engine's own join key (PG: userid/dbid/queryid/toplevel;
    MySQL: schema/digest), valid for diffing only within one epoch_id.
    stats_since is PG 17+ per-row creation time; first_seen is MySQL's
    FIRST_SEEN. Either moving forward between snapshots proves the row was
    recreated. minmax_stats_since is PG 17+ per-row min/max reset time: it
    moving forward while stats_since holds still means only min/max were
    reset (pg_stat_statements_reset(minmax_only => true)), not the row.
    Timestamps are opaque comparables supplied by the caller (unix seconds
    recommended).
    """

    engine_key: str
    calls: int
    rows: int
    total_exec_time: float
    mean_exec_time: float
    min_exec_time: float
    max_exec_time: float
    captured_at: float
    epoch_id: str
    stats_since: Optional[float] = None
    first_seen: Optional[float] = None
    minmax_stats_since: Optional[float] = None


@dataclass(frozen=True)
class DeltaResult:
    """Outcome of diffing one engine_key across a window.

    calls_delta / exec_time_delta / approximate_qps are None whenever the
    window could not be computed (every completeness except 'complete');
    a baseline observation additionally sets is_baseline=True so a first
    sighting can never be mistaken for a zero-traffic window.

    calls_total_attribution is the amount the caller should add to its
    running lifetime call total (PMM semantics): the window delta when the
    window is valid, the full current counter when the counter restarted
    (regression / reinsert / epoch change / first observation), and zero
    when nothing new was observed. Summed across snapshots it tracks the
    engine's lifetime count and never regresses.

    window_start / window_end are the row's own window (previous capture to
    current capture) and can span several collection cycles when the
    baseline row was retained through 'absent' windows.
    """

    engine_key: str
    window_start: float
    window_end: float
    completeness: str
    calls_delta: Optional[int]
    exec_time_delta: Optional[float]
    approximate_qps: Optional[float]
    calls_total_attribution: int
    is_baseline: bool = False


def compute_pg_epoch_id(postmaster_start_time: object, stats_reset: object = None) -> str:
    """Epoch identity for pg_stat_statements (Q9 rule 5).

    Hashes pg_postmaster_start_time() together with
    pg_stat_statements_info.stats_reset; below PG 14 the info view does not
    exist, so pass stats_reset=None and only the start time is hashed.
    dealloc is deliberately not part of the identity (Q9 rule 6).
    """
    material = str(postmaster_start_time)
    if stats_reset is not None:
        material += "|" + str(stats_reset)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def compute_mysql_epoch_id(uptime_or_server_start: object) -> str:
    """Epoch identity for performance_schema statement summaries.

    Derive from the server start moment (e.g. now - Uptime, or a start
    timestamp); a restart clears performance_schema, so the start moment is
    the epoch. TRUNCATE of a single summary table is per-row-visible via
    FIRST_SEEN and is handled in process_snapshot, not here.
    """
    return hashlib.sha256(str(uptime_or_server_start).encode("utf-8")).hexdigest()[:16]


def _moved_forward(prev_value: Optional[float], curr_value: Optional[float]) -> bool:
    return prev_value is not None and curr_value is not None and curr_value > prev_value


def _looks_reinserted(prev: CounterRow, curr: CounterRow, *, minmax_reset: bool = False) -> bool:
    """Datadog-style execution-indicators check for evict-then-reinsert.

    A surviving statistics entry advances calls and total_exec_time together
    (both update atomically when an execution completes) and its min can only
    fall. Counters that went backward are caught earlier as a regression; the
    residual evict+reinsert case yields a positive-but-wrong diff, detected
    conservatively by two shapes a live entry cannot produce:

    - same call count with a higher total time (Datadog's documented case:
      "evicted then re-inserted with same call count (usually 1) and slight
      duration change");
    - min_exec_time rose, which only a recreated entry can show, unless
      minmax_reset is set: on PG 17+, pg_stat_statements_reset(minmax_only =>
      true) rebuilds min/max in place (minmax_stats_since advances while
      stats_since holds still), so a risen min proves nothing about the row.
      Q9 rule 9 stores the min/max window separately from the counter window;
      a min/max-only reset must never invalidate a counter window, so the
      min-based (and any max-based) reasoning is skipped and the delta is
      computed normally from calls / total_exec_time, which minmax_only
      leaves untouched. Without minmax_stats_since (pre-17, or unchanged)
      the min indicator stays active.
    """
    calls_delta = curr.calls - prev.calls
    time_delta = curr.total_exec_time - prev.total_exec_time
    if calls_delta == 0 and time_delta > 0:
        return True
    if not minmax_reset and curr.min_exec_time > prev.min_exec_time:
        return True
    return False


def process_snapshot(
    prev: Mapping[str, CounterRow],
    curr: Iterable[CounterRow],
    window_start: float,
    window_end: float,
    *,
    reset_suspected: bool = False,
) -> list[DeltaResult]:
    """Diff a snapshot against the previous baseline, one result per key.

    Rules are applied per row in this order: epoch guard, definitive
    recreation (stats_since / FIRST_SEEN), negative-delta guard,
    evict-reinsert heuristic, zero-delta handling, normal delta. Keys in
    prev that are missing from curr are reported as 'absent' (Q9 rule 10)
    and must stay in the baseline; use advance_baseline() to carry the
    baseline forward.

    reset_suspected: zero call deltas default to 'complete' with
    calls_delta=0 because a genuinely idle query is normal; a blanket
    'indeterminate' would poison every quiet window. Only when the caller
    has an independent reset signal (e.g. ambiguous epoch inputs this
    round) does zero-delta become 'indeterminate', encoding PMM's
    admission that a reset plus identical call count is indistinguishable
    from idle (Q9 rule 12).

    window_start / window_end are fallbacks only: each result's window is
    derived per row as prev.captured_at -> curr.captured_at, because a
    baseline row retained through 'absent' windows accumulates its counter
    difference since its own capture, not since the last global window.
    The arguments fill in where a row carries no capture time, and bound
    'absent' results, which have no current capture.
    """
    results: list[DeltaResult] = []
    seen: set[str] = set()

    def emit(
        row_key: str,
        completeness: str,
        calls_delta: Optional[int] = None,
        exec_time_delta: Optional[float] = None,
        approximate_qps: Optional[float] = None,
        attribution: int = 0,
        is_baseline: bool = False,
        start: Optional[float] = None,
        end: Optional[float] = None,
    ) -> None:
        results.append(
            DeltaResult(
                engine_key=row_key,
                window_start=window_start if start is None else start,
                window_end=window_end if end is None else end,
                completeness=completeness,
                calls_delta=calls_delta,
                exec_time_delta=exec_time_delta,
                approximate_qps=approximate_qps,
                calls_total_attribution=attribution,
                is_baseline=is_baseline,
            )
        )

    for row in curr:
        seen.add(row.engine_key)
        base = prev.get(row.engine_key)
        row_end = row.captured_at if row.captured_at is not None else window_end
        row_start = (
            base.captured_at
            if base is not None and base.captured_at is not None
            else window_start
        )

        if base is None:
            # First observation is a baseline, not a window.
            emit(row.engine_key, COMPLETE, attribution=row.calls, is_baseline=True,
                 start=row_start, end=row_end)
            continue

        if base.epoch_id != row.epoch_id:
            emit(row.engine_key, EPOCH_CHANGED, attribution=row.calls,
                 start=row_start, end=row_end)
            continue

        # PG 17+ stats_since and MySQL FIRST_SEEN moving forward prove the
        # entry was recreated; definitive, so the heuristic is skipped.
        if _moved_forward(base.stats_since, row.stats_since) or _moved_forward(
            base.first_seen, row.first_seen
        ):
            emit(row.engine_key, REINSERTED, attribution=row.calls,
                 start=row_start, end=row_end)
            continue

        # Negative movement in any monotonic counter invalidates the whole
        # row for this window (Datadog drops the row, not just the metric).
        if (
            row.calls < base.calls
            or row.total_exec_time < base.total_exec_time
            or row.rows < base.rows
        ):
            emit(row.engine_key, COUNTER_REGRESSION, attribution=row.calls,
                 start=row_start, end=row_end)
            continue

        # minmax_stats_since advancing while stats_since held still (a
        # stats_since move was handled above) means a PG 17+ minmax_only
        # reset rebuilt min/max in place; the heuristic must ignore them.
        minmax_reset = _moved_forward(base.minmax_stats_since, row.minmax_stats_since)
        if _looks_reinserted(base, row, minmax_reset=minmax_reset):
            emit(row.engine_key, REINSERTED, attribution=row.calls,
                 start=row_start, end=row_end)
            continue

        calls_delta = row.calls - base.calls
        if calls_delta == 0:
            if reset_suspected:
                emit(row.engine_key, INDETERMINATE, start=row_start, end=row_end)
            else:
                emit(row.engine_key, COMPLETE, calls_delta=0, exec_time_delta=0.0,
                     approximate_qps=0.0, attribution=0, start=row_start, end=row_end)
            continue

        exec_time_delta = row.total_exec_time - base.total_exec_time
        qps = calls_delta / max(row_end - row_start, 1.0)
        emit(row.engine_key, COMPLETE, calls_delta=calls_delta,
             exec_time_delta=exec_time_delta, approximate_qps=qps,
             attribution=calls_delta, start=row_start, end=row_end)

    for key in prev:
        if key not in seen:
            emit(key, ABSENT)

    return results


def advance_baseline(
    prev: Mapping[str, CounterRow], curr: Iterable[CounterRow]
) -> dict[str, CounterRow]:
    """Baseline for the next window: every observed row re-baselines to its
    current values (correct for all completeness outcomes), and absent rows
    are retained because disappearance is not deletion (Q9 rule 10). A
    retained row that later reappears is validated by the epoch and
    regression guards like any other pair.
    """
    merged: dict[str, CounterRow] = dict(prev)
    for row in curr:
        merged[row.engine_key] = row
    return merged
