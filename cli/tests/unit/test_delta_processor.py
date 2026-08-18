"""Tests for the epoch-aware counter delta processor (Q9 rules)."""

from __future__ import annotations

import random

from shared.query_registry.delta_processor import (
    ABSENT,
    COMPLETE,
    COUNTER_REGRESSION,
    EPOCH_CHANGED,
    INDETERMINATE,
    REINSERTED,
    CounterRow,
    advance_baseline,
    compute_mysql_epoch_id,
    compute_pg_epoch_id,
    process_snapshot,
)

W0, W1 = 1000.0, 1060.0


def make_row(
    key: str = "q1",
    calls: int = 10,
    rows: int = 100,
    total: float = 500.0,
    minimum: float = 1.0,
    maximum: float = 50.0,
    captured_at: float = W1,
    epoch: str = "epoch-a",
    stats_since: float | None = None,
    first_seen: float | None = None,
    minmax_stats_since: float | None = None,
) -> CounterRow:
    mean = total / calls if calls else 0.0
    return CounterRow(
        engine_key=key,
        calls=calls,
        rows=rows,
        total_exec_time=total,
        mean_exec_time=mean,
        min_exec_time=minimum,
        max_exec_time=maximum,
        captured_at=captured_at,
        epoch_id=epoch,
        stats_since=stats_since,
        first_seen=first_seen,
        minmax_stats_since=minmax_stats_since,
    )


def run_one(prev_row, curr_row, **kwargs):
    results = process_snapshot({prev_row.engine_key: prev_row}, [curr_row], W0, W1, **kwargs)
    assert len(results) == 1
    return results[0]


class TestEpochHelpers:
    def test_pg_epoch_hashes_both_inputs(self):
        with_reset = compute_pg_epoch_id("2026-01-01 00:00:00", "2026-02-01 00:00:00")
        other_reset = compute_pg_epoch_id("2026-01-01 00:00:00", "2026-03-01 00:00:00")
        assert with_reset != other_reset
        assert with_reset == compute_pg_epoch_id("2026-01-01 00:00:00", "2026-02-01 00:00:00")

    def test_pg_epoch_pre_14_uses_start_time_only(self):
        no_reset = compute_pg_epoch_id("2026-01-01 00:00:00")
        assert no_reset == compute_pg_epoch_id("2026-01-01 00:00:00", None)
        assert no_reset != compute_pg_epoch_id("2026-01-02 00:00:00")
        assert no_reset != compute_pg_epoch_id("2026-01-01 00:00:00", "2026-02-01 00:00:00")

    def test_mysql_epoch_is_deterministic(self):
        assert compute_mysql_epoch_id(1750000000) == compute_mysql_epoch_id(1750000000)
        assert compute_mysql_epoch_id(1750000000) != compute_mysql_epoch_id(1750009999)


class TestEpochGuard:
    def test_epoch_change_yields_no_delta_and_full_attribution(self):
        prev = make_row(calls=100, total=5000.0, epoch="epoch-a", captured_at=W0)
        curr = make_row(calls=7, total=30.0, epoch="epoch-b")
        result = run_one(prev, curr)
        assert result.completeness == EPOCH_CHANGED
        assert result.calls_delta is None
        assert result.exec_time_delta is None
        assert result.approximate_qps is None
        assert result.calls_total_attribution == 7
        assert not result.is_baseline

    def test_epoch_change_rebaselines_to_current_row(self):
        prev = make_row(calls=100, epoch="epoch-a", captured_at=W0)
        curr = make_row(calls=7, total=30.0, minimum=3.0, epoch="epoch-b")
        baseline = advance_baseline({prev.engine_key: prev}, [curr])
        assert baseline["q1"] is curr


class TestDefinitiveRebaseline:
    def test_pg17_stats_since_forward_is_reinserted(self):
        # Counters moved plausibly forward, but stats_since proves recreation.
        prev = make_row(calls=5, total=100.0, stats_since=900.0, captured_at=W0)
        curr = make_row(calls=6, total=120.0, stats_since=1010.0)
        result = run_one(prev, curr)
        assert result.completeness == REINSERTED
        assert result.calls_delta is None
        assert result.calls_total_attribution == 6

    def test_mysql_first_seen_forward_is_reinserted(self):
        prev = make_row(calls=5, total=100.0, first_seen=900.0, captured_at=W0)
        curr = make_row(calls=6, total=120.0, first_seen=1010.0)
        result = run_one(prev, curr)
        assert result.completeness == REINSERTED
        assert result.calls_total_attribution == 6

    def test_stable_stats_since_falls_through_to_normal_delta(self):
        prev = make_row(calls=5, total=100.0, rows=50, stats_since=900.0, captured_at=W0)
        curr = make_row(calls=8, total=160.0, rows=80, stats_since=900.0)
        result = run_one(prev, curr)
        assert result.completeness == COMPLETE
        assert result.calls_delta == 3


class TestNegativeDeltaGuard:
    def test_calls_regression_drops_delta_with_pmm_attribution(self):
        prev = make_row(calls=100, total=5000.0, captured_at=W0)
        curr = make_row(calls=3, total=12.0)
        result = run_one(prev, curr)
        assert result.completeness == COUNTER_REGRESSION
        assert result.calls_delta is None
        assert result.exec_time_delta is None
        # PMM: attribute the full current counter so lifetime counts advance.
        assert result.calls_total_attribution == 3

    def test_exec_time_regression_alone_drops_whole_row(self):
        prev = make_row(calls=100, total=5000.0, captured_at=W0)
        curr = make_row(calls=105, total=4000.0)
        result = run_one(prev, curr)
        assert result.completeness == COUNTER_REGRESSION
        assert result.calls_delta is None

    def test_rows_regression_alone_drops_whole_row(self):
        prev = make_row(calls=100, rows=1000, total=5000.0, captured_at=W0)
        curr = make_row(calls=105, rows=900, total=5200.0)
        result = run_one(prev, curr)
        assert result.completeness == COUNTER_REGRESSION


class TestEvictReinsertHeuristic:
    def test_same_calls_higher_total_time_is_reinserted(self):
        # Datadog's documented case: re-inserted with same call count
        # (usually 1) and slight duration change.
        prev = make_row(calls=1, rows=1, total=10.0, minimum=10.0, maximum=10.0, captured_at=W0)
        curr = make_row(calls=1, rows=1, total=10.4, minimum=10.4, maximum=10.4)
        result = run_one(prev, curr)
        assert result.completeness == REINSERTED
        assert result.calls_delta is None
        assert result.calls_total_attribution == 1

    def test_min_exec_time_rising_is_reinserted(self):
        # A surviving entry's min can only fall; a rise means recreation.
        prev = make_row(calls=1, rows=1, total=10.0, minimum=2.0, captured_at=W0)
        curr = make_row(calls=2, rows=2, total=25.0, minimum=5.0)
        result = run_one(prev, curr)
        assert result.completeness == REINSERTED

    def test_ordinary_growth_is_not_flagged(self):
        prev = make_row(calls=10, rows=100, total=500.0, minimum=1.0, captured_at=W0)
        curr = make_row(calls=13, rows=130, total=650.0, minimum=0.8)
        result = run_one(prev, curr)
        assert result.completeness == COMPLETE
        assert result.calls_delta == 3

    def test_idle_row_with_stable_counters_is_not_flagged(self):
        prev = make_row(calls=10, rows=100, total=500.0, minimum=1.0, captured_at=W0)
        curr = make_row(calls=10, rows=100, total=500.0, minimum=1.0)
        result = run_one(prev, curr)
        assert result.completeness == COMPLETE
        assert result.calls_delta == 0

    def test_pg17_minmax_only_reset_keeps_counter_window(self):
        # pg_stat_statements_reset(minmax_only => true): minmax_stats_since
        # advances while stats_since holds still, and min can rise. The
        # counter window stays valid (Q9 rule 9: min/max window is separate).
        prev = make_row(calls=10, rows=100, total=500.0, minimum=2.0,
                        stats_since=900.0, minmax_stats_since=900.0, captured_at=W0)
        curr = make_row(calls=14, rows=140, total=580.0, minimum=15.0,
                        stats_since=900.0, minmax_stats_since=1020.0)
        result = run_one(prev, curr)
        assert result.completeness == COMPLETE
        assert result.calls_delta == 4
        assert result.exec_time_delta == 80.0
        assert result.calls_total_attribution == 4

    def test_min_rise_with_unchanged_minmax_stats_since_is_reinserted(self):
        prev = make_row(calls=1, rows=1, total=10.0, minimum=2.0,
                        stats_since=900.0, minmax_stats_since=900.0, captured_at=W0)
        curr = make_row(calls=2, rows=2, total=25.0, minimum=5.0,
                        stats_since=900.0, minmax_stats_since=900.0)
        result = run_one(prev, curr)
        assert result.completeness == REINSERTED

    def test_min_rise_without_minmax_stats_since_is_reinserted(self):
        # Pre-17 rows carry no minmax_stats_since; the min indicator stays on.
        prev = make_row(calls=1, rows=1, total=10.0, minimum=2.0,
                        minmax_stats_since=None, captured_at=W0)
        curr = make_row(calls=2, rows=2, total=25.0, minimum=5.0,
                        minmax_stats_since=None)
        result = run_one(prev, curr)
        assert result.completeness == REINSERTED


class TestAbsentRows:
    def test_absent_row_reported_and_baseline_retained(self):
        prev_row = make_row(key="gone", calls=50, captured_at=W0)
        prev = {"gone": prev_row}
        results = process_snapshot(prev, [], W0, W1)
        assert len(results) == 1
        result = results[0]
        assert result.engine_key == "gone"
        assert result.completeness == ABSENT
        assert result.calls_delta is None
        assert result.calls_total_attribution == 0
        baseline = advance_baseline(prev, [])
        assert baseline["gone"] is prev_row

    def test_retained_absent_row_validates_on_reappearance(self):
        prev_row = make_row(key="gone", calls=50, total=500.0, rows=100, captured_at=W0)
        baseline = advance_baseline({"gone": prev_row}, [])
        # Reappears with continued growth: valid delta against the retained
        # row, over the row's own window (the growth accumulated since the
        # retained capture, not since the last global window).
        back = make_row(key="gone", calls=55, total=560.0, rows=110, captured_at=W1 + 60)
        results = process_snapshot(baseline, [back], W1, W1 + 60)
        result = results[0]
        assert result.completeness == COMPLETE
        assert result.calls_delta == 5
        assert result.window_start == W0
        assert result.window_end == W1 + 60
        assert result.approximate_qps == 5 / (W1 + 60 - W0)


class TestZeroDelta:
    def test_zero_delta_defaults_to_complete(self):
        # A genuinely idle query is normal; blanket 'indeterminate' would
        # poison every quiet window.
        prev = make_row(calls=10, total=500.0, captured_at=W0)
        curr = make_row(calls=10, total=500.0)
        result = run_one(prev, curr)
        assert result.completeness == COMPLETE
        assert result.calls_delta == 0
        assert result.exec_time_delta == 0.0
        assert result.approximate_qps == 0.0
        assert result.calls_total_attribution == 0
        assert not result.is_baseline

    def test_zero_delta_with_reset_suspected_is_indeterminate(self):
        prev = make_row(calls=10, total=500.0, captured_at=W0)
        curr = make_row(calls=10, total=500.0)
        result = run_one(prev, curr, reset_suspected=True)
        assert result.completeness == INDETERMINATE
        assert result.calls_delta is None
        assert result.calls_total_attribution == 0

    def test_reset_suspected_does_not_touch_positive_deltas(self):
        prev = make_row(calls=10, total=500.0, rows=100, captured_at=W0)
        curr = make_row(calls=16, total=620.0, rows=160)
        result = run_one(prev, curr, reset_suspected=True)
        assert result.completeness == COMPLETE
        assert result.calls_delta == 6


class TestNormalDelta:
    def test_delta_math_and_qps(self):
        prev = make_row(calls=10, total=500.0, rows=100, captured_at=W0)
        curr = make_row(calls=130, total=740.0, rows=220)
        result = run_one(prev, curr)
        assert result.completeness == COMPLETE
        assert result.calls_delta == 120
        assert result.exec_time_delta == 240.0
        assert result.approximate_qps == 120 / 60.0
        assert result.calls_total_attribution == 120
        assert result.window_start == W0
        assert result.window_end == W1

    def test_window_is_per_row_from_captured_at(self):
        # The window arguments are fallbacks; each result's window comes
        # from the rows' own capture times.
        prev = make_row(calls=10, total=500.0, rows=100, captured_at=W0)
        curr = make_row(calls=130, total=740.0, rows=220, captured_at=W1)
        results = process_snapshot({"q1": prev}, [curr], W0 - 30, W1 + 30)
        result = results[0]
        assert result.window_start == W0
        assert result.window_end == W1
        assert result.approximate_qps == 120 / 60.0

    def test_qps_clamps_degenerate_window(self):
        prev = make_row(calls=10, total=500.0, captured_at=W0)
        curr = make_row(calls=15, total=550.0, captured_at=W0 + 0.25)
        results = process_snapshot({"q1": prev}, [curr], W0, W0 + 0.25)
        assert results[0].approximate_qps == 5.0


class TestBaseline:
    def test_new_row_is_a_baseline_not_a_window(self):
        curr = make_row(calls=42, total=100.0)
        results = process_snapshot({}, [curr], W0, W1)
        assert len(results) == 1
        result = results[0]
        assert result.is_baseline
        assert result.completeness == COMPLETE
        assert result.calls_delta is None
        assert result.approximate_qps is None
        assert result.calls_total_attribution == 42

    def test_baseline_is_distinguishable_from_zero_traffic_window(self):
        baseline_row = make_row(calls=42, total=100.0)
        baseline = process_snapshot({}, [baseline_row], W0, W1)[0]
        idle = run_one(make_row(calls=42, total=100.0, captured_at=W0),
                       make_row(calls=42, total=100.0))
        assert baseline.is_baseline and baseline.calls_delta is None
        assert not idle.is_baseline and idle.calls_delta == 0


class TestAttributionInvariant:
    def test_random_monotonic_sequences_conserve_call_growth(self):
        # Property: within one epoch, the sum of calls_delta over all
        # windows equals total counter growth, and summed attribution
        # initialized by the baseline equals the final lifetime counter.
        rng = random.Random(20260816)
        for _ in range(50):
            calls, total, rows, minimum = 1, 5.0, 1, 5.0
            snapshots = []
            for step in range(rng.randint(2, 12)):
                grew = rng.random() < 0.8
                if grew:
                    delta = rng.randint(1, 20)
                    calls += delta
                    total += delta * rng.uniform(0.5, 10.0)
                    rows += delta * rng.randint(0, 5)
                    if rng.random() < 0.3:
                        minimum *= rng.uniform(0.5, 1.0)
                snapshots.append(
                    make_row(calls=calls, total=total, rows=rows,
                             minimum=minimum, captured_at=W0 + 60 * step)
                )

            baseline: dict[str, CounterRow] = {}
            deltas_sum = 0
            attribution_sum = 0
            start = W0 - 60
            for snap in snapshots:
                results = process_snapshot(baseline, [snap], start, snap.captured_at)
                assert len(results) == 1
                result = results[0]
                if result.is_baseline:
                    assert result.calls_delta is None
                else:
                    assert result.completeness == COMPLETE
                    deltas_sum += result.calls_delta
                attribution_sum += result.calls_total_attribution
                baseline = advance_baseline(baseline, [snap])
                start = snap.captured_at

            assert deltas_sum == snapshots[-1].calls - snapshots[0].calls
            assert attribution_sum == snapshots[-1].calls
