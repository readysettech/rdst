"""Regressions for retained history, bounded admission, and report workload totals."""

import json

import pytest

from features.monitor import snapshot
from features.monitor.api import views
from shared.monitor.monitor_store import EVERYTHING_ELSE, MonitorStore

DAY = 100 * 86400


@pytest.mark.parametrize("days", [1, 7])
def test_middle_tier_totals_include_complete_days(tmp_path, monkeypatch, days):
    monkeypatch.setattr("shared.monitor.monitor_store.FULL_DETAIL_QUERIES", 1)
    monkeypatch.setattr("shared.monitor.monitor_store.SERIES_QUERIES", 2)
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: DAY)
    for day in range(days):
        store.record("db", DAY + day * 86400,
                     [{"query_key": "a", "calls": 10, "total_ms": 100},
                      {"query_key": "b", "calls": 2, "total_ms": 20}], {}, [])
        store.rollup("db", DAY + (day + 1) * 86400 + 300)
    assert store.read("SELECT 1 FROM stmt_series WHERE query_key='b' AND res=3600")
    assert not store.read("SELECT 1 FROM stmt_series WHERE query_key='b' AND res=86400")
    totals = views.stmt_totals(store, "db", DAY, DAY + days * 86400)
    assert totals["b"]["calls"] == 2 * days
    assert totals["a"]["calls"] == 10 * days


def test_full_cap_admits_heavy_entrant_and_folds_displaced_history(tmp_path, monkeypatch):
    monkeypatch.setattr("shared.monitor.monitor_store.FULL_DETAIL_QUERIES", 1)
    monkeypatch.setattr("shared.monitor.monitor_store.SERIES_QUERIES", 2)
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: DAY)
    store.record("db", DAY, [{"query_key": "a", "calls": 1, "total_ms": 100},
                             {"query_key": "b", "calls": 1, "total_ms": 10}],
                 {"a": "SELECT * FROM a", "b": "SELECT * FROM b"}, [])
    store.record("db", DAY + 60, [{"query_key": "c", "calls": 10, "total_ms": 1000000}],
                 {"c": "SELECT * FROM c"}, [])
    assert store.full_detail_keys("db") == {"c"}
    assert {row["query_key"] for row in store.read("SELECT query_key FROM query_load")} == {"a", "c"}
    assert not store.read("SELECT 1 FROM stmt_series WHERE query_key='b'")
    assert views.stmt_totals(store, "db", DAY, DAY + 120)[EVERYTHING_ELSE]["total_ms"] == 10
    assert store.read("SELECT SUM(calls) n,SUM(total_ms) ms FROM stmt_series WHERE res=60") == [
        {"n": 12, "ms": 1000110}]


def test_excluded_candidate_accumulates_bounded_load_across_readings(tmp_path, monkeypatch):
    monkeypatch.setattr("shared.monitor.monitor_store.SERIES_QUERIES", 2)
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: DAY)
    store.record("db", DAY, [{"query_key": "a", "calls": 1, "total_ms": 100},
                             {"query_key": "b", "calls": 1, "total_ms": 10}], {}, [])
    for minute in range(1, 4):
        store = MonitorStore(store.path, clock=lambda: DAY)
        store.record("db", DAY + minute * 60,
                     [{"query_key": "c", "calls": 1, "total_ms": 4}]
                     + [{"query_key": f"tail{index}", "calls": 1, "total_ms": 0.01}
                        for index in range(20)], {}, [])
        counters = store.read("SELECT value FROM monitor_setting WHERE key='candidate_loads'")[0]
        assert len(json.loads(counters["value"])) <= 2
        assert store.read("SELECT COUNT(*) n FROM query_load")[0]["n"] == 2
    assert {row["query_key"] for row in store.read("SELECT query_key FROM query_load")} == {"a", "c"}


@pytest.mark.parametrize("named", [True, False])
def test_snapshot_keeps_aggregate_in_chart_and_share_denominator(tmp_path, monkeypatch, named):
    monkeypatch.setattr("shared.monitor.monitor_store.SERIES_QUERIES", 1 if named else 0)
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: DAY)
    store.record("db", DAY,
                 [{"query_key": "a", "calls": 10, "total_ms": 1000},
                  {"query_key": "b", "calls": 90, "total_ms": 900}],
                 {"a": "SELECT * FROM a", "b": "SELECT * FROM b"}, [],
                 {"calls": 100, "total_ms": 1900})
    day = snapshot.build(store, "db", DAY, DAY + 300, DAY + 300, partial=True)
    chart = day["chart"]
    assert chart is not None
    assert sum(band["calls"] for band in chart["bands"]) == 100
    assert sum(band["share"] for band in chart["bands"]) == pytest.approx(1)
    assert chart["overall_mean_ms"][0] == 19
    assert any(band["kind"] == "other" for band in chart["bands"])
    assert EVERYTHING_ELSE not in day["names"]
    assert all(row["query_key"] != EVERYTHING_ELSE for row in day["queries"])
    if named:
        assert day["queries"][0]["share"] == pytest.approx(1000 / 1900)


def test_report_selection_uses_whole_workload_denominator(tmp_path, monkeypatch):
    import asyncio
    import json

    from features.monitor import report

    monkeypatch.setattr("shared.monitor.monitor_store.SERIES_QUERIES", 1)
    monkeypatch.setenv("RDST_MONITOR_ANALYZE_DAILY_CAP", "0")
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: DAY)
    store.record("db", DAY, [{"query_key": "a", "calls": 10, "total_ms": 1000},
                             {"query_key": "b", "calls": 90, "total_ms": 900}],
                 {"a": "SELECT * FROM a", "b": "SELECT * FROM b"}, [],
                 {"calls": 100, "total_ms": 1900})
    result = asyncio.run(report.run_report(
        "db", "user", store=store, clock=lambda: DAY + 300,
        explain=lambda target, sql: {"plan": ["Seq Scan"]},
        collect=lambda target: ({"section_errors": {}}, {}),
        narrate=lambda context: {"summary": "No wording.", "fix_first": [],
                                 "one_fix_several_queries": [], "health": [],
                                 "changed_since_yesterday": []}))
    stored = json.loads(store.report(result["id"])["report_json"])
    top = stored["computed"]["top_queries"]
    assert [row["query_key"] for row in top] == ["a"]
    assert top[0]["db_time_share"] == pytest.approx(1000 / 1900)


def test_displacement_preserves_totals_with_existing_aggregate_rollups(tmp_path, monkeypatch):
    monkeypatch.setattr("shared.monitor.monitor_store.FULL_DETAIL_QUERIES", 1)
    monkeypatch.setattr("shared.monitor.monitor_store.SERIES_QUERIES", 2)
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: DAY)
    store.record("db", DAY,
                 [{"query_key": key, "calls": 1, "total_ms": ms}
                  for key, ms in [("a", 100), ("b", 10), ("tail", 1)]], {}, [])
    store.rollup("db", DAY + 86400 + 300)
    before = views.stmt_totals(store, "db", DAY, DAY + 86400)
    store.record("db", DAY + 86400 + 3600,
                 [{"query_key": "c", "calls": 1, "total_ms": 1000000}], {}, [])
    after = views.stmt_totals(store, "db", DAY, DAY + 86400)
    assert sum(row["total_ms"] for row in after.values()) == sum(
        row["total_ms"] for row in before.values())


def test_heavy_entrant_at_ten_thousand_cap(tmp_path):
    from shared.monitor.monitor_store import SERIES_QUERIES

    assert SERIES_QUERIES == 10000
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: DAY)
    rows = [{"query_key": f"{index:012x}", "calls": 1, "total_ms": SERIES_QUERIES - index}
            for index in range(SERIES_QUERIES)]
    store.record("db", DAY, rows, {row["query_key"]: "SELECT * FROM orders" for row in rows}, [])
    store.record("db", DAY + 60, [{"query_key": "entrant", "calls": 1, "total_ms": 1000000}],
                 {"entrant": "SELECT * FROM events"}, [],
                 samples=[{"query_key": "entrant", "text": "SELECT * FROM events"}])
    identities = store.read("SELECT query_key FROM query_text UNION SELECT query_key FROM query_load "
                            "UNION SELECT query_key FROM query_sample UNION "
                            "SELECT query_key FROM stuck_sample WHERE query_key IS NOT NULL")
    assert len(identities) == SERIES_QUERIES
    assert "entrant" in store.full_detail_keys("db")
    assert not store.read("SELECT 1 FROM query_load WHERE query_key=?", (rows[-1]["query_key"],))
    assert views.stmt_totals(store, "db", DAY, DAY + 120)[EVERYTHING_ELSE]["total_ms"] == 1
