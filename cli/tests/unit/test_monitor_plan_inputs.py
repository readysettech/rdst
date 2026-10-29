import pytest

from features.monitor import plan_inputs
from features.monitor.index_plan import build_plan
from shared.monitor.monitor_store import MonitorStore

NOW = 1_000_000


def _seed(tmp_path, monkeypatch, *, observed_s):
    store = MonitorStore(tmp_path / "monitor.db")
    texts = {"read": "SELECT * FROM orders WHERE customer_id = $1",
             "touch": "UPDATE sessions SET last_seen = $1 WHERE token = $2"}
    for minute in range(3):
        store.record("db", NOW - 300 + minute * 60,
                     [{"query_key": "read", "calls": 10, "total_ms": 900},
                      {"query_key": "touch", "calls": 50, "total_ms": 100}], texts, [])
    store.sync_indexes("db", [
        {"schema": "public", "table": "sessions", "index_name": "sessions_pkey",
         "definition": "CREATE UNIQUE INDEX sessions_pkey ON public.sessions USING btree (id)"},
        {"schema": "public", "table": "sessions", "index_name": "sessions_seen",
         "definition": "CREATE INDEX sessions_seen ON public.sessions USING btree (last_seen)"}],
        NOW - 300)

    def table(name, writes):
        return {"schema": "public", "table": name, "ins": writes, "upd": writes, "del": 0,
                "hot_upd": writes, "seq_scan": 0, "idx_scan": 0, "scan_rows": 0, "live_rows": 1000,
                "dead_rows": 0, "size_bytes": 8192}

    def index(name, scans, unique=False):
        return {"schema": "public", "table": "sessions", "index_name": name, "scans": scans,
                "size_bytes": 4096, "unique": unique, "primary": unique, "last_scan_at": None}

    for step, writes in ((0, 1000), (1, 1000 + 120 * observed_s // 2)):
        store.record_stats("db", {
            "engine": "postgresql", "version": 160000, "counter_since": NOW - 30 * 86400,
            "taken_at": NOW - observed_s + step * observed_s,
            "tables": [table("sessions", writes), table("orders", 0)],
            "indexes": [index("sessions_pkey", 500, unique=True), index("sessions_seen", 0)],
            "settings": {}})
    monkeypatch.setattr(plan_inputs, "latest_done_job",
                        lambda store, target, key: {"analysis_ref": f"ref-{key}"} if key == "read" else None)
    monkeypatch.setattr(plan_inputs, "_suggestions", lambda target, key, ref: (
        {"table": "public.orders", "columns": ["customer_id"], "method": "btree",
         "sql": "CREATE INDEX ON orders (customer_id)", "gain_pct": 85.0, "verified": True,
         "impact": "high"},))
    return store


def test_gather_measures_the_window_and_feeds_the_plan(tmp_path, monkeypatch):
    store = _seed(tmp_path, monkeypatch, observed_s=3600)
    inputs = plan_inputs.gather(store, "db", NOW, "postgresql")
    assert [query["key"] for query in inputs["queries"]] == ["read", "touch"]
    assert round(inputs["queries"][0]["share"], 2) == 0.9
    sessions = inputs["tables"]["sessions"]
    assert sessions["writes_per_s"] > 100 and sessions["updated_columns"] == ["last_seen"]
    assert sessions["hot_ratio"] == 1.0
    plan = build_plan(inputs)
    assert [(add["table"], add["columns"], add["label"]) for add in plan["add"]] == [
        ("orders", ["customer_id"], "safe")]
    assert [(drop["index_name"], drop["reason"], drop["confidence"]) for drop in plan["drop"]] == [
        ("sessions_seen", "unused", "high")]


def test_a_short_observation_measures_writes_between_readings(tmp_path, monkeypatch):
    store = _seed(tmp_path, monkeypatch, observed_s=60)
    inputs = plan_inputs.gather(store, "db", NOW, "postgresql")
    assert inputs["observed_s"] == 60
    assert inputs["tables"]["sessions"]["writes_per_s"] == 120.0


@pytest.mark.parametrize("engine", ["postgresql", "mysql"])
def test_writes_from_before_rdst_watched_never_count_as_a_rate(tmp_path, engine):
    """A bulk load lifts the engine's totals, but the rate is what changes between readings."""
    store = MonitorStore(tmp_path / "monitor.db")
    store.sync_indexes("db", [{"schema": "public", "table": "orders", "index_name": "orders_idx",
                               "definition": "CREATE INDEX orders_idx ON orders (customer_id)"}],
                       NOW - 600)

    def reading(ts, orders, products):
        def table(name, ins, upd):
            return {"schema": "public", "table": name, "ins": ins, "upd": upd, "del": 0,
                    "hot_upd": 0, "seq_scan": 0, "idx_scan": 0, "scan_rows": 0,
                    "live_rows": ins, "dead_rows": 0, "size_bytes": 8192}
        return {"engine": engine, "version": 160000, "counter_since": NOW - 900, "taken_at": ts,
                "tables": [table("orders", *orders), table("products", *products)],
                "indexes": [], "settings": {}}

    # Loaded right after the server started: 250,000 orders and 10,000 products.
    store.record_stats("db", reading(NOW - 300, (250_000, 0), (10_000, 0)))
    store.record_stats("db", reading(NOW - 150, (250_020, 10), (10_000, 0)))
    store.record_stats("db", reading(NOW, (250_040, 20), (10_000, 0)))
    tables = plan_inputs.gather(store, "db", NOW, engine)["tables"]
    assert tables["orders"]["writes_per_s"] == 0.2
    assert tables["products"]["writes_per_s"] == 0.0
    cost = plan_inputs.write_cost(store, "db", "INSERT INTO orders (customer_id) VALUES (1)",
                                  engine, NOW)
    assert cost["table_writes_per_s"] == tables["orders"]["writes_per_s"]


def test_write_cost_reads_the_table_a_write_changes(tmp_path, monkeypatch):
    store = _seed(tmp_path, monkeypatch, observed_s=3600)
    cost = plan_inputs.write_cost(store, "db", "UPDATE sessions SET last_seen = $1 WHERE token = $2",
                                  "postgresql", NOW)
    assert cost["table"] == "sessions" and cost["set_columns"] == ["last_seen"]
    assert cost["indexes_maintained"] == 2 and cost["hot_ratio"] == 1.0
    assert cost["table_writes_per_s"] > 100
    assert plan_inputs.write_cost(store, "db", "SELECT 1", "postgresql", NOW) is None
