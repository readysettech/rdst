"""Load tiers bound collection and AI while preserving searchable identities."""

import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from features.monitor.api import routes, views
from features.monitor.analyze_queue import _enqueue
from features.monitor.judge import _request
from shared.monitor.monitor_store import EVERYTHING_ELSE, MonitorStore
from shared.query_registry.query_registry import QueryRegistry

NOW = 100 * 86400


def rows(count):
    return [{"query_key": f"{index:012x}", "calls": 1, "total_ms": float(count - index)}
            for index in range(count)]


def test_collection_at_100000_digests_is_batched_and_bounded(tmp_path, monkeypatch):
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: NOW)
    statements = rows(100000)
    batch_sizes = []
    original = store._connect

    class Connection:
        def __init__(self, conn):
            self.conn = conn

        def executemany(self, sql, params):
            values = list(params)
            batch_sizes.append(len(values))
            return self.conn.executemany(sql, values)

        def __getattr__(self, attr):
            return getattr(self.conn, attr)

    store.ensure_open()
    monkeypatch.setattr(store, "_connect", lambda: Connection(original()))
    started = time.perf_counter()
    store.record("db", NOW, statements, {}, [],
                 {"calls": 100000, "total_ms": sum(row["total_ms"] for row in statements)})
    assert time.perf_counter() - started < 15
    assert max(batch_sizes) <= 10001
    assert store.read("SELECT COUNT(*) n FROM query_load")[0]["n"] == 10000
    assert store.read("SELECT COUNT(*) n FROM query_tier")[0]["n"] == 10000
    assert store.read("SELECT COUNT(*) n FROM stmt_series WHERE res=60")[0]["n"] == 10001
    other = store.read("SELECT calls,total_ms FROM stmt_series WHERE query_key=?", (EVERYTHING_ELSE,))[0]
    assert other["calls"] == 90000
    assert other["total_ms"] == sum(row["total_ms"] for row in statements[10000:])
    store.rollup("db", NOW + 300)
    assert store.read("SELECT COUNT(*) n FROM stmt_series WHERE res=60")[0]["n"] == 1001
    assert store.read("SELECT COUNT(*) n FROM stmt_series WHERE res=300")[0]["n"] == 10001


def test_tier_transitions_keep_old_history_and_change_future_detail(tmp_path, monkeypatch):
    monkeypatch.setattr("shared.monitor.monitor_store.FULL_DETAIL_QUERIES", 1)
    monkeypatch.setattr("shared.monitor.monitor_store.SERIES_QUERIES", 2)
    clock = [NOW]
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: clock[0])
    statements = [{"query_key": key, "calls": 1, "total_ms": load}
                  for key, load in [("a", 100), ("b", 1), ("c", 10)]]
    texts = {key: f"SELECT * FROM {key}" for key in ("a", "b", "c")}
    store.record("db", NOW, statements, texts, [])
    store.rollup("db", NOW + 300)
    assert store.full_detail_keys("db") == {"a"}
    assert not store.read("SELECT 1 FROM stmt_series WHERE query_key='b'")
    assert not store.read("SELECT 1 FROM stmt_series WHERE query_key='c' AND res=60")
    clock[0] += 3600
    store._write(lambda conn: conn.execute("UPDATE query_load SET total_ms=1000,updated_at=? "
                                          "WHERE query_key='c'", (clock[0],)))
    store.rank_queries("db")
    assert store.full_detail_keys("db") == {"c"}
    store.record("db", clock[0], statements, {}, [])
    store.rollup("db", clock[0] + 3600)
    assert store.read("SELECT 1 FROM stmt_series WHERE query_key='a' AND res=60 AND bucket_start=?", (NOW,))
    assert store.read("SELECT 1 FROM stmt_series WHERE query_key='c' AND res=60")
    assert not store.read("SELECT 1 FROM stmt_series WHERE query_key='b' AND bucket_start>=?", (clock[0],))
    clock[0] += 3 * 86400
    store.rollup("db", clock[0])
    store.maintain(pause_s=0)
    assert store.read("SELECT 1 FROM stmt_series WHERE query_key='a' AND res=300 AND bucket_start=?", (NOW,))
    assert not store.read("SELECT 1 FROM stmt_series WHERE query_key='b' AND res=300")
    assert not store.read("SELECT 1 FROM stmt_series WHERE query_key='b' AND res=3600")
    assert not store.read("SELECT 1 FROM stmt_series WHERE query_key='b' AND res=86400")
    clock[0] += 30 * 86400
    store.maintain(pause_s=0)
    assert not store.read("SELECT 1 FROM stmt_series WHERE query_key='b' AND res=3600")
    assert store.read("SELECT 1 FROM stmt_series WHERE query_key='a' AND res=3600")


def test_judge_and_analysis_use_only_top_thousand(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: NOW)
    statements = rows(1001)
    store.record("db", NOW, statements,
                 {row["query_key"]: "SELECT * FROM orders" for row in statements}, [])
    top, tail = statements[0]["query_key"], statements[-1]["query_key"]
    chosen = [{"query_key": key, "db_time_share": 0.1, "baseline": {},
               "ratios": {}, "reason": "new_heavy"} for key in (top, tail)]
    item = store.upsert_item("db", "latency_regression", tail, 1, "Slow query", {}, NOW)
    request = _request(store, "db", NOW, chosen, {"inbox": [item]})
    assert [row["query_key"] for row in request["queries"]] == [top]
    assert not request["open_items"]
    assert _enqueue("db", tail, "new", store=store, now=NOW) is None
    assert _enqueue("db", tail, "report", store=store, now=NOW) is None
    user = _enqueue("db", tail, "user", store=store, now=NOW)
    assert user and user["reason"] == "user"
    assert store.claim_analysis("db", NOW, eligible_keys=[top])["id"] == user["id"]


def test_classification_claim_rejects_tail_and_admits_entrant(tmp_path):
    registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
    a, _ = registry.add_query("SELECT * FROM a", target="db")
    b, _ = registry.add_query("SELECT * FROM b", target="db")
    store = registry.library_store
    store.prepare_assessments("db", "identity", now=NOW)
    claim = store.claim_assessment("db", "identity", owner="worker", request_id="a",
                                   eligible_keys=[a], now=NOW)
    assert claim["hash"] == a
    assert store.claim_assessment("db", "identity", owner="worker", request_id="none",
                                  eligible_keys=[a], now=NOW) is None
    entrant = store.claim_assessment("db", "identity", owner="worker", request_id="b",
                                     eligible_keys=[b], now=NOW)
    assert entrant["hash"] == b


def test_api_pages_20000_queries_before_enriching(tmp_path, monkeypatch):
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: NOW)
    statements = rows(20000)
    texts = {row["query_key"]: f"SELECT * FROM table_{index}"
             for index, row in enumerate(statements)}
    # Long SQL on the first page exercises bounded enrichment at modest fixture size.
    for row in statements[:20]:
        texts[row["query_key"]] += f" /* {'x' * 8000} */"
    store.record("db", NOW - 60, statements, texts, [],
                 {"calls": 20000, "total_ms": sum(row["total_ms"] for row in statements)},
                 workload_checked=True)
    calls = {name: [] for name in ("texts", "registry", "assessments")}

    def record_keys(name, keys):
        assert keys is not None and len(keys) <= 20
        calls[name].append(list(keys))

    def registry(keys=None):
        record_keys("registry", keys)
        return {}

    def assessments(target, keys=None):
        record_keys("assessments", keys)
        return {}

    monkeypatch.setattr(views, "registry_entries", registry)
    monkeypatch.setattr(views, "assessments", assessments)
    original = views.query_texts
    enriched = []

    def texts(store, target, keys=None):
        record_keys("texts", keys)
        enriched.extend(keys)
        return original(store, target, keys)

    monkeypatch.setattr(views, "query_texts", texts)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/monitor")
    monkeypatch.setattr(routes, "_store", lambda: store)
    monkeypatch.setattr(routes, "_now", lambda: NOW)
    client = TestClient(app)
    for offset, search in [(0, ""), (5000, ""), (0, "table_9999")]:
        for batches in calls.values():
            batches.clear()
        response = client.get("/api/monitor/targets/db/queries", params={
            "range": "1h", "sort": "mean_ms", "q": search, "limit": 20, "offset": offset})
        assert response.status_code == 200
        page = response.json()
        assert page["total"] == (1 if search else 10000)
        assert len(page["queries"]) == (1 if search else 20)
        assert page["offset"] == offset
        page_keys = [query["key"] for query in page["queries"]]
        for batches in calls.values():
            assert batches == [page_keys]
        if offset == 0 and not search:
            assert page["queries"][0]["db_time_share"] == 20000 / sum(row["total_ms"] for row in statements)
    assert len(enriched) == 41
    series = views.series(store, "db", f"{19999:012x}", "1h", NOW)
    assert series["detail_note"] == "Detail is kept for the top 1,000 queries by load"
    assert all(point["calls"] is None for point in series["points"])


def test_tail_late_readings_preserve_rollup_counts_with_incident_pins(tmp_path, monkeypatch):
    monkeypatch.setattr("shared.monitor.monitor_store.FULL_DETAIL_QUERIES", 1)
    monkeypatch.setattr("shared.monitor.monitor_store.SERIES_QUERIES", 2)
    for pinned in (False, True):
        store = MonitorStore(tmp_path / f"monitor-{pinned}.db", clock=lambda: NOW)
        top, tail = "000000000001", "000000000002"
        store.record("db", NOW, [
            {"query_key": top, "calls": 1, "total_ms": 100},
            {"query_key": tail, "calls": 1, "total_ms": 10}], {}, [])
        if pinned:
            store.upsert_item("db", "stuck_query", tail, 1, "Stuck query", {"query_key": tail}, NOW)
        store.rollup("db", NOW + 300)
        store.record("db", NOW + 120, [{"query_key": tail, "calls": 2, "total_ms": 20}], {}, [])
        store.rollup("db", NOW + 600)
        assert store.read("SELECT calls FROM stmt_series WHERE query_key=? AND res=300", (tail,)) == [{"calls": 3}]
        if pinned:
            assert len(store.read("SELECT 1 FROM stmt_series WHERE query_key=? AND res=60", (tail,))) == 2
        else:
            assert not store.read("SELECT 1 FROM stmt_series WHERE query_key=? AND res=60", (tail,))


def test_sql_paging_sorts_scores_and_filters_classification_globally(tmp_path, monkeypatch):
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: NOW)
    store.record("db", NOW - 60, [], {}, [], {"calls": 20000, "total_ms": 20000})
    store._write(lambda conn: conn.executemany("INSERT INTO query_text VALUES (?,?,?,?,?)",
        (("db", f"{index:012x}", f"SELECT * FROM table_{index}", NOW - 60, NOW)
         for index in range(20000))))
    last = f"{19999:012x}"
    store.update_query_ui("db", last, summary={"efficiency_score": 8, "fix_ready": True}, findings=2)
    monkeypatch.setattr(views, "registry_entries", lambda keys=None: {})
    monkeypatch.setattr(views, "assessments", lambda target, keys=None: {})
    scored = views.queries(store, "db", "1h", NOW, sort="score_low", filter="all", q="", limit=10, offset=0)
    assert scored["queries"][0]["key"] == last
    filtered = views.queries(store, "db", "1h", NOW, sort="db_time_share", filter="index_coverage",
                             q="", limit=10, offset=0)
    assert filtered["total"] == 1 and filtered["queries"][0]["key"] == last


def test_observation_cache_batches_middle_tier_and_skips_tail(tmp_path, monkeypatch):
    from features.query_registry.discovery import QueryDiscoveryCollector
    from shared.query_registry.delta_processor import COMPLETE, DeltaResult
    from shared.query_registry.observation_store import ObservationStore

    monkeypatch.setattr("shared.monitor.monitor_store.FULL_DETAIL_QUERIES", 1)
    monkeypatch.setattr("shared.monitor.monitor_store.SERIES_QUERIES", 2)
    monkeypatch.setattr("features.query_registry.discovery.FULL_DETAIL_QUERIES", 1)
    monitor = MonitorStore(tmp_path / "monitor.db", clock=lambda: NOW)
    monitor.record("db", NOW, rows(3), {}, [])
    collector = QueryDiscoveryCollector("db", two_phase=True)
    collector._monitor = monitor
    collector._monitor_enabled = True
    collector._alias_map = {str(index): f"{index:012x}" for index in range(3)}
    cache = ObservationStore(tmp_path / "cache.db")
    try:
        for minute in range(5):
            start, end = NOW + minute * 60, NOW + (minute + 1) * 60
            deltas = [DeltaResult(str(index), start, end, COMPLETE, 1, 10, 1 / 60, 1)
                      for index in range(3)]
            observations = collector._recent_observation_rows(deltas, incremental=False)
            assert len(observations) == (2 if minute == 4 else 1)
            cache.record_recent_observations("db", observations)
        with cache._read() as conn:
            saved = [dict(row) for row in conn.execute(
                "SELECT normalized_hash,window_end-window_start seconds,calls_delta "
                "FROM recent_observation ORDER BY normalized_hash,window_end")]
        assert len(saved) == 6
        assert saved[-1] == {"normalized_hash": "000000000001", "seconds": 300.0, "calls_delta": 5}
        assert all(row["normalized_hash"] != "000000000002" for row in saved)
    finally:
        cache.close()


def test_rank_changes_within_one_second_keep_the_current_tier_period(tmp_path, monkeypatch):
    monkeypatch.setattr("shared.monitor.monitor_store.FULL_DETAIL_QUERIES", 1)
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: NOW)
    store.record("db", NOW, [{"query_key": "a", "calls": 1, "total_ms": 100},
                              {"query_key": "b", "calls": 1, "total_ms": 10}], {}, [])
    store._write(lambda conn: conn.execute("UPDATE query_load SET total_ms=1000 WHERE query_key='b'"))
    store.rank_queries("db", NOW)
    assert store.full_detail_keys("db") == {"b"}
    assert store.read("SELECT query_key,tier FROM query_tier_period WHERE end IS NULL ORDER BY query_key") == [
        {"query_key": "a", "tier": 2}, {"query_key": "b", "tier": 1}]
    store._write(lambda conn: conn.execute("UPDATE query_load SET total_ms=1 WHERE query_key='b'"))
    store.rank_queries("db", NOW)
    assert store.full_detail_keys("db") == {"a"}
    assert store.read("SELECT query_key,tier FROM query_tier_period WHERE end IS NULL ORDER BY query_key") == [
        {"query_key": "a", "tier": 1}, {"query_key": "b", "tier": 2}]


def test_activity_sampling_parses_only_head_aliases(tmp_path, monkeypatch):
    from features.query_registry import statement_stats
    from features.query_registry.discovery import QueryDiscoveryCollector

    monkeypatch.setattr("shared.monitor.monitor_store.FULL_DETAIL_QUERIES", 1)
    monkeypatch.setattr("shared.monitor.monitor_store.SERIES_QUERIES", 2)
    monitor = MonitorStore(tmp_path / "monitor.db", clock=lambda: NOW)
    monitor.record("db", NOW, rows(3), {}, [])
    collector = QueryDiscoveryCollector("db", two_phase=True)
    collector._monitor = monitor
    collector._engine = "postgresql"
    collector._alias_map = {f"10:20:{index}:t": "000000000002" for index in range(2, 20002)}
    collector._alias_map["10:20:1:t"] = "000000000000"
    parsed = []
    original = statement_stats.parse_pg_engine_key

    def parse(key):
        parsed.append(key)
        return original(key)

    monkeypatch.setattr(statement_stats, "parse_pg_engine_key", parse)
    collector._record_monitor_cycle(NOW + 60, [], None, [
        {"pid": 1, "query_id": 1, "query": "SELECT * FROM orders", "state": "active", "duration_s": 1}])
    assert parsed == ["10:20:1:t"]
    assert monitor.read("SELECT query_key FROM query_sample") == [{"query_key": "000000000000"}]


def test_ten_thousand_identity_cap_preserves_updates_and_database_totals(tmp_path):
    from shared.monitor.monitor_store import SERIES_QUERIES

    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: NOW)
    statements = rows(SERIES_QUERIES)
    texts = {row["query_key"]: "SELECT * FROM orders" for row in statements}
    store.record("db", NOW, statements, texts, [])
    existing = statements[0]["query_key"]
    new = "overflow"
    store.record("db", NOW + 60,
                 [{"query_key": existing, "calls": 7, "total_ms": 70},
                  {"query_key": new, "calls": 9, "total_ms": 0.5}],
                 {existing: "SELECT id FROM orders", new: "SELECT * FROM new_table"}, [],
                 {"calls": 16, "total_ms": 70.5},
                 samples=[{"query_key": new, "text": "SELECT * FROM new_table"}])
    assert store.read("SELECT COUNT(*) n FROM query_text WHERE target='db'")[0]["n"] == SERIES_QUERIES
    for table in ("query_text", "query_load", "query_sample", "query_tier", "query_ui"):
        assert not store.read(f"SELECT 1 FROM {table} WHERE query_key=?", (new,))
    assert store.read("SELECT text,last_seen FROM query_text WHERE query_key=?", (existing,)) == [
        {"text": "SELECT id FROM orders", "last_seen": NOW + 60}]
    assert store.read("SELECT calls,total_ms FROM db_series WHERE res=60") == [{"calls": 16, "total_ms": 70.5}]
    assert store.read("SELECT calls FROM stmt_series WHERE query_key=? AND res=60", (EVERYTHING_ELSE,))[0]["calls"] == 9
    assert store.record_user_analysis("db", new, "SELECT * FROM new_table", NOW,
                                      analysis_ref="test", mode="explain") == {"job": None}
    store.record_sample("db", new, "SELECT * FROM new_table", NOW)
    assert not store.read("SELECT 1 FROM query_sample WHERE query_key=?", (new,))
    # The cap belongs to each target.
    store.record("other", NOW, [], {new: "SELECT * FROM new_table"}, [])
    assert store.read("SELECT 1 FROM query_text WHERE target='other'")


def test_monitoring_off_discards_collection_and_on_accepts_it(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: NOW)
    store.set_settings("db", {"enabled": False})
    args = ("db", NOW, [{"query_key": "q", "calls": 1, "total_ms": 1}],
            {"q": "SELECT * FROM orders"}, [], {"calls": 1, "total_ms": 1})
    store.record(*args)
    assert not store.read("SELECT 1 FROM query_text")
    assert not store.read("SELECT 1 FROM db_series")
    assert not store.read("SELECT 1 FROM monitor_pulse")
    store.set_settings("db", {"enabled": True})
    store.record(*args)
    assert store.read("SELECT 1 FROM query_text")
    assert store.read("SELECT 1 FROM db_series")


def test_upgrade_caps_existing_identities_and_preserves_aggregates(tmp_path, monkeypatch):
    import shared.monitor.monitor_store as module

    monkeypatch.setattr(module, "SERIES_QUERIES", 2)
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: NOW)
    store.ensure_open()
    def seed(conn):
        conn.executemany("INSERT INTO query_text VALUES ('db',?,?,?,?)",
                         [(key, "SELECT * FROM orders", NOW, NOW) for key in ("a", "b", "c")])
        conn.executemany("INSERT INTO query_load VALUES ('db',?,?,?)",
                         [(key, weight, NOW) for key, weight in (("a", 30), ("b", 20), ("c", 10))])
        conn.executemany("INSERT INTO stmt_series (target,query_key,res,bucket_start,calls,total_ms,rows,blks_hit,blks_read) VALUES ('db',?,60,?,1,10,0,0,0)",
                         [(key, NOW) for key in ("a", "b", "c")])
        conn.execute("PRAGMA user_version=22")
    store._write(seed)
    upgraded = MonitorStore(store.path, clock=lambda: NOW)
    assert {row["query_key"] for row in upgraded.read("SELECT query_key FROM query_text")} == {"a", "b"}
    assert upgraded.read("SELECT SUM(calls) n,SUM(total_ms) ms FROM stmt_series") == [{"n": 3, "ms": 30}]
    assert upgraded.read("SELECT calls FROM stmt_series WHERE query_key=?", (EVERYTHING_ELSE,)) == [{"calls": 1}]
