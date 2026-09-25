"""Monitor names stay unique across targets, pages, restarts and new arrivals."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from features.monitor.api import views
from features.monitor.naming import target_names
from shared.monitor.monitor_store import MonitorStore
from shared.monitor.statements import display_name, distinct_names


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("RDST_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(views, "registry_entries", lambda keys=None: {})
    monkeypatch.setattr(views, "assessments", lambda target, keys=None: {})
    return MonitorStore(tmp_path / "monitor.db", clock=lambda: 1000800)


def record(store, texts, ts=1000740, target="db"):
    store.record(target, ts, [{"query_key": key, "calls": 5, "total_ms": 50.0}
                              for key in texts], texts, [])


@pytest.mark.parametrize("texts,details", [
    ({"a": "SELECT id FROM orders", "b": "SELECT status FROM orders"},
     {"a": "returning id", "b": "returning status"}),
    ({"a": "SELECT count(*) FROM orders WHERE total > $1 AND status = $2",
      "b": "SELECT count(*) FROM orders WHERE total > $1 AND region = $2"},
     {"a": "filtered on status", "b": "filtered on region"}),
    ({"a": "SELECT * FROM orders JOIN customers USING (id) JOIN items USING (id)",
      "b": "SELECT * FROM orders JOIN customers USING (id) JOIN products USING (id)"},
     {"a": "with items", "b": "with products"}),
])
def test_small_distinguishing_sql_detail(texts, details):
    names = distinct_names(texts)
    assert len(set(names.values())) == len(texts)
    for key, detail in details.items():
        assert names[key] == f"{display_name(texts[key])}, {detail}"


def test_unique_names_stay_unchanged(store):
    texts = {"a": "SELECT id FROM orders", "b": "SELECT id FROM customers"}
    record(store, texts)
    assert target_names(store, "db") == {key: display_name(sql) for key, sql in texts.items()}


def test_existing_name_survives_restart_new_duplicate_and_retention(store):
    record(store, {"a": "SELECT id FROM orders"})
    original = target_names(store, "db")["a"]
    record(store, {"b": "SELECT status FROM orders"}, ts=1000750)
    names = target_names(MonitorStore(store.path), "db")
    assert names == {"a": original, "b": "SELECT on orders, returning status"}
    store.read("DELETE FROM query_text WHERE query_key='a'")
    record(store, {"c": "SELECT region FROM orders"}, ts=1000760)
    later = target_names(MonitorStore(store.path), "db")
    assert all(later[key] == name for key, name in names.items())
    assert len(set(later.values())) == 3


def test_suffixes_use_first_seen_order_and_survive_restart(store):
    record(store, {"z": "SELECT id FROM orders WHERE id > 1"}, ts=1000700)
    record(store, {"a": "SELECT id FROM orders WHERE id > 2"}, ts=1000710)
    record(store, {"m": "SELECT id FROM orders WHERE id > 3"}, ts=1000710)
    names = target_names(store, "db")
    assert [names[key] for key in ("z", "a", "m")] == [
        "SELECT on orders by id", "SELECT on orders by id, variant 2",
        "SELECT on orders by id, variant 3"]
    assert target_names(MonitorStore(store.path), "db") == names


def test_targets_and_concurrent_readers_allocate_independently(store):
    record(store, {"a": "SELECT id FROM orders", "b": "SELECT status FROM orders"})
    record(store, {"a": "SELECT id FROM orders"}, target="other")
    store.read("INSERT INTO monitor_setting VALUES (?,?,?)", (
        "db", "query_display_names", json.dumps({"a": "SELECT on orders (44)",
                                                "b": "SELECT on orders (1 columns)"})))
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: target_names(MonitorStore(store.path), "db"), range(4)))
    assert all(result == results[0] for result in results)
    assert target_names(store, "other") == {"a": "SELECT on orders"}


def test_missing_text_gets_name_when_text_arrives(store):
    record(store, {"a": ""})
    assert "a" not in target_names(store, "db")
    record(store, {"a": "SELECT id FROM orders"}, ts=1000750)
    assert target_names(store, "db")["a"] == "SELECT on orders"


def test_pagination_and_chart_use_target_names(store):
    texts = {"000000000001": "SELECT id FROM orders",
             "000000000002": "SELECT status FROM orders"}
    record(store, texts)
    names = target_names(store, "db")
    for offset in (0, 1):
        page = views.queries(store, "db", "1h", 1000800, sort="db_time_share", filter="all",
                             q="", limit=1, offset=offset)
        row = page["queries"][0]
        assert row["display_name"] == names[row["key"]]
    chart = views.load(store, "db", "1h", 1000800)
    assert {band["key"]: band["display_name"] for band in chart["bands"]
            if band["kind"] == "query"} == names


def test_inbox_analyses_and_reports_use_the_same_names(store):
    from features.monitor import report, snapshot
    from features.monitor.analyze_queue import queue_plan
    from features.monitor.api.routes import _queue_summary

    first, second = "000000000001", "000000000002"
    texts = {first: "SELECT id FROM orders", second: "SELECT status FROM orders"}
    record(store, texts)
    record(store, {first: texts[first]}, target="other")
    names = target_names(store, "db")
    for target, key in (("db", first), ("db", second), ("other", first)):
        store.upsert_item(target, "stuck_query", key, 0.9, "Stuck query",
                          {"query_key": key}, 1000800)
    items = views.inbox_items(store, store.inbox())
    assert {(item["target"], item["subject"]): item["subject_label"] for item in items} == {
        ("db", first): names[first], ("db", second): names[second],
        ("other", first): "SELECT on orders"}
    for key in texts:
        store.enqueue_analysis("db", key, "user", 1.0, 1000800)
    queue = _queue_summary(store, "db", queue_plan(store, "db", 1000800))
    assert {item["query_key"]: item["display_name"] for item in queue["next"]} == names
    rows, day_names = snapshot.day_queries(store, "db", 1000700, 1000801, 1000800, [], set())
    assert {row["query_key"]: row["display_name"] for row in rows} == names
    assert all(day_names[key] == name for key, name in names.items())
    run, _ = store.claim_report("db", "user", 1000800)
    store.update_report(run["id"], state="done", finished_at=1000800, report={
        "computed": {"top_queries": [{"query_key": key, "text": sql,
                                      "total_ms": 50, "db_time_share": 0.5}
                                     for key, sql in texts.items()]},
        "fix_first": [{"query_key": key, "action": "Check query"} for key in texts]})
    saved = store.read("SELECT * FROM report_run WHERE id=?", (run["id"],))[0]
    store.append_report_event(run["id"], {"type": "selected", "queries": [
        {"key": key, "display_name": "SELECT on orders (44)", "db_time_share": 0.5}
        for key in texts]}, 1000800)
    saved_events = store.read("SELECT * FROM report_event")
    presented = report.present_report(saved, store)
    assert {item["query_key"]: item["display_name"]
            for item in presented["report_json"]["fix_first"]} == names
    assert {item["key"]: item["display_name"] for item in presented["queries"]} == names
    assert names[first] in report.report_summary(saved, names)["verdict"]
    assert store.read("SELECT * FROM report_run WHERE id=?", (run["id"],))[0] == saved
    assert store.read("SELECT * FROM report_event") == saved_events


@pytest.mark.parametrize("suffix", ["(1 column)", "(1 columns)", "(3 columns)"])
def test_saved_projection_label_is_migrated_once(store, suffix):
    record(store, {"a": "SELECT count(*) FROM orders"})
    store.read("INSERT INTO monitor_setting VALUES (?,?,?)", (
        "db", "query_display_names", json.dumps({"a": f"COUNT on orders {suffix}"})))
    assert target_names(store, "db")["a"] == "COUNT on orders"
    record(store, {"b": "SELECT count(*), sum(total) FROM orders"}, ts=1000750)
    assert target_names(store, "db")["a"] == "COUNT on orders"


@pytest.mark.parametrize("left,right,detail", [
    ("SELECT email FROM customers WHERE country = $1 AND status = $2 AND region = $3",
     "SELECT id FROM customers WHERE country = $1 AND active = $2",
     "filtered on status and region"),
    ("SELECT email FROM customers ORDER BY created_at", "SELECT id FROM customers ORDER BY id",
     "sorted by created_at"),
    ("SELECT email FROM customers ORDER BY created_at DESC",
     "SELECT id FROM customers ORDER BY created_at", "sorted by created_at descending"),
    ("SELECT count(*) FROM customers GROUP BY day", "SELECT count(*) FROM customers GROUP BY month",
     "per day"),
    ("SELECT count(*) FROM customers GROUP BY date_trunc('day', created_at)",
     "SELECT count(*) FROM customers GROUP BY date_trunc('month', created_at)", "per day"),
    ("SELECT email FROM customers LIMIT 50", "SELECT id FROM customers LIMIT 100", "top 50"),
    ("SELECT id, email FROM customers", "SELECT id, phone FROM customers", "returning email"),
    ("SELECT id, email, status, region FROM customers", "SELECT id FROM customers",
     "returning email and status and region"),
    ("SELECT email FROM customers JOIN orders USING(id) JOIN payments USING(id) ORDER BY day",
     "SELECT phone FROM customers JOIN orders USING(id) JOIN refunds USING(id) ORDER BY month",
     "with payments"),
    ("SELECT count(*), email FROM customers GROUP BY day LIMIT 50",
     "SELECT count(*), phone FROM customers GROUP BY month LIMIT 100", "per day"),
])
def test_detail_priority(left, right, detail):
    assert distinct_names({"a": left, "b": right})["a"] == f"{display_name(left)}, {detail}"


def test_many_projections_and_unparsed_sql_use_variants():
    texts = {"a": "SELECT a, b, c, d FROM customers", "b": "SELECT e, f, g, h FROM customers"}
    assert list(distinct_names(texts).values()) == [
        "SELECT on customers", "SELECT on customers, variant 2"]
    assert list(distinct_names({"z": "SELECT (", "a": "SELECT ("}).values()) == [
        "SELECT", "SELECT, variant 2"]


def test_shared_filter_uses_next_difference():
    texts = {"a": "SELECT email FROM customers WHERE id > 1 AND status = 1 LIMIT 50",
             "b": "SELECT phone FROM customers WHERE id > 1 AND status = 1 LIMIT 100",
             "c": "SELECT id FROM customers WHERE id > 1 AND region = 1"}
    assert distinct_names(texts)["a"].endswith("filtered on status, top 50")


def test_migration_preserves_history_and_stable_labels(store, monkeypatch):
    texts = {"z": "SELECT email FROM customers", "a": "SELECT phone FROM customers",
             "m": "SELECT id FROM orders"}
    record(store, texts)
    legacy = json.dumps({"z": "SELECT on customers (44)",
                         "a": "SELECT on customers (1 columns)", "m": "Orders saved label"})
    store.read("INSERT INTO monitor_setting VALUES (?,?,?)", ("db", "query_display_names", legacy))
    names = target_names(store, "db")
    assert names == {"z": "SELECT on customers, returning email",
                     "a": "SELECT on customers, returning phone", "m": "Orders saved label"}
    saved = store.read("SELECT value FROM monitor_setting WHERE key='query_display_names'")
    assert saved[0]["value"] == legacy
    monkeypatch.setattr(store, "_write", lambda *args: pytest.fail("already migrated"))
    assert target_names(store, "db") == names


def test_ambiguous_saved_labels_are_repaired(store):
    record(store, {"a": "SELECT email FROM customers", "b": "SELECT phone FROM customers"})
    store.read("INSERT INTO monitor_setting VALUES (?,?,?)", (
        "db", "query_display_names_v2", '{"a":"Customers","b":"Customers"}'))
    names = target_names(store, "db")
    assert names["a"] == "SELECT on customers, returning email"
    assert names["b"] == "SELECT on customers, returning phone"
