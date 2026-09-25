"""Randomized attention selection and local judge gating."""

import json
import random

import pytest

from features.monitor.attention import baselines, shortlist
from features.monitor.judge import AttentionJudge
from shared.monitor.monitor_store import MonitorStore


def _row(key, calls, mean):
    return {"query_key": key, "calls": calls, "total_ms": calls * mean}


@pytest.mark.parametrize("seed", range(200))
def test_baseline_shortlist_invariants(seed):
    rng = random.Random(seed)
    count = rng.randrange(2, 40)
    old = [_row(f"q{i}", rng.randrange(100, 1000), rng.randrange(1, 100))
           for i in range(count)]
    heaviest = max(old, key=lambda row: row["total_ms"])["query_key"]
    current = [_row(row["query_key"], row["calls"],
                    row["total_ms"] // row["calls"] *
                    (3 if row["query_key"] == heaviest else 1)) for row in old]
    history = {name: [_row(row["query_key"], row["calls"] * (seconds // 300),
                           row["total_ms"] // row["calls"]) for row in old]
               for name, seconds in (("1h", 3600), ("1d", 86400),
                                     ("7d", 7 * 86400))}
    rows = baselines(current, history)
    selected = shortlist(rows)
    assert len(selected) <= 15
    assert selected == shortlist(baselines(current, history))
    assert heaviest in {row["query_key"] for row in selected}
    assert shortlist(baselines(old, history)) == []
    sparse = {name: rng.sample(history[name], rng.randrange(len(old) + 1))
              for name in history}
    assert len(shortlist(baselines(current, sparse))) <= 15


class _Jev:
    def __init__(self):
        self.calls = 0

    def attention(self, **request):
        self.calls += 1
        return {}


@pytest.mark.parametrize("seed", range(8))
def test_judge_runs_each_cycle_with_work_and_stops_at_the_daily_cap(seed, tmp_path, monkeypatch):
    rng = random.Random(seed)
    cap = rng.randrange(1, 5)
    monkeypatch.setenv("RDST_MONITOR_JEV_DAILY_CAP", str(cap))
    monkeypatch.setenv("RDST_MONITOR_JUDGE_DRY_RUN", "1")
    now = [10 * 86400]
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: now[0])
    client = _Jev()
    judge = AttentionJudge(store, client=client, clock=lambda: now[0])
    for step in range(12):
        now[0] += 60
        chosen = ([] if rng.randrange(3) == 0 else
                  [{"query_key": f"q{step}", "reason": "mover",
                    "mean_ms": 10.0, "qps": 1.0, "db_time_share": 0.5,
                    "ratios": {name: {metric: 1.5 for metric in
                                     ("mean_ms", "qps", "db_time_share")}
                               for name in ("1h", "1d", "7d")}}])
        if chosen:
            store.record("db", now[0], [{"query_key": chosen[0]["query_key"], "calls": 1, "total_ms": 10}],
                         {chosen[0]["query_key"]: "SELECT * FROM orders"}, [])
        before = client.calls
        judge(chosen, {"target": "db", "inbox": []})
        # An empty cycle costs nothing; any shortlist gets a call until the cap.
        expected = before + 1 if chosen and before < cap else before
        assert client.calls == expected
    assert store.read("SELECT COUNT(*) n FROM judge_run WHERE request_json IS NOT NULL")[0]["n"] <= cap
