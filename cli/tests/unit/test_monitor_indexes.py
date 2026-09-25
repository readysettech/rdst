from features.monitor.api import routes
from features.monitor.indexes import index_columns, read_indexes
from shared.monitor.monitor_store import MonitorStore


def test_index_seed_and_changes(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    index = {"schema": "public", "table": "orders", "index_name": "idx_orders_customer",
             "definition": "CREATE INDEX idx_orders_customer ON public.orders USING btree (customer_id, created_at)"}
    assert store.sync_indexes("db", [index], 100) == []
    assert store.sync_indexes("db", [index], 200) == []
    assert store.sync_indexes("db", [], 300)[0]["kind"] == "dropped"
    assert store.sync_indexes("db", [index], 400)[0]["kind"] == "added"
    changed = {**index, "definition": "CREATE INDEX idx_orders_customer ON orders (created_at)"}
    assert [item["kind"] for item in store.sync_indexes("db", [changed], 500)] == ["dropped", "added"]
    assert index_columns(index["definition"]) == ["customer_id", "created_at"]


def test_mysql_index_rows():
    class Cursor:
        def execute(self, sql):
            assert sql.startswith("/*rdst:observe*/")
        def fetchall(self):
            return [("app", "orders", "idx", "customer_id", 1),
                    ("app", "orders", "idx", "created_at", 2)]
        def close(self):
            pass
    class Connection:
        def cursor(self):
            return Cursor()
    rows = read_indexes(Connection(), "mysql")
    assert rows[0]["definition"] == "(customer_id, created_at)"


def test_index_markers_are_neutral_facts(monkeypatch, tmp_path):
    """An index change is a timeline fact: no recommendation match, no effect claimed."""
    monkeypatch.setenv("RDST_DATA_DIR", str(tmp_path))
    store = MonitorStore()
    store.sync_indexes("db", [], 100)
    store.sync_indexes("db", [{"schema": "public", "table": "orders",
                               "index_name": "idx", "definition": "CREATE INDEX idx ON orders (customer_id)"}],
                       200)
    store.record("db", 140, [{"query_key": "0123456789ab", "calls": 2, "total_ms": 200}], {}, [])
    store.record("db", 260, [{"query_key": "0123456789ab", "calls": 2, "total_ms": 10}], {}, [])
    monkeypatch.setattr(routes, "_now", lambda: 300)
    series = routes.series("db", "0123456789ab")
    assert [(marker["kind"], marker["label"], marker["matches_recommendation"])
            for marker in series["markers"] if marker["kind"].startswith("index")] == [
        ("index_added", "idx added", False)]
    assert series["fix"] is None
    assert not [marker for marker in series["markers"] if marker["kind"].startswith("fix")]
    assert store.inbox() == []
