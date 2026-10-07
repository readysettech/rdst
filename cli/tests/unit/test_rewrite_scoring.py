from features.analyze.functions.rewrite_testing import _calculate_improvement


def test_rows_examined_cannot_outvote_a_measured_speedup():
    # An index plan counts rows per loop: the rewrite examines far more rows on paper while
    # running twice as fast.
    improvement = _calculate_improvement(
        {"execution_time_ms": 14.6, "cost_estimate": 9331.6, "rows_examined": 3967},
        {"execution_time_ms": 6.5, "cost_estimate": 7100.1, "rows_examined": 76546})
    assert improvement["execution_time"]["improvement_pct"] > 50
    assert improvement["overall"]["is_better"] and improvement["overall"]["improvement_pct"] >= 20


def test_a_slower_rewrite_stays_worse():
    improvement = _calculate_improvement(
        {"execution_time_ms": 10, "cost_estimate": 100, "rows_examined": 100},
        {"execution_time_ms": 40, "cost_estimate": 400, "rows_examined": 400})
    assert not improvement["overall"]["is_better"]
