"""Seeded checks of the monitor time series and API arithmetic."""

import random
from types import SimpleNamespace

import pytest

from features.monitor.api import routes
from features.query_registry.discovery import QueryDiscoveryCollector
from shared.monitor.monitor_store import MonitorStore, RETENTION
from shared.query_registry.delta_processor import COMPLETE, COUNTER_REGRESSION, DeltaResult


@pytest.mark.parametrize("seed", range(8))
def test_rollups_conserve_closed_buckets_and_retention(seed, tmp_path):
    rng = random.Random(seed)
    now = 10 * 86400
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: now)
    source = {}
    # Missing minutes are gaps; a new positive delta after a reset belongs to
    # the first minute after the reset, just as the collector sink supplies it.
    for minute in range(28):
        if rng.randrange(5) == 0:
            continue
        ts = now + minute * 60
        calls = rng.randrange(0, 20)
        total_ms = calls * rng.randrange(1, 100)
        source[ts] = (calls, total_ms)
    # Shuffle writes inside each still-open 5m bucket, and sometimes split a
    # minute into two writes. Moving to the next bucket closes the prior one.
    for bucket in sorted({ts // 300 for ts in source}):
        minutes = [ts for ts in source if ts // 300 == bucket]
        rng.shuffle(minutes)
        for ts in minutes:
            calls, total_ms = source[ts]
            split = rng.randrange(calls + 1)
            first_ms = total_ms * split // calls if calls else 0
            parts = ((split, first_ms), (calls - split, total_ms - first_ms))
            if rng.randrange(2) == 0:
                parts = ((calls, total_ms),)
            for part_calls, part_ms in parts:
                store.record("db", ts, [{"query_key": "q", "calls": part_calls,
                                         "total_ms": part_ms}], {}, [],
                             {"calls": part_calls, "total_ms": part_ms})
    end = now + 2 * 86400
    store.rollup("db", end)
    store.rollup("db", end)
    for res in (300, 3600, 86400):
        for table in ("stmt_series", "db_series"):
            rows = store.read(f"SELECT bucket_start,calls,total_ms FROM {table} "
                              "WHERE target='db' AND res=?", (res,))
            for row in rows:
                included = [value for ts, value in source.items()
                            if row["bucket_start"] <= ts < row["bucket_start"] + res]
                assert (row["calls"], row["total_ms"]) == (
                    sum(value[0] for value in included),
                    sum(value[1] for value in included))
            assert len({row["bucket_start"] for row in rows}) == len(rows)
    assert store.read("SELECT COUNT(*) n FROM stmt_series WHERE res=86400")[0]["n"] >= 1

    # Keep rows at the retention boundary and purge rows older than it.
    for res, age in RETENTION.items():
        cutoff = (end - age) // res * res
        for key, ts in (("old", cutoff - res), ("boundary", cutoff),
                        ("young", cutoff + rng.randrange(1, 4) * res)):
            store._write(lambda conn, res=res, key=key, ts=ts: conn.execute(
                "INSERT OR REPLACE INTO stmt_series (target,query_key,res,bucket_start,calls,total_ms,rows,blks_hit,blks_read) VALUES (?,?,?,?,?,?,?,?,?)",
                ("db", key, res, ts, 1, 1.0, 0, 0, 0)))
    daily_before = store.read("SELECT COUNT(*) n FROM stmt_series WHERE res=86400")[0]["n"]
    store.rollup("db", end)
    for res, age in RETENTION.items():
        cutoff = (end - age) // res * res
        assert store.read("SELECT bucket_start FROM stmt_series WHERE query_key='boundary' "
                          "AND res=?", (res,)) == [{"bucket_start": cutoff}]
        assert store.read("SELECT bucket_start FROM stmt_series WHERE query_key='young' "
                          "AND res=?", (res,))
        assert store.read("SELECT bucket_start FROM stmt_series WHERE query_key='old' "
                          "AND res=?", (res,)) == []
    assert store.read("SELECT COUNT(*) n FROM stmt_series WHERE res=86400")[0]["n"] == daily_before - 1


@pytest.mark.parametrize("seed", range(8))
def test_api_rates_and_shares(seed, tmp_path, monkeypatch):
    rng = random.Random(seed)
    now = 20 * 86400
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: now)
    expected = {}
    for i in range(rng.randrange(1, 12)):
        calls = rng.randrange(0, 30)
        total_ms = calls * rng.randrange(1, 500)
        key = f"q{i}"
        expected[key] = (calls, total_ms)
        store.record("db", now - 60, [{"query_key": key, "calls": calls,
                                        "total_ms": total_ms}], {}, [], None)
    monkeypatch.setattr(routes, "_store", lambda: store)
    monkeypatch.setattr(routes, "_now", lambda: now)
    rows = routes.queries("db")["queries"]
    assert {row["key"] for row in rows} == set(expected)
    for row in rows:
        calls, total_ms = expected[row["key"]]
        # One minute recorded: the rate covers that minute, not the whole day.
        assert row["qps"] == pytest.approx(calls / 60)
        assert row["mean_ms"] == pytest.approx(total_ms / calls if calls else 0)
    assert sum(row["db_time_share"] for row in rows) <= 1 + 1e-12
    assert sum(row["db_time_share"] for row in rows) == pytest.approx(
        1 if sum(ms for _, ms in expected.values()) else 0)


def test_late_closed_minute_reaches_5m_rollup(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    store.record("db", 0, [{"query_key": "q", "calls": 1, "total_ms": 1}], {}, [])
    store.rollup("db", 300)
    store.record("db", 60, [{"query_key": "q", "calls": 1, "total_ms": 1}], {}, [])
    store.rollup("db", 300)
    assert store.read("SELECT calls,total_ms FROM stmt_series WHERE res=300") == [
        {"calls": 2, "total_ms": 2.0}]


def test_late_minute_cascades_through_closed_rollups(tmp_path):
    path = tmp_path / "monitor.db"
    store = MonitorStore(path)
    db = {"calls": 1, "total_ms": 2, "blks_hit": 3, "blks_read": 1}
    store.record("db", 0, [{"query_key": "q", "calls": 1, "total_ms": 2}],
                 {}, [], db)
    store.rollup("db", 86400)
    store.record("db", 60, [{"query_key": "q", "calls": 2, "total_ms": 4}],
                 {}, [], {**db, "calls": 2, "total_ms": 4})
    store = MonitorStore(path)
    for _ in range(2):
        store.rollup("db", 86400)
        for res in (300, 3600, 86400):
            assert store.read("SELECT calls,total_ms FROM stmt_series WHERE res=?",
                              (res,)) == [{"calls": 3, "total_ms": 6.0}]
            assert store.read("SELECT calls,total_ms,cache_hit_ratio FROM db_series "
                              "WHERE res=?", (res,)) == [
                                  {"calls": 3, "total_ms": 6.0,
                                   "cache_hit_ratio": 0.75}]


@pytest.mark.parametrize("seed", range(8))
def test_gaps_and_counter_resets_pass_through_sink(seed, tmp_path):
    rng = random.Random(seed)
    store = MonitorStore(tmp_path / "monitor.db")
    collector = QueryDiscoveryCollector("db", two_phase=True)
    collector._monitor = store
    collector._alias_map = {"engine": "q"}
    collector._baseline = {}
    registry = SimpleNamespace(get_query=lambda key: None)
    expected = {}
    ts = 10 * 86400
    collector._record_monitor_cycle(ts, [], registry, [], baseline=True)
    for _ in range(15):
        interval = rng.choice((60, 60, 120, 180))
        ts += interval
        reset = rng.randrange(5) == 0
        calls = rng.randrange(1, 20)
        total_ms = calls * rng.randrange(1, 100)
        delta = DeltaResult("engine", ts - 60, ts,
                            COUNTER_REGRESSION if reset else COMPLETE,
                            None if reset else calls,
                            None if reset else float(total_ms),
                            None if reset else calls / 60, calls)
        collector._record_monitor_cycle(ts, [delta], registry, [])
        if not reset and interval == 60:
            expected[ts // 60 * 60] = (calls, float(total_ms))
    store.rollup("db", ts + 86400)
    rows = store.read("SELECT bucket_start,calls,total_ms FROM stmt_series "
                      "WHERE res=60 ORDER BY bucket_start")
    assert rows == [{"bucket_start": minute, "calls": calls, "total_ms": ms}
                    for minute, (calls, ms) in sorted(expected.items())]
    for res in (300, 3600, 86400):
        for row in store.read("SELECT bucket_start,calls,total_ms FROM stmt_series "
                              "WHERE res=?", (res,)):
            values = [value for minute, value in expected.items()
                      if row["bucket_start"] <= minute < row["bucket_start"] + res]
            assert (row["calls"], row["total_ms"]) == (
                sum(value[0] for value in values), sum(value[1] for value in values))
