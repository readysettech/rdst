"""Impact ranking: live calls at a cost per call steadied by the engine's long-run average."""

from features.monitor.api import routes
from features.monitor.ranking import PRIOR_CALLS, blended_ms
from shared.monitor.monitor_store import MonitorStore


def test_a_few_live_calls_lean_on_the_long_run_cost():
    # Three live calls, one of them a slow outlier: 900 ms. The engine's average is 10 ms.
    assert blended_ms(3, 900.0, (100_000, 1_000_000.0)) == 3 * (900 + PRIOR_CALLS * 10) / (3 + PRIOR_CALLS)
    # Many live calls outweigh the long run.
    assert abs(blended_ms(100_000, 5_000_000.0, (10, 100.0)) - 5_000_000.0) < 5_000
    # No history: the live total. Not running: no impact, whatever the history.
    assert blended_ms(4, 80.0, None) == 80.0
    assert blended_ms(0, 0.0, (1_000_000, 9e9)) == 0.0


def test_the_list_ranks_by_impact_with_the_long_run_cost(monkeypatch, tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    store.ensure_open()
    monkeypatch.setattr(routes, "_store", lambda: store)
    monkeypatch.setattr(routes, "_now", lambda: 100_000)

    def seed(conn):
        # "spike" had one slow call in the first minutes; "steady" runs often and costs more
        # per call over the long run. "dead" ran heavily in the past and not at all now.
        for key, calls, total in (("spike", 2, 400.0), ("steady", 40, 300.0), ("dead", 0, 0.0)):
            conn.execute("INSERT INTO query_text VALUES (?,?,?,?,?)",
                         ("db", key, f"SELECT {key} FROM t", 0, 100_000))
            if calls:
                conn.execute("INSERT INTO stmt_series (target,query_key,res,bucket_start,calls,"
                             "total_ms,rows,blks_hit,blks_read) VALUES (?,?,?,?,?,?,?,?,?)",
                             ("db", key, 60, 99_960, calls, total, 0, 0, 0))
    store._write(seed)
    store.record("db", 99_960, [], {}, [], priors={
        "spike": (50_000, 100_000.0), "steady": (80_000, 800_000.0), "dead": (9_000_000, 9e9)})
    page = routes.queries("db", sort="db_time_share", limit=10)
    assert [row["key"] for row in page["queries"]] == ["steady", "spike", "dead"]
