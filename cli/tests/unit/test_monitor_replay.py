"""Calibrate attention filing with synthetic series and a scripted Jev client."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from features.monitor import judge as judge_module
from features.monitor import loop as monitor_loop
from features.monitor.delivery import LoadWindow, Relevance
from features.monitor.attention import group_many
from features.monitor.judge import AttentionJudge, reconcile_recovery
from shared.llm_manager.jev_client import JevClientError
from shared.llm_manager.inference_attribution import record_billable_call
from shared.monitor.monitor_store import MonitorStore
from tests.unit.monitor_replay_fixtures import NOW, many_slowdowns, planted, series


class EveryQueryTop(LoadWindow):
    def top(self, key):
        return True


class EveryQueryRelevant(Relevance):
    def __init__(self):
        super().__init__(EveryQueryTop(), EveryQueryTop())

    def rule(self, key):
        return "top_10_24h"


@pytest.fixture(autouse=True)
def replay_gates(monkeypatch):
    """Replays plant an hour of history, and some drive the judge with synthetic rows and no
    recorded series. The learning period and the relevance gate have their own tests
    (test_monitor_delivery.py); here a judge without recorded load treats every query as
    relevant, and one with recorded load uses the real gate."""
    monkeypatch.setenv("RDST_MONITOR_LEARNING_S", "0")
    real = judge_module.relevance

    def gate(store, target, now):
        measured = real(store, target, now)
        return measured if measured.day.rank else EveryQueryRelevant()

    monkeypatch.setattr(judge_module, "relevance", gate)
    monkeypatch.setattr(monitor_loop, "relevance", gate)


class Clock:
    def __init__(self, now=NOW):
        self.now = now

    def __call__(self):
        return self.now


class ScriptedJev:
    def __init__(self, choices=None, *, scope="separate", failures=None):
        self.choices = choices or {}
        self.scope = scope
        self.failures = list(failures or [])
        self.requests = []
        self.status = "still"

    def attention(self, **request):
        self.requests.append(request)
        if self.failures:
            failure = self.failures.pop(0)
            if failure:
                raise failure
        return {"request_id": request["request_id"], "model": "jev-1.13.0",
                "rubric_version": "query-attention-v1", "event_scope": self.scope,
                "queries": [{"query_key": query["query_key"],
                             "attention": self.choices.get(query["query_key"],
                                                            ("ignore", "latency_regression", 0.1))[0],
                             "kind": self.choices.get(query["query_key"],
                                                       ("ignore", "latency_regression", 0.1))[1],
                             "severity": self.choices.get(query["query_key"],
                                                           ("ignore", "latency_regression", 0.1))[2]}
                            for query in request["queries"]],
                "open_items": [{"id": item["id"], "status": self.status}
                               for item in request["open_items"]],
                "usage": {"input_tokens": 10, "output_tokens": 2}}


def loop_for(monkeypatch, tmp_path, store, clock, client, *, indexes=None):
    monkeypatch.setattr(monitor_loop.query_discovery, "collector_for",
                        lambda target: SimpleNamespace(_snapshot={}))
    monkeypatch.setattr(monitor_loop.MonitorLoop, "_monitoring_issues", lambda *args: None)
    judge = AttentionJudge(store, client=client, clock=clock)
    return monitor_loop.MonitorLoop(store, clock=clock, judge=judge,
                                    index_reader=lambda target: indexes or [])


def current_series(store, now, queries):
    """Record one observed five-minute window and a live collector sample."""
    def write(conn):
        for key, (calls, mean_ms) in queries.items():
            conn.execute("INSERT OR IGNORE INTO query_text VALUES (?,?,?,?,?)",
                         ("db", key, "SELECT * FROM orders", now - 300, now))
            conn.executemany("INSERT OR REPLACE INTO stmt_series (target,query_key,res,bucket_start,calls,total_ms,rows,blks_hit,blks_read) VALUES (?,?,?,?,?,?,?,?,?)",
                             [("db", key, 60, now - 300 + minute * 60,
                               calls, calls * mean_ms, 0, 0, 0) for minute in range(5)])
    store._write(write)
    store.record("db", now - 60, [], {}, [], {"calls": sum(v[0] for v in queries.values()),
                 "total_ms": sum(v[0] * v[1] for v in queries.values()),
                 "active_conns": 2, "max_conns": 20})


def test_planted_incidents_file_expected_inbox(monkeypatch, tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    planted(store)
    clock = Clock()
    client = ScriptedJev({"heavy_regression": ("notify", "latency_regression", 0.8),
                          "traffic_spike": ("inbox", "traffic_spike", 0.5),
                          "new_heavy": ("notify", "new_heavy_query", 0.7),
                          "index_improved": ("inbox", "query_improved", 0.4)})
    store.sync_indexes("db", [], NOW - 600)
    indexes = [{"schema": "public", "table": "orders", "index_name": "idx_orders",
                "definition": "CREATE INDEX idx_orders ON orders(id)"}]
    loop = loop_for(monkeypatch, tmp_path, store, clock, client, indexes=indexes)
    loop.cycle("db")
    items = {item["type"]: item for item in store.inbox("new")}
    assert set(items) == {"latency_regression", "traffic_spike", "new_heavy_query",
                          "query_improved"}
    assert "always_slow" not in {query["query_key"] for query in client.requests[0]["queries"]}
    # The regression notifies, the spike is worth a look, the improvement is digest-only and
    # the new query waits for a second check in the top 10.
    assert {kind: item["delivery"] for kind, item in items.items()} == {
        "latency_regression": "notify", "traffic_spike": "inbox",
        "new_heavy_query": "digest", "query_improved": "digest"}
    assert len([event for event in store.inbox_events() if event["kind"] == "notified"]) == 1
    assert json.loads(items["latency_regression"]["body_json"])["relevance_rule"] == "top_10_24h"
    assert client.requests[0]["target_numbers"]["max_connections"] == 20
    assert client.requests[0]["index_changes"] == []
    heavy = next(query for query in client.requests[0]["queries"]
                 if query["query_key"] == "heavy_regression")
    assert heavy["current_5m"]["mean_ms"] == 250
    assert heavy["baseline"]["1h"]["mean_ms"] == 100
    assert heavy["current_5m"]["calls"] == 500
    assert heavy["extra_db_time_ms_per_5m_vs_1h"] == pytest.approx(75000)
    assert items["latency_regression"]["subject"] == "heavy_regression"
    assert store.read("SELECT outcome FROM judge_run") == [{"outcome": "filed"}]
    loop.cycle("db")
    assert len(client.requests) == 2
    # Still in the top 10 by last-hour load on the next check: the new query now notifies.
    clock.now += 300
    loop.cycle("db")
    promoted = store.read("SELECT delivery FROM inbox_item WHERE type='new_heavy_query'")
    assert promoted == [{"delivery": "notify"}]
    assert len([event for event in store.inbox_events() if event["kind"] == "notified"]) == 2


def test_jev_is_not_called_or_trusted_to_close_items_without_a_shortlist(tmp_path):
    """Items open and close on measurements only; an empty shortlist costs no Jev call."""
    store = MonitorStore(tmp_path / "monitor.db")
    item = store.upsert_item("db", "many_queries_changed", f"burst:{NOW}", 0.9,
                             "Many queries changed at once",
                             {"query_keys": ["q1", "q2"], "burst_start": NOW,
                              "burst_last_join": NOW}, NOW)
    clock = Clock(NOW + 300)
    client = ScriptedJev()
    client.status = "resolved"
    judge = AttentionJudge(store, client=client, clock=clock)
    for cycle in range(3):
        judge([], {"target": "db", "inbox": store.inbox(target="db"),
                   "measured": [], "database": {}})
        clock.now += 300
        if cycle < 2:
            assert store.inbox("new")[0]["id"] == item["id"]
    # The open item is reassessed each cycle, but Jev's "resolved" is never obeyed.
    assert [request["queries"] for request in client.requests] == [[], []]
    # Three cycles in which measurements flag none of the episode's queries close it.
    assert store.read("SELECT state,resolved_by FROM inbox_item WHERE id=?", (item["id"],)) == [
        {"state": "resolved", "resolved_by": "code"}]


@pytest.mark.parametrize("kind,metric,normal,observed", [
    ("latency_regression", "mean_ms", 10, 11),
    ("plan_change", "mean_ms", 10, 11),
    ("table_slowdown", "mean_ms", 10, 11),
    ("traffic_spike", "qps", 2, 2.2),
    ("new_heavy_query", "db_time_share", 0, 0.04),
])
def test_measurable_recovery_requires_three_cycles(tmp_path, kind, metric, normal, observed):
    store = MonitorStore(tmp_path / "monitor.db")
    series(store, "q")
    item = store.upsert_item("db", kind, "q", 0.7, kind,
                             {"at_filing": {metric: 30},
                              "baseline": {"1h": {metric: normal}}}, NOW)
    row = {"query_key": "q", "calls": 30, "mean_ms": 30, "qps": 4,
           "db_time_share": 0.3}
    row[metric] = observed
    for cycle in range(2):
        reconcile_recovery(store, "db", NOW + (cycle + 1) * 300, [row])
        assert store.read("SELECT state FROM inbox_item WHERE id=?", (item["id"],)) == [
            {"state": "new"}]
    reconcile_recovery(store, "db", NOW + 900, [row])
    resolved = store.inbox("resolved")[0]
    assert resolved["resolved_by"] == "code"
    body = json.loads(resolved["body_json"])
    assert body["resolution"]["before"][metric] == 30
    assert body["resolution"]["after"][metric] == observed


def test_recovery_streak_resets_and_stopped_query_closes_on_return(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    series(store, "q")
    latency = store.upsert_item("db", "latency_regression", "q", .7, "slow",
                                {"at_filing": {"mean_ms": 30},
                                 "baseline": {"1h": {"mean_ms": 10}}}, NOW)
    stopped = store.upsert_item("db", "query_stopped_running", "q", .7, "stopped",
                                {"at_filing": {"qps": 0}}, NOW)
    row = {"query_key": "q", "calls": 30, "mean_ms": 11, "qps": 2,
           "db_time_share": .1}
    reconcile_recovery(store, "db", NOW + 300, [row])
    assert store.read("SELECT resolved_by FROM inbox_item WHERE id=?", (stopped["id"],)) == [
        {"resolved_by": "code"}]
    reconcile_recovery(store, "db", NOW + 600, [])
    assert json.loads(store.inbox("new")[0]["body_json"])["recovery_streak"] == 0
    reconcile_recovery(store, "db", NOW + 900, [{**row, "mean_ms": 20}])
    assert json.loads(store.inbox("new")[0]["body_json"])["recovery_streak"] == 0
    for cycle in range(3):
        reconcile_recovery(store, "db", NOW + 1200 + cycle * 300, [row])
    assert store.read("SELECT resolved_by FROM inbox_item WHERE id=?", (latency["id"],)) == [
        {"resolved_by": "code"}]


def test_new_heavy_ages_into_tracked_query(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    series(store, "q")
    item = store.upsert_item("db", "new_heavy_query", "q", .7, "heavy",
                             {"at_filing": {"db_time_share": .4}}, NOW)
    row = {"query_key": "q", "calls": 30, "mean_ms": 10, "qps": 2,
           "db_time_share": .3}
    reconcile_recovery(store, "db", NOW + 86400, [row])
    assert store.read("SELECT resolved_by FROM inbox_item WHERE id=?", (item["id"],)) == [
        {"resolved_by": "code"}]


def test_measured_recovery_closes_returned_dismissed_item(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    series(store, "q")
    item = store.upsert_item("db", "query_stopped_running", "q", .5, "stopped",
                             {"at_filing": {"qps": 0}}, NOW)
    store.action_item(item["id"], "dismiss", NOW + 1)
    store.upsert_item("db", "query_stopped_running", "q", .8, "stopped",
                      {"at_filing": {"qps": 0}}, NOW + 2)
    store.action_item(item["id"], "dismiss", NOW + 3)
    reconcile_recovery(store, "db", NOW + 300, [{"query_key": "q", "calls": 2,
                                                 "qps": .01, "mean_ms": 10,
                                                 "db_time_share": .1}])
    assert store.read("SELECT state,resolved_by FROM inbox_item WHERE id=?", (item["id"],)) == [
        {"state": "resolved", "resolved_by": "code"}]
    assert "still_happening" not in json.loads(store.read(
        "SELECT body_json FROM inbox_item WHERE id=?", (item["id"],))[0]["body_json"])


def test_repeat_verdict_preserves_filing_baseline_and_recovery_streak(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    series(store, "q")
    item = store.upsert_item("db", "latency_regression", "q", .7, "slow",
                             {"at_filing": {"mean_ms": 20},
                              "baseline": {"1h": {"mean_ms": 10}}}, NOW)
    row = {"query_key": "q", "calls": 30, "mean_ms": 11, "qps": 2,
           "db_time_share": .2, "baseline": {"1h": {"mean_ms": 18}}}
    reconcile_recovery(store, "db", NOW + 300, [row])
    judge = AttentionJudge(store, client=ScriptedJev(), clock=Clock(NOW + 300))
    judge._file("db", NOW + 300, [row], {"target_numbers": {}},
                {"event_scope": "separate", "queries": [{"query_key": "q",
                 "attention": "inbox", "kind": "latency_regression", "severity": .7}],
                 "open_items": []})
    body = json.loads(store.read("SELECT body_json FROM inbox_item WHERE id=?",
                                 (item["id"],))[0]["body_json"])
    assert body["at_filing"]["mean_ms"] == 20
    assert body["baseline"]["1h"]["mean_ms"] == 10
    assert body["recovery_streak"] == 1


def test_new_heavy_reopens_after_clear_and_code_closes_it(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    series(store, "q")
    item = store.upsert_item("db", "new_heavy_query", "q", .6, "heavy",
                             {"at_filing": {"db_time_share": .3}}, NOW - 2100)
    store.action_item(item["id"], "resolve", NOW - 1800, resolved_by="jev")
    row = {"query_key": "q", "calls": 30, "mean_ms": 10, "qps": 2,
           "db_time_share": .4, "baseline": {"1h": None}}
    for cycle in range(3):
        reconcile_recovery(store, "db", NOW - 1500 + cycle * 300,
                           [{**row, "db_time_share": .03}])
    judge = AttentionJudge(store, client=ScriptedJev(), clock=Clock())
    judge._file("db", NOW, [row], {"target_numbers": {}},
                {"event_scope": "separate", "queries": [{"query_key": "q",
                 "attention": "notify", "kind": "new_heavy_query", "severity": .6}],
                 "open_items": []})
    assert store.read("SELECT state FROM inbox_item WHERE id=?", (item["id"],)) == [
        {"state": "new"}]
    for cycle in range(3):
        reconcile_recovery(store, "db", NOW + (cycle + 1) * 300,
                           [{**row, "db_time_share": .03}])
    assert store.read("SELECT state,resolved_by FROM inbox_item WHERE id=?", (item["id"],)) == [
        {"state": "resolved", "resolved_by": "code"}]


def test_replay_regression_closes_when_latency_recovers(monkeypatch, tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    series(store, "q", before_ms=10, after_ms=14, before_calls=100, after_calls=100)
    clock = Clock()
    client = ScriptedJev({"q": ("notify", "latency_regression", .8)})
    loop = loop_for(monkeypatch, tmp_path, store, clock, client)
    loop.cycle("db")
    item = store.inbox("new")[0]
    for cycle in range(1, 4):
        clock.now = NOW + cycle * 300
        for minute in range(5):
            store.record("db", clock.now - 300 + minute * 60,
                         [{"query_key": "q", "calls": 100, "total_ms": 1000}],
                         {}, [], None)
        loop.cycle("db")
    resolved = store.read("SELECT state,resolved_by,body_json FROM inbox_item WHERE id=?",
                          (item["id"],))[0]
    assert (resolved["state"], resolved["resolved_by"]) == ("resolved", "code")
    assert json.loads(resolved["body_json"])["resolution"]["after"]["mean_ms"] == 10
    clock.now += 300
    for minute in range(5):
        store.record("db", clock.now - 300 + minute * 60,
                     [{"query_key": "q", "calls": 100, "total_ms": 2000}],
                     {}, [], None)
    loop.cycle("db")
    reopened = store.read("SELECT state,body_json FROM inbox_item WHERE id=?", (item["id"],))[0]
    assert reopened["state"] == "new"
    assert json.loads(reopened["body_json"])["at_filing"]["mean_ms"] == 20


def test_many_queries_group_without_index_context_in_request(monkeypatch, tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    many_slowdowns(store)
    store.sync_indexes("db", [], NOW - 600)
    clock = Clock()
    client = ScriptedJev({f"slow_{i}": ("inbox", "latency_regression", 0.6)
                          for i in range(6)})
    indexes = [{"schema": "public", "table": "orders", "index_name": "idx_new",
                "definition": "CREATE INDEX idx_new ON orders(id)"}]
    loop = loop_for(monkeypatch, tmp_path, store, clock, client, indexes=indexes)
    loop.cycle("db")
    items = store.inbox("new")
    assert len(items) == 1 and items[0]["title"] == "6 queries got slower at the same time"
    assert len(json.loads(items[0]["body_json"])["query_keys"]) == 6
    # Every member was an inbox-level answer, so the episode is listed without a push.
    assert items[0]["delivery"] == "inbox"
    assert not [event for event in store.inbox_events() if event["kind"] == "notified"]
    # Index changes are timeline facts only; they never reach the judgment.
    assert client.requests[0]["index_changes"] == []
    assert client.requests[0]["queries"][0]["recent_index_changes"] == []
    store.action_item(items[0]["id"], "dismiss", NOW + 1)
    chosen = json.loads(store.read("SELECT shortlist_json FROM judge_run")[0]["shortlist_json"])
    clock.now += 7 * 86400 + 2
    AttentionJudge(store, client=client, clock=clock)(chosen, {"target": "db"})
    # Dismissed and still happening a week later: reassessed, never returned or pushed.
    assert len(client.requests) == 2
    assert store.inbox("dismissed")[0]["type"] == "many_queries_changed"
    assert not [event for event in store.inbox_events() if event["kind"] == "notified"]


GROUP_CLOSED = {"many_queries_changed"}


def closed_kinds(closed):
    """The kinds an attention cycle closed; burst items each have their own subject."""
    return {kind for kind, _ in closed}


def _group_cycle(judge, store, ts, keys, *, recovered=()):
    rows = [{"query_key": key, "calls": 300,
             "mean_ms": 10 if key in recovered else 30, "qps": 1,
             "db_time_share": .2, "baseline": {"1h": {"mean_ms": 10}}}
            for key in keys]
    verdict = {"event_scope": "database_wide", "queries": [
        {"query_key": key, "kind": "latency_regression", "attention": "notify",
         "severity": .8} for key in keys], "open_items": []}
    closed = reconcile_recovery(store, "db", ts, rows)
    judge._file("db", ts, rows, {"target_numbers": {"total_qps": 2}}, verdict,
                just_closed=closed)
    return closed


def test_group_sequential_incidents_are_separate_bursts(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    judge = AttentionJudge(store, client=ScriptedJev(), clock=Clock())
    first = ("orders_list", "orders_count")
    _group_cycle(judge, store, NOW, first)
    item = store.inbox("new")[0]
    for cycle in range(1, 4):
        closed = _group_cycle(judge, store, NOW + cycle * 300, first,
                              recovered=first)
    assert closed_kinds(closed) == GROUP_CLOSED
    resolved = store.inbox("resolved")[0]
    assert resolved["id"] == item["id"] and resolved["resolved_by"] == "code"
    resolution = json.loads(resolved["body_json"])["resolution"]
    assert set(resolution["before"]) == set(resolution["after"]) == set(first)
    assert resolution["before"]["orders_list"]["mean_ms"] == 30
    assert resolution["after"]["orders_list"]["mean_ms"] == 10
    second = ("customers_list", "customers_count")
    _group_cycle(judge, store, NOW + 1500, second)
    # One burst, one item: the later burst is an item of its own.
    later = store.inbox("new")[0]
    body = json.loads(later["body_json"])
    assert later["id"] != item["id"] and later["subject"] == f"burst:{NOW + 1500}"
    assert set(body["recovery_queries"]) == set(second)
    assert not set(first) & set(body["episode_query_keys"])
    assert body["burst_start"] == body["burst_last_join"] == NOW + 1500
    events = [event["kind"] for event in store.inbox_events()]
    assert events.count("created") == 2 and events.count("reopened") == 0
    assert events.count("notified") == 2


def test_group_overlap_retires_members_and_closes_after_both_recover(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    judge = AttentionJudge(store, client=ScriptedJev(), clock=Clock())
    first = ("orders_list", "orders_count")
    second = ("products_list", "products_count")
    _group_cycle(judge, store, NOW, first)
    _group_cycle(judge, store, NOW + 300, first + second)
    for cycle in range(2, 5):
        _group_cycle(judge, store, NOW + cycle * 300, first + second,
                     recovered=first)
    item = store.inbox("new")[0]
    body = json.loads(item["body_json"])
    assert set(body["query_keys"]) == set(second)
    assert set(body["recovered_queries"]) == set(first)
    assert set(body["episode_query_keys"]) == set(first + second)
    for cycle in range(5, 8):
        closed = _group_cycle(judge, store, NOW + cycle * 300, first + second,
                              recovered=first + second)
    assert closed_kinds(closed) == GROUP_CLOSED
    assert store.inbox("resolved")[0]["id"] == item["id"]
    resolution = json.loads(store.inbox("resolved")[0]["body_json"])["resolution"]
    assert set(resolution["after"]) == set(first + second)
    kinds = [event["kind"] for event in store.inbox_events()]
    # New members join the episode without a second push; one episode, one notification.
    assert kinds == ["created", "notified", "updated", "updated", "resolved"]


def test_group_repeat_verdict_has_no_update_or_notification_spam(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    judge = AttentionJudge(store, client=ScriptedJev(), clock=Clock())
    keys = ("orders_list", "orders_count")
    for cycle in range(5):
        _group_cycle(judge, store, NOW + cycle * 300, keys)
    kinds = [event["kind"] for event in store.inbox_events()]
    assert kinds == ["created", "notified"]


def test_stopped_load_generator_then_index_incident_gets_fresh_group(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    judge = AttentionJudge(store, client=ScriptedJev(), clock=Clock())
    old_keys = [f"old_load_{index}" for index in range(10)]
    old_rows = [{"query_key": key, "calls": 300, "mean_ms": 30, "qps": 1,
                 "db_time_share": .1, "baseline": {"1h": {"mean_ms": 10, "qps": 1}}}
                for key in old_keys]
    old_rows[0] = {**old_rows[0], "calls": 0, "mean_ms": 0, "qps": 0}
    old_verdict = {"event_scope": "database_wide", "queries": [
        {"query_key": key, "kind": "query_stopped_running" if index == 0 else
         "latency_regression", "attention": "notify", "severity": .7}
        for index, key in enumerate(old_keys)], "open_items": []}
    store._write(lambda conn: conn.executemany(
        "INSERT INTO stmt_series (target,query_key,res,bucket_start,calls,total_ms,rows,blks_hit,blks_read) VALUES (?,?,?,?,?,?,?,?,?)",
        [("db", key, 60, NOW - 60, 300, 9000, 0, 0, 0)
         for key in old_keys[1:]]))
    judge._file("db", NOW, old_rows, {"target_numbers": {}}, old_verdict)
    group_id = store.inbox("new")[0]["id"]

    def collect(ts):
        store.record("db", ts - 60, [], {}, [], {"calls": 0, "total_ms": 0})

    collect(NOW + 300)
    reconcile_recovery(store, "db", NOW + 300, [])
    body = json.loads(store.inbox("new")[0]["body_json"])
    assert old_keys[0] not in body["recovered_queries"]
    assert old_keys[0] in body["query_keys"]
    for cycle in range(1, 4):
        ts = NOW + cycle * 600
        collect(ts)
        reconcile_recovery(store, "db", ts, [])
    # The slow calls ended, but the stopped member awaits resumption or expiry.
    assert json.loads(store.inbox("new")[0]["body_json"])["query_keys"] == [old_keys[0]]
    assert not store.inbox("resolved")

    incident = NOW + 86400 + 600
    collect(incident)
    closed = reconcile_recovery(store, "db", incident, [])
    expired = store.inbox("resolved")[0]
    assert expired["id"] == group_id
    assert json.loads(expired["body_json"])["settled"]

    fresh_keys = ("index_regression_list", "index_regression_count")
    def incident_rows(mean):
        return [{"query_key": key, "calls": 300, "mean_ms": mean, "qps": 1,
                 "db_time_share": .2, "baseline": {"1h": {"mean_ms": 10, "qps": 1}}}
                for key in fresh_keys]
    fresh_verdict = {"event_scope": "database_wide", "queries": [
        {"query_key": key, "kind": "latency_regression", "attention": "notify",
         "severity": .8} for key in fresh_keys], "open_items": []}
    index = {"schema": "public", "table": "orders", "index_name": "idx_orders",
             "definition": "CREATE INDEX idx_orders ON orders(id)"}
    store.sync_indexes("db", [index], incident - 300)
    judge._file("db", incident, incident_rows(30), {"target_numbers": {}},
                fresh_verdict, just_closed=closed)
    fresh = store.inbox("new")[0]
    body = json.loads(fresh["body_json"])
    assert fresh["id"] != group_id and fresh["reopened_count"] == 0
    assert set(body["episode_query_keys"]) == set(fresh_keys)
    assert not set(old_keys) & set(body["episode_query_keys"])
    store.sync_indexes("db", [], incident + 300)
    for cycle in range(1, 5):
        judge._file("db", incident + cycle * 300, incident_rows(30),
                    {"target_numbers": {}}, fresh_verdict)
    assert [event["kind"] for event in store.inbox_events()] == [
        "created", "notified", "updated", "resolved", "created", "notified"]
    store.sync_indexes("db", [index], incident + 1500)
    assert [row["kind"] for row in store.read(
        "SELECT kind FROM index_change WHERE target=? ORDER BY id", ("db",))] == [
        "dropped", "added"]
    for cycle in range(1, 4):
        reconcile_recovery(store, "db", incident + 1200 + cycle * 300,
                           incident_rows(10))
    assert store.inbox("resolved")[0]["id"] == fresh["id"]

    spike = incident + 2400
    spike_keys = ("traffic_list", "traffic_count")
    def spike_rows(qps):
        return [{"query_key": key, "calls": 300, "mean_ms": 10, "qps": qps,
                 "db_time_share": .2, "baseline": {"1h": {"mean_ms": 10, "qps": 1}}}
                for key in spike_keys]
    spike_verdict = {"event_scope": "database_wide", "queries": [
        {"query_key": key, "kind": "traffic_spike", "attention": "inbox",
         "severity": .8} for key in spike_keys], "open_items": []}
    judge._file("db", spike, spike_rows(3), {"target_numbers": {}}, spike_verdict)
    body = json.loads(store.inbox("new")[0]["body_json"])
    assert set(body["episode_query_keys"]) == set(spike_keys)
    assert not set(old_keys + list(fresh_keys)) & set(body["episode_query_keys"])
    for cycle in range(1, 4):
        reconcile_recovery(store, "db", spike + cycle * 300, spike_rows(1))
    assert len(store.inbox("resolved")) == 3
    # Each burst is its own item; the traffic one is inbox-level, so it files without a push.
    assert [event["kind"] for event in store.inbox_events()] == [
        "created", "notified", "updated", "resolved", "created", "notified",
        "resolved", "created", "resolved"]


def test_unchanged_or_unsupported_group_verdict_does_not_write_updates(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    judge = AttentionJudge(store, client=ScriptedJev(), clock=Clock())
    rows = [{"query_key": key, "calls": 300, "mean_ms": 30, "qps": 1,
             "db_time_share": .2, "baseline": {"1h": {"mean_ms": 10, "qps": 1}}}
            for key in ("a", "b")]
    verdict = {"event_scope": "database_wide", "queries": [
        {"query_key": key, "kind": "latency_regression", "attention": "notify",
         "severity": .7} for key in ("a", "b")], "open_items": []}
    judge._file("db", NOW, rows, {"target_numbers": {}}, verdict)
    for cycle in range(1, 5):
        unchanged = [{**row, "mean_ms": 10.5} for row in rows]
        repeated = {**verdict, "queries": [
            {**answer, "kind": "traffic_spike" if cycle % 2 else
             "latency_regression"} for answer in verdict["queries"]]}
        judge._file("db", NOW + cycle * 300, unchanged,
                    {"target_numbers": {}}, repeated)
    assert [event["kind"] for event in store.inbox_events()] == ["created", "notified"]


def test_group_recovers_before_a_real_second_index_regression(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    judge = AttentionJudge(store, client=ScriptedJev(), clock=Clock())
    keys = ("c58ed20d30d1", "99aab2298038")
    normal = {keys[0]: .06, keys[1]: .08}

    def rows(means, hour):
        result = [{"query_key": key, "calls": 1500, "mean_ms": means[key],
                   "qps": 5, "db_time_share": .3,
                   "baseline": {"1h": {"mean_ms": hour[key], "qps": 5},
                                "1d": {"mean_ms": .5},
                                "7d": {"mean_ms": .04}}}
                  for key in keys]
        result.append({"query_key": "other", "calls": 300, "mean_ms": 10,
                       "qps": 1, "db_time_share": .1,
                       "baseline": {"1h": {"mean_ms": 10, "qps": 1}}})
        return result

    def verdict(kinds):
        return {"event_scope": "database_wide", "queries": [
            {"query_key": key, "kind": kind, "attention": "notify", "severity": .8}
            for key, kind in kinds.items()], "open_items": []}

    initial = rows({keys[0]: 1.25, keys[1]: 1.29}, normal)
    judge._file("db", NOW, initial, {"target_numbers": {}},
                verdict({keys[0]: "latency_regression", keys[1]: "latency_regression",
                         "other": "new_heavy_query"}))
    back_in_band = rows(normal, {keys[0]: .86, keys[1]: .88})
    for cycle in range(1, 4):
        reconcile_recovery(store, "db", NOW + cycle * 300, back_in_band)
        # The rolling baseline includes the first incident. Neither an
        # "improved" answer nor a stale regression is a new condition.
        judge._file("db", NOW + cycle * 300, back_in_band,
                    {"target_numbers": {}}, verdict({keys[0]: "latency_regression",
                                                       keys[1]: "query_improved"}))
    body = json.loads(store.inbox("new")[0]["body_json"])
    assert set(keys) <= set(body["recovered_queries"])
    assert not set(keys) & set(body["recovery_queries"])
    assert [event["kind"] for event in store.inbox_events()] == [
        "created", "notified", "updated"]

    second = rows({keys[0]: 3.16, keys[1]: 3.14},
                  {keys[0]: 1.0, keys[1]: 1.0})
    judge._file("db", NOW + 1200, second, {"target_numbers": {}},
                verdict({key: "latency_regression" for key in keys}))
    body = json.loads(store.inbox("new")[0]["body_json"])
    assert set(keys) <= set(body["recovery_queries"])
    assert all(body["recovery_queries"][key]["filed_at"] == NOW + 1200
               for key in keys)
    assert {key: body["recovery_queries"][key]["baseline_mean_ms"] for key in keys} == normal
    # Twenty minutes later it is a burst of its own, measured against the normal level
    # its members recovered to.
    assert [event["kind"] for event in store.inbox_events()] == [
        "created", "notified", "updated", "created", "notified"]


def test_resolved_latency_item_ignores_in_band_reflag_then_reopens(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    judge = AttentionJudge(store, client=ScriptedJev(), clock=Clock())
    verdict = {"event_scope": "separate", "queries": [
        {"query_key": "q", "kind": "latency_regression", "attention": "notify",
         "severity": .8}], "open_items": []}

    def row(mean):
        return {"query_key": "q", "calls": 300, "mean_ms": mean, "qps": 1,
                "db_time_share": .3,
                "baseline": {"1h": {"mean_ms": 10, "qps": 1},
                             "7d": {"mean_ms": 5}}}

    judge._file("db", NOW, [row(30)], {"target_numbers": {}}, verdict)
    for cycle in range(1, 4):
        reconcile_recovery(store, "db", NOW + cycle * 300, [row(10)])
    assert store.inbox("resolved")[0]["resolved_by"] == "code"
    judge._file("db", NOW + 1200, [row(10)], {"target_numbers": {}}, verdict)
    assert store.inbox("resolved")[0]["reopened_count"] == 0
    judge._file("db", NOW + 1500, [row(30)], {"target_numbers": {}}, verdict)
    assert store.inbox("new")[0]["reopened_count"] == 1
    assert [event["kind"] for event in store.inbox_events()] == [
        "created", "notified", "resolved", "reopened", "notified"]


def test_resolved_group_ignores_in_band_reflag_then_reopens(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    judge = AttentionJudge(store, client=ScriptedJev(), clock=Clock())
    keys = ("a", "b")
    verdict = {"event_scope": "database_wide", "queries": [
        {"query_key": key, "kind": "latency_regression", "attention": "notify",
         "severity": .8} for key in keys], "open_items": []}

    def rows(mean):
        return [{"query_key": key, "calls": 300, "mean_ms": mean, "qps": 1,
                 "db_time_share": .3,
                 "baseline": {"1h": {"mean_ms": 10, "qps": 1},
                              "7d": {"mean_ms": 5}}} for key in keys]

    judge._file("db", NOW, rows(30), {"target_numbers": {}}, verdict)
    for cycle in range(1, 4):
        reconcile_recovery(store, "db", NOW + cycle * 300, rows(10))
    assert store.inbox("resolved")[0]["resolved_by"] == "code"
    judge._file("db", NOW + 1200, rows(10), {"target_numbers": {}}, verdict)
    assert store.inbox("resolved")[0]["reopened_count"] == 0
    judge._file("db", NOW + 1500, rows(30), {"target_numbers": {}}, verdict)
    # The same queries bursting again: the item's second occurrence.
    repeat = store.inbox("new")[0]
    assert repeat["reopened_count"] == 1
    assert len(json.loads(repeat["body_json"])["occurrences"]) == 2
    assert [event["kind"] for event in store.inbox_events()] == [
        "created", "notified", "resolved", "reopened", "notified"]


def test_settled_stopped_group_does_not_reopen_without_resume(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    judge = AttentionJudge(store, client=ScriptedJev(), clock=Clock())
    rows = [{"query_key": key, "calls": 0, "mean_ms": 0, "qps": 0,
             "db_time_share": 0, "baseline": {"1h": {"mean_ms": 10}}}
            for key in ("old_a", "old_b")]
    verdict = {"event_scope": "database_wide", "queries": [
        {"query_key": key, "kind": "query_stopped_running", "attention": "inbox",
         "severity": .7} for key in ("old_a", "old_b")], "open_items": []}
    judge._file("db", NOW, rows, {"target_numbers": {}}, verdict)
    assert closed_kinds(reconcile_recovery(store, "db", NOW + 86400, rows)) == GROUP_CLOSED
    for cycle in range(2, 5):
        judge._file("db", NOW + 86400 + cycle * 300, rows, {"target_numbers": {}}, verdict)
    assert store.inbox("resolved")[0]["reopened_count"] == 0
    # Stopped queries are inbox-level: listed, never pushed.
    assert [event["kind"] for event in store.inbox_events()] == ["created", "resolved"]


def test_group_with_no_active_recovery_members_closes(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    store.upsert_item("db", "many_queries_changed", "database", .7, "Many queries",
                      {"query_keys": [], "recovery_queries": {},
                       "recovered_queries": {"old": {"before": {"qps": 1},
                                                     "after": {"qps": 0}}}}, NOW)
    assert reconcile_recovery(store, "db", NOW + 300, []) == {
        ("many_queries_changed", "database")}
    assert store.inbox("resolved")[0]["resolved_by"] == "code"


def test_new_group_member_updates_without_a_second_push(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    judge = AttentionJudge(store, client=ScriptedJev(), clock=Clock())
    def file(keys, attention, ts):
        rows = [{"query_key": key, "calls": 300, "mean_ms": 30, "qps": 1,
                 "db_time_share": .2 if key in ("a", "b") else .01,
                 "baseline": {"1h": {"mean_ms": 10, "qps": 1}}}
                for key in keys]
        verdict = {"event_scope": "database_wide", "queries": [
            {"query_key": key, "kind": "latency_regression",
             "attention": attention.get(key, "inbox"), "severity": .6}
            for key in keys], "open_items": []}
        judge._file("db", ts, rows, {"target_numbers": {}}, verdict)

    file(("a", "b"), {"a": "notify"}, NOW)
    file(("a", "b", "c"), {"c": "notify"}, NOW + 300)
    file(("a", "b", "c", "d"), {}, NOW + 600)
    file(("a", "b", "c", "d"), {}, NOW + 900)
    events = store.inbox_events()
    # One episode, one push: members that join later only update it.
    assert [event["kind"] for event in events] == [
        "created", "notified", "updated", "updated"]
    assert set(json.loads(events[2]["detail_json"])["query_keys"]) == {"a", "b", "c"}
    assert set(json.loads(events[3]["detail_json"])["query_keys"]) == {
        "a", "b", "c", "d"}


def test_user_resolved_group_needs_new_episode_or_material_worsening(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    judge = AttentionJudge(store, client=ScriptedJev(), clock=Clock())
    keys = ("orders_list", "orders_count")
    _group_cycle(judge, store, NOW, keys)
    item = store.inbox("new")[0]
    store.action_item(item["id"], "resolve", NOW + 1)
    for cycle in range(1, 5):
        _group_cycle(judge, store, NOW + cycle * 300, keys)
    assert store.inbox("resolved")[0]["reopened_count"] == 0
    assert [event["kind"] for event in store.inbox_events()] == [
        "created", "notified", "resolved"]
    _group_cycle(judge, store, NOW + 1500, keys + ("new_member",))
    # The closed members stay closed; the one new change files on its own.
    [fresh] = store.inbox("new")
    assert fresh["type"] == "latency_regression" and fresh["subject"] == "new_member"


def test_user_resolved_query_ignores_same_regression(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    judge = AttentionJudge(store, client=ScriptedJev(), clock=Clock())
    row = {"query_key": "q", "calls": 300, "mean_ms": 30, "qps": 1,
           "db_time_share": .2, "baseline": {"1h": {"mean_ms": 10}}}
    verdict = {"event_scope": "single", "queries": [
        {"query_key": "q", "kind": "latency_regression", "attention": "notify",
         "severity": .7}], "open_items": []}
    judge._file("db", NOW, [row], {"target_numbers": {}}, verdict)
    item = store.inbox("new")[0]
    store.action_item(item["id"], "resolve", NOW + 1)
    for cycle in range(1, 5):
        reconcile_recovery(store, "db", NOW + cycle * 300, [row])
        judge._file("db", NOW + cycle * 300, [row],
                    {"target_numbers": {}}, verdict)
    assert store.inbox("resolved")[0]["reopened_count"] == 0
    assert [event["kind"] for event in store.inbox_events()] == [
        "created", "notified", "resolved"]


def test_user_resolved_query_reopens_on_measured_worsening(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    judge = AttentionJudge(store, client=ScriptedJev(), clock=Clock())
    row = {"query_key": "q", "calls": 300, "mean_ms": 30, "qps": 1,
           "db_time_share": .2, "baseline": {"1h": {"mean_ms": 10}}}
    verdict = {"event_scope": "single", "queries": [
        {"query_key": "q", "kind": "latency_regression", "attention": "notify",
         "severity": .7}], "open_items": []}
    judge._file("db", NOW, [row], {"target_numbers": {}}, verdict)
    item = store.inbox("new")[0]
    store.action_item(item["id"], "resolve", NOW + 1)
    # 40 ms is the same episode; 60 ms is a further 2x on the filed 30 ms.
    judge._file("db", NOW + 300, [{**row, "mean_ms": 40, "db_time_share": .3}],
                {"target_numbers": {}}, verdict)
    assert store.inbox("resolved")
    judge._file("db", NOW + 600, [{**row, "mean_ms": 60, "db_time_share": .3}],
                {"target_numbers": {}}, verdict)
    assert store.inbox("new")[0]["reopened_count"] == 1
    assert [event["kind"] for event in store.inbox_events()] == [
        "created", "notified", "resolved", "reopened", "notified"]


def test_zero_call_query_cannot_be_filed_as_improved(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    judge = AttentionJudge(store, client=ScriptedJev(), clock=Clock())
    row = {"query_key": "q", "calls": 0, "mean_ms": 0, "qps": 0,
           "db_time_share": 0, "baseline": {"1h": {"mean_ms": 10}}}
    judge._file("db", NOW, [row], {"target_numbers": {}},
                {"event_scope": "single", "queries": [
                    {"query_key": "q", "kind": "query_improved",
                     "attention": "inbox", "severity": .5}], "open_items": []})
    assert store.inbox() == []
    assert store.read("SELECT * FROM attention_episode") == []


def test_user_resolved_group_reopens_after_measured_clear(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    judge = AttentionJudge(store, client=ScriptedJev(), clock=Clock())
    keys = ("orders_list", "orders_count")
    _group_cycle(judge, store, NOW, keys)
    item = store.inbox("new")[0]
    store.action_item(item["id"], "resolve", NOW + 1)
    normal = [{"query_key": key, "calls": 300, "mean_ms": 10, "qps": 1,
               "db_time_share": .2} for key in keys]
    for cycle in range(1, 4):
        reconcile_recovery(store, "db", NOW + cycle * 300, normal)
    assert json.loads(store.inbox("resolved")[0]["body_json"])["condition_cleared"]
    _group_cycle(judge, store, NOW + 1200, keys)
    [burst] = store.inbox("new")
    assert burst["id"] == item["id"] and burst["reopened_count"] == 1
    assert len(json.loads(burst["body_json"])["occurrences"]) == 2


def test_user_resolved_group_reopens_on_material_worsening(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    judge = AttentionJudge(store, client=ScriptedJev(), clock=Clock())
    keys = ("orders_list", "orders_count")
    _group_cycle(judge, store, NOW, keys)
    item = store.inbox("new")[0]
    store.action_item(item["id"], "resolve", NOW + 1)
    rows = [{"query_key": key, "calls": 300, "mean_ms": 60, "qps": 1,
             "db_time_share": .3, "baseline": {"1h": {"mean_ms": 10}}}
            for key in keys]
    verdict = {"event_scope": "database_wide", "queries": [
        {"query_key": key, "kind": "latency_regression", "attention": "notify",
         "severity": .8} for key in keys], "open_items": []}
    judge._file("db", NOW + 300, rows, {"target_numbers": {}}, verdict)
    [burst] = store.inbox("new")
    assert burst["id"] == item["id"]
    assert set(json.loads(burst["body_json"])["query_keys"]) == set(keys)


def test_user_resolved_group_reopens_for_new_kind_on_same_query(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    judge = AttentionJudge(store, client=ScriptedJev(), clock=Clock())
    rows = [{"query_key": key, "calls": 300, "mean_ms": 30, "qps": 3,
             "db_time_share": .2, "baseline": {"1h": {"mean_ms": 10, "qps": 1}}}
            for key in ("steady", "other")]
    def verdict(kind):
        return {"event_scope": "database_wide", "queries": [
            {"query_key": "steady", "kind": kind, "attention": "inbox", "severity": .7},
            {"query_key": "other", "kind": "latency_regression", "attention": "inbox",
             "severity": .6}], "open_items": []}
    judge._file("db", NOW, rows, {"target_numbers": {}}, verdict("traffic_spike"))
    item = store.inbox("new")[0]
    store.action_item(item["id"], "resolve", NOW + 1)
    stopped = [{**rows[0], "calls": 0, "qps": 0, "mean_ms": 0}, rows[1]]
    judge._file("db", NOW + 300, stopped, {"target_numbers": {}},
                verdict("query_stopped_running"))
    # Only "steady" changed in a new way, so it files on its own.
    [fresh] = store.inbox("new")
    assert (fresh["type"], fresh["subject"]) == ("query_stopped_running", "steady")


def test_stopped_load_and_sustained_improvement_settle_without_repeat_news(
        monkeypatch, tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    for index in range(9):
        series(store, f"load_{index}", after_calls=0)
    start = NOW + 1800
    series(store, "improved", now=start, before_ms=100, after_ms=40)
    clock = Clock(start)
    choices = {f"load_{index}": ("ignore", "query_stopped_running", .1)
               for index in range(9)}
    choices.update({"load_0": ("notify", "query_stopped_running", .6),
                    "improved": ("inbox", "query_improved", .5)})
    client = ScriptedJev(choices, scope="database_wide")
    loop = loop_for(monkeypatch, tmp_path, store, clock, client)
    loop.cycle("db")
    assert len(client.requests) == 1
    # An improvement never joins a burst: each files on its own.
    assert {item["type"] for item in store.inbox("new")} == {
        "query_stopped_running", "query_improved"}
    for cycle in range(1, 145):
        clock.now = start + cycle * 600
        store._write(lambda conn: conn.executemany(
            "INSERT OR REPLACE INTO stmt_series (target,query_key,res,bucket_start,calls,total_ms,rows,blks_hit,blks_read) VALUES (?,?,?,?,?,?,?,?,?)",
            [("db", "improved", 60, clock.now - 300 + minute * 60,
              60, 2400, 0, 0, 0) for minute in range(5)]))
        loop.cycle("db")
    # Jev reassesses the open episode every cycle; repeated answers add no news.
    calls = len(client.requests)
    assert calls > 1
    assert {item["resolved_by"] for item in store.inbox("resolved")} == {"code"}
    assert not store.inbox("new")
    # A stopped query and an improvement never push, and repeated answers add no news.
    assert sorted(event["kind"] for event in store.inbox_events()) == [
        "created", "created", "resolved", "resolved"]
    last = store.read("SELECT shortlist_json FROM judge_run ORDER BY id DESC LIMIT 1")[0]
    assert last["shortlist_json"] == "[]"

    clock.now += 600
    store._write(lambda conn: conn.executemany(
        "INSERT OR REPLACE INTO stmt_series (target,query_key,res,bucket_start,calls,total_ms,rows,blks_hit,blks_read) VALUES (?,?,?,?,?,?,?,?,?)",
        [("db", "improved", 60, clock.now - 300 + minute * 60,
          60, 2400, 0, 0, 0) for minute in range(5)]))
    series(store, "fresh_regression", now=clock.now, before_ms=10, after_ms=20)
    client.choices["fresh_regression"] = ("notify", "latency_regression", .8)
    loop.cycle("db")
    assert len(client.requests) == calls + 1
    assert {item["type"] for item in store.inbox("new")} == {"latency_regression"}
    assert len([event for event in store.inbox_events() if event["kind"] == "notified"]) == 1


def test_stopped_query_can_file_again_after_running_resumes(monkeypatch, tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    series(store, "q", after_calls=0)
    clock = Clock(NOW + 1800)
    client = ScriptedJev({"q": ("notify", "query_stopped_running", .6)})
    loop = loop_for(monkeypatch, tmp_path, store, clock, client)
    loop.cycle("db")
    item = store.inbox("new")[0]
    clock.now += 600
    series(store, "q", now=clock.now, before_calls=60, after_calls=60)
    loop.cycle("db")
    assert store.inbox("resolved")[0]["id"] == item["id"]
    assert store.read("SELECT * FROM attention_episode") == []
    resumed = store.read("SELECT shortlist_json FROM judge_run ORDER BY id DESC LIMIT 1")[0]
    assert not any(row["reason"] == "stopped" for row in json.loads(
        resumed["shortlist_json"]))
    clock.now += 2400
    loop.cycle("db")
    assert store.inbox("new")[0]["id"] == item["id"]
    assert store.inbox("new")[0]["reopened_count"] == 1
    assert len(client.requests) == 2
    repeated = store.read("SELECT shortlist_json,response_json FROM judge_run "
                          "ORDER BY id DESC LIMIT 1")[0]
    assert json.loads(repeated["shortlist_json"])[0]["reason"] == "stopped"
    assert json.loads(repeated["response_json"])["queries"][0]["kind"] == (
        "query_stopped_running")


def test_traffic_spike_repeats_after_rate_recovers_despite_improvement(
        monkeypatch, tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    series(store, "q", before_calls=60, before_ms=100,
           after_calls=60, after_ms=40)
    clock = Clock()
    client = ScriptedJev({"q": ("inbox", "query_improved", .5)})
    loop = loop_for(monkeypatch, tmp_path, store, clock, client)
    loop.cycle("db")
    assert store.read("SELECT judged_qps FROM attention_episode")[0]["judged_qps"] == 1
    assert store.inbox("new")[0]["type"] == "query_improved"

    client.choices["q"] = ("notify", "traffic_spike", .7)
    clock.now += 600
    current_series(store, clock.now, {"q": (240, 40)})
    loop.cycle("db")
    spike = next(item for item in store.inbox("new") if item["type"] == "traffic_spike")
    assert "q" in [query["query_key"] for query in client.requests[-1]["queries"]]

    for cycle in range(1, 4):
        clock.now += 600
        current_series(store, clock.now, {"q": (60, 40)})
        loop.cycle("db")
    assert store.read("SELECT state FROM inbox_item WHERE id=?", (spike["id"],)) == [
        {"state": "resolved"}]

    clock.now += 600
    current_series(store, clock.now, {"q": (240, 40)})
    loop.cycle("db")
    reopened = store.read("SELECT state,reopened_count FROM inbox_item WHERE id=?",
                          (spike["id"],))[0]
    assert reopened == {"state": "new", "reopened_count": 1}


def test_improvement_marker_without_saved_rate_does_not_hide_spike(
        monkeypatch, tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    series(store, "q", before_calls=60, before_ms=100,
           after_calls=240, after_ms=40)
    store.record_attention_episodes([("db", "q", "query_improved", 100, NOW - 600, 0)])
    client = ScriptedJev({"q": ("notify", "traffic_spike", .7)})
    loop = loop_for(monkeypatch, tmp_path, store, Clock(), client)
    loop.cycle("db")
    assert store.inbox("new")[0]["type"] == "traffic_spike"


def test_improvement_repeats_after_latency_returns_to_prior_band(monkeypatch, tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    series(store, "q", before_ms=100, after_ms=40)
    clock = Clock()
    client = ScriptedJev({"q": ("inbox", "query_improved", .5)})
    loop = loop_for(monkeypatch, tmp_path, store, clock, client)
    loop.cycle("db")
    item = store.inbox("new")[0]
    for cycle in range(3):
        clock.now += 600
        current_series(store, clock.now, {"q": (60, 100)})
        loop.cycle("db")
    assert store.read("SELECT * FROM attention_episode") == []
    assert store.read("SELECT state FROM inbox_item WHERE id=?", (item["id"],)) == [
        {"state": "resolved"}]

    clock.now += 600
    current_series(store, clock.now, {"q": (60, 40)})
    loop.cycle("db")
    assert store.read("SELECT state,reopened_count FROM inbox_item WHERE id=?",
                      (item["id"],)) == [{"state": "new", "reopened_count": 1}]


def test_new_heavy_group_closes_on_silence_and_repeats(monkeypatch, tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    clock = Clock()
    client = ScriptedJev({key: ("notify", "new_heavy_query", .7)
                          for key in ("a", "b")}, scope="database_wide")
    loop = loop_for(monkeypatch, tmp_path, store, clock, client)
    current_series(store, clock.now, {"a": (60, 100), "b": (60, 100)})
    loop.cycle("db")
    item = store.inbox("new")[0]
    assert item["type"] == "many_queries_changed"
    for cycle in range(3):
        clock.now += 600
        current_series(store, clock.now, {})
        loop.cycle("db")
    assert store.read("SELECT state FROM inbox_item WHERE id=?", (item["id"],)) == [
        {"state": "resolved"}]

    clock.now += 600
    current_series(store, clock.now, {"a": (60, 100), "b": (60, 100)})
    loop.cycle("db")
    # The same queries again: the item reopens with its second occurrence.
    [repeat] = store.inbox("new")
    assert repeat["id"] == item["id"]
    assert len(json.loads(repeat["body_json"])["occurrences"]) == 2
    # Jev reassesses while the episode is open and skips the cycle after it closed.
    assert len(client.requests) == 4


def test_user_resolved_new_heavy_group_reopens_after_silence(monkeypatch, tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    clock = Clock()
    client = ScriptedJev({key: ("notify", "new_heavy_query", .7)
                          for key in ("a", "b")}, scope="database_wide")
    loop = loop_for(monkeypatch, tmp_path, store, clock, client)
    current_series(store, clock.now, {"a": (60, 100), "b": (60, 100)})
    loop.cycle("db")
    item = store.inbox("new")[0]
    store.action_item(item["id"], "resolve", clock.now + 1)
    for cycle in range(3):
        clock.now += 600
        current_series(store, clock.now, {})
        loop.cycle("db")
    body = json.loads(store.read("SELECT body_json FROM inbox_item WHERE id=?",
                                 (item["id"],))[0]["body_json"])
    assert body["condition_cleared"]

    clock.now += 600
    current_series(store, clock.now, {"a": (60, 100), "b": (60, 100)})
    loop.cycle("db")
    [repeat] = store.inbox("new")
    assert repeat["id"] == item["id"]
    clock.now += 600
    loop.cycle("db")
    assert [row["id"] for row in store.inbox("new")] == [repeat["id"]]
    # No open item while it was resolved, so the silent cycles cost no calls.
    assert len(client.requests) == 3


def test_group_materially_worse_notifies_once(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    judge = AttentionJudge(store, client=ScriptedJev(), clock=Clock())
    keys = ("orders_list", "orders_count")
    _group_cycle(judge, store, NOW, keys)
    # Filed at 30 ms; 60 ms is a further 2x, a new episode that notifies once.
    rows = [{"query_key": key, "calls": 300, "mean_ms": 60, "qps": 1,
             "db_time_share": .3, "baseline": {"1h": {"mean_ms": 10}}}
            for key in keys]
    verdict = {"event_scope": "database_wide", "queries": [
        {"query_key": key, "kind": "latency_regression", "attention": "notify",
         "severity": .8} for key in keys], "open_items": []}
    for cycle in (1, 2):
        judge._file("db", NOW + cycle * 300, rows,
                    {"target_numbers": {"total_qps": 2}}, verdict)
    assert [event["kind"] for event in store.inbox_events()] == [
        "created", "notified", "worse", "notified"]


def test_group_records_new_condition_for_existing_member(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    judge = AttentionJudge(store, client=ScriptedJev(), clock=Clock())
    rows = [{"query_key": "steady", "calls": 300, "mean_ms": 10, "qps": 3,
             "db_time_share": .2, "baseline": {"1h": {"mean_ms": 10, "qps": 1}}},
            {"query_key": "other", "calls": 300, "mean_ms": 30, "qps": 1,
             "db_time_share": .2, "baseline": {"1h": {"mean_ms": 10, "qps": 1}}}]
    def verdict(kind):
        return {"event_scope": "database_wide", "queries": [
            {"query_key": "steady", "kind": kind, "attention": "inbox", "severity": .7},
            {"query_key": "other", "kind": "latency_regression", "attention": "inbox",
             "severity": .5}], "open_items": []}
    judge._file("db", NOW, rows, {"target_numbers": {}}, verdict("traffic_spike"))
    item = store.inbox("new")[0]
    stopped = [{**rows[0], "calls": 0, "qps": 0, "mean_ms": 0}, rows[1]]
    judge._file("db", NOW + 300, stopped, {"target_numbers": {}},
                verdict("query_stopped_running"))
    body = json.loads(store.inbox("new")[0]["body_json"])
    assert body["recovery_queries"]["steady"]["kind"] == "query_stopped_running"
    # Inbox-level answers list the episode without a push.
    assert [event["kind"] for event in store.inbox_events() if event["item_id"] == item["id"]] == [
        "created", "updated"]
    reconcile_recovery(store, "db", NOW + 87000, stopped)
    body = json.loads(store.inbox("new")[0]["body_json"])
    assert "steady" in body["recovered_queries"]
    store.action_item(item["id"], "resolve", NOW + 87001)
    reconcile_recovery(store, "db", NOW + 87300, rows)
    body = json.loads(store.inbox("resolved")[0]["body_json"])
    assert body["recovered_queries"]["steady"]["resumed_at"] == NOW + 87300
    judge._file("db", NOW + 87600, stopped, {"target_numbers": {}},
                verdict("query_stopped_running"))
    # "steady" stopped again after running; "other" is the same episode the user closed.
    [fresh] = store.inbox("new")
    assert (fresh["type"], fresh["subject"]) == ("query_stopped_running", "steady")


def test_retired_spike_stops_later_as_its_own_item(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    judge = AttentionJudge(store, client=ScriptedJev(), clock=Clock())
    spike = {"query_key": "steady", "calls": 300, "mean_ms": 10, "qps": 3,
             "db_time_share": .2, "baseline": {"1h": {"mean_ms": 10, "qps": 1}}}
    other = {"query_key": "other", "calls": 300, "mean_ms": 30, "qps": 1,
             "db_time_share": .2, "baseline": {"1h": {"mean_ms": 10, "qps": 1}}}
    def file(kind, ts):
        rows = [spike if kind == "traffic_spike" else
                {**spike, "calls": 0, "mean_ms": 0, "qps": 0,
                 "db_time_share": 0}, other]
        verdict = {"event_scope": "database_wide", "queries": [
            {"query_key": "steady", "kind": kind, "attention": "notify", "severity": .6},
            {"query_key": "other", "kind": "latency_regression",
             "attention": "inbox", "severity": .5}], "open_items": []}
        judge._file("db", ts, rows, {"target_numbers": {}}, verdict)
        return rows

    file("traffic_spike", NOW)
    stopped = [{**spike, "calls": 0, "mean_ms": 0, "qps": 0,
                "db_time_share": 0}, other]
    for cycle in range(1, 4):
        reconcile_recovery(store, "db", NOW + cycle * 300, stopped)
    body = json.loads(store.inbox("new")[0]["body_json"])
    assert "steady" in body["recovered_queries"]
    assert body["query_keys"] == ["other"]

    file("query_stopped_running", NOW + 1200)
    # Twenty minutes on, the stop is a change of its own: "other" stays in its burst.
    [stopped_item] = [row for row in store.inbox("new") if row["subject"] == "steady"]
    assert stopped_item["type"] == "query_stopped_running"
    events = store.inbox_events()
    # A stopped query is worth a look, not a push.
    assert not [event for event in events if event["item_id"] == stopped_item["id"]
                and event["kind"] == "notified"]
    file("query_stopped_running", NOW + 1500)
    assert len(store.inbox_events()) == len(events)


def test_mixed_database_wide_verdict_recovers_by_member_kind(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    judge = AttentionJudge(store, client=ScriptedJev(), clock=Clock())
    kinds = {"slow": "latency_regression", "spike": "traffic_spike",
             "stopped": "query_stopped_running", "improved": "query_improved"}
    filing = [{"query_key": key, "calls": 0 if key == "stopped" else 300,
               "mean_ms": 5 if key == "improved" else 30,
               "qps": 3 if key == "spike" else 1,
               "db_time_share": .2,
               "baseline": {"1h": {"mean_ms": 10, "qps": 1}}}
              for key in kinds]
    verdict = {"event_scope": "database_wide", "queries": [
        {"query_key": key, "kind": kind, "attention": "notify", "severity": .8}
        for key, kind in kinds.items()], "open_items": []}
    judge._file("db", NOW, filing, {"target_numbers": {}}, verdict)
    [group] = [item for item in store.inbox("new") if item["type"] == "many_queries_changed"]
    body = json.loads(group["body_json"])
    # The improvement is a digest finding of its own, never a burst member.
    assert set(body["recovery_queries"]) == set(kinds) - {"improved"}
    normal = [{**row, "calls": 300, "mean_ms": 10, "qps": 1} for row in filing]
    for cycle in range(1, 4):
        closed = reconcile_recovery(store, "db", NOW + cycle * 300, normal)
    assert GROUP_CLOSED <= closed_kinds(closed)
    assert store.inbox("resolved")[0]["resolved_by"] == "code"


def test_ps18_cycle3_new_heavy_files_despite_database_verdict_and_open_group(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    judge = AttentionJudge(store, client=ScriptedJev(), clock=Clock())
    prior = [{"query_key": key, "calls": 300, "mean_ms": 3, "qps": 1,
              "db_time_share": .2, "baseline": {"1h": {"mean_ms": 1}}}
             for key in ("10d494e97296", "c58ed20d30d1")]
    judge._file("db", NOW - 600, prior, {"target_numbers": {}},
                {"event_scope": "database_wide", "queries": [
                    {"query_key": row["query_key"], "kind": "latency_regression",
                     "attention": "inbox", "severity": .7} for row in prior],
                 "open_items": []})
    group = next(item for item in store.inbox("new")
                 if item["type"] == "many_queries_changed")
    assert json.loads(group["body_json"])["recovery_queries"]

    def write_database(conn):
        conn.executemany(
            "INSERT OR REPLACE INTO db_series "
            "(target,res,bucket_start,calls,total_ms,active_conns,max_conns,"
            "cache_hit_ratio,xact_commit,xact_rollback,deadlocks) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [("db", 60, NOW - 3900 + minute * 60, 0, 0, 9, 100, 1, 0, 5, 0)
             for minute in range(75)])
    store._write(write_database)

    def replay(ts, calls, share):
        rows = [{"query_key": "ffea7ed4084b", "calls": calls, "mean_ms": 11.1,
                 "qps": calls / 300, "db_time_share": share,
                 "baseline": {"1h": None, "1d": None, "7d": None}},
                {"query_key": "081043271dee", "calls": 300, "mean_ms": .3,
                 "qps": 1, "db_time_share": .18,
                 "baseline": {"1h": {"mean_ms": .3, "qps": 1}}},
                {"query_key": "07e20a27f40c", "calls": 300, "mean_ms": .2,
                 "qps": 1, "db_time_share": .04,
                 "baseline": {"1h": {"mean_ms": .2, "qps": 1}}},
                {"query_key": "32260ee331d5", "calls": 300, "mean_ms": .02,
                 "qps": 1, "db_time_share": .01,
                 "baseline": {"1h": {"mean_ms": .02, "qps": 1}}},
                {"query_key": "9f049f8e8d36", "calls": 300, "mean_ms": .02,
                 "qps": 1, "db_time_share": .01,
                 "baseline": {"1h": {"mean_ms": .02, "qps": 1}}}]
        verdict = {"event_scope": "database_wide", "queries": [
            {"query_key": rows[0]["query_key"], "kind": "new_heavy_query",
             "attention": "notify", "severity": .8},
            {"query_key": rows[1]["query_key"], "kind": "rollback_or_deadlock_spike",
             "attention": "notify", "severity": .7},
            {"query_key": rows[2]["query_key"], "kind": "traffic_spike",
             "attention": "inbox", "severity": .5},
            {"query_key": rows[3]["query_key"], "kind": "query_improved",
             "attention": "inbox", "severity": .4},
            {"query_key": rows[4]["query_key"], "kind": "query_improved",
             "attention": "inbox", "severity": .4}], "open_items": []}
        judge._file("db", ts, rows,
                    {"target_numbers": {"rollbacks": 5, "deadlocks": 0}}, verdict)

    replay(NOW, 119, .625842)
    heavy = next(item for item in store.inbox("new")
                 if item["type"] == "new_heavy_query")
    assert heavy["subject"] == "ffea7ed4084b"
    # A new query waits for a second check in the top 10 before it pushes.
    assert [(event["kind"], event["ts"]) for event in store.inbox_events()
            if event["item_id"] == heavy["id"]] == [("created", NOW)]
    replay(NOW + 600, 96, .578773)
    assert len([event for event in store.inbox_events()
                if event["item_id"] == heavy["id"] and event["kind"] == "notified"]) == 1
    group_body = json.loads(store.read("SELECT body_json FROM inbox_item WHERE id=?",
                                       (group["id"],))[0]["body_json"])
    assert "ffea7ed4084b" not in group_body["episode_query_keys"]
    assert not [item for item in store.inbox("new")
                if item["type"] == "rollback_or_deadlock_spike"]


def test_rollback_spike_is_one_database_item_with_measured_support(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    judge = AttentionJudge(store, client=ScriptedJev(), clock=Clock())

    def record(recent_rollbacks):
        def write(conn):
            conn.executemany(
                "INSERT OR REPLACE INTO db_series "
                "(target,res,bucket_start,calls,total_ms,active_conns,max_conns,"
                "cache_hit_ratio,xact_commit,xact_rollback,deadlocks) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [("db", 60, NOW - 3900 + minute * 60, 0, 0, 2, 20,
                  1, 0, recent_rollbacks if minute >= 60 else 5, 0)
                 for minute in range(65)])
        store._write(write)

    rows = [{"query_key": key, "calls": 300, "mean_ms": 10, "qps": 1,
             "db_time_share": .01} for key in ("a", "b")]
    verdict = {"event_scope": "database_wide", "queries": [
        {"query_key": key, "kind": "rollback_or_deadlock_spike",
         "attention": attention, "severity": .7}
        for key, attention in (("a", "inbox"), ("b", "notify"))], "open_items": []}
    request = {"target_numbers": {"rollbacks": 5, "deadlocks": 0}}
    record(5)
    judge._file("db", NOW, rows, request, verdict)
    assert store.inbox("new") == []
    record(20)
    request["target_numbers"]["rollbacks"] = 20
    judge._file("db", NOW, rows, request, verdict)
    judge._file("db", NOW, rows, request, verdict)
    items = store.inbox("new")
    assert [(item["type"], item["subject"]) for item in items] == [
        ("rollback_or_deadlock_spike", "database")]
    assert [event["kind"] for event in store.inbox_events()] == [
        "created", "notified"]


@pytest.mark.parametrize("count", [1, 2])
def test_database_wide_scope_groups_only_multiple_qualifying_queries(
        monkeypatch, tmp_path, count):
    store = MonitorStore(tmp_path / "monitor.db")
    many_slowdowns(store)
    choices = {f"slow_{index}": ("notify", "latency_regression", .8)
               for index in range(count)}
    client = ScriptedJev(choices, scope="database_wide")
    loop_for(monkeypatch, tmp_path, store, Clock(), client).cycle("db")
    items = store.inbox("new")
    assert len(items) == 1
    if count == 1:
        assert items[0]["type"] == "latency_regression"
        assert items[0]["subject"] == "slow_0"
        assert json.loads(items[0]["body_json"])["event_scope"] == "database_wide"
    else:
        assert items[0]["type"] == "many_queries_changed"
        assert set(json.loads(items[0]["body_json"])["query_keys"]) == {
            "slow_0", "slow_1"}


def test_database_wide_scope_needs_distinct_qualifying_keys():
    answers = [{"query_key": "q", "attention": "notify"},
               {"query_key": "q", "attention": "inbox"}]
    assert group_many(answers, "database_wide") == answers


def test_five_minute_table_slowdown_reaches_jev_at_next_production_cycle(
        monkeypatch, tmp_path):
    monkeypatch.delenv("RDST_MONITOR_JUDGE_INTERVAL_S", raising=False)
    store = MonitorStore(tmp_path / "monitor.db")
    series(store, "orders_status", before_ms=10, after_ms=14,
           sql="SELECT count(*) FROM orders WHERE status = $1")
    series(store, "orders_total", before_ms=10, after_ms=14,
           sql="SELECT sum(total) FROM orders WHERE status = $1")
    client = ScriptedJev({key: ("notify", "table_slowdown", .8)
                          for key in ("orders_status", "orders_total")},
                         scope="database_wide")
    # The incident begins just after the prior 5-minute judge and has ended
    # by this cycle. Its completed statement buckets must still be shortlisted.
    loop = loop_for(monkeypatch, tmp_path, store, Clock(NOW + 1), client)
    assert loop.interval == 300
    loop.cycle("db")
    queries = client.requests[0]["queries"]
    assert {query["query_key"] for query in queries} == {"orders_status", "orders_total"}
    assert all(query["tables"] == ["orders"] for query in queries)
    assert all(query["current_5m"]["mean_ms"] >= 14 for query in queries)
    assert all(query["baseline"]["1h"]["mean_ms"] < 11 for query in queries)
    item = store.inbox("new")[0]
    assert item["type"] == "many_queries_changed"
    assert set(json.loads(item["body_json"])["query_keys"]) == {
        "orders_status", "orders_total"}


def test_rare_threefold_change_is_ignored_heavy_1_4x_is_sent(monkeypatch, tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    series(store, "heavy", before_calls=100, after_calls=100,
           before_ms=10, after_ms=14)
    def write(conn):
        conn.execute("INSERT INTO query_text VALUES (?,?,?,?,?)",
                     ("db", "rare", "SELECT * FROM rare_orders", NOW - 90000, NOW))
        conn.execute("INSERT INTO stmt_series (target,query_key,res,bucket_start,calls,total_ms,rows,blks_hit,blks_read) VALUES (?,?,?,?,?,?,?,?,?)",
                     ("db", "rare", 300, NOW - 86400 - 300, 2, 20, 0, 0, 0))
        conn.execute("INSERT INTO stmt_series (target,query_key,res,bucket_start,calls,total_ms,rows,blks_hit,blks_read) VALUES (?,?,?,?,?,?,?,?,?)",
                     ("db", "rare", 60, NOW - 60, 1, 30, 0, 0, 0))
    store._write(write)
    clock = Clock()
    client = ScriptedJev({"heavy": ("notify", "latency_regression", 0.8),
                          "rare": ("notify", "latency_regression", 0.8)})
    loop_for(monkeypatch, tmp_path, store, clock, client).cycle("db")
    assert [query["query_key"] for query in client.requests[0]["queries"]] == ["heavy"]
    assert client.requests[0]["queries"][0]["db_time_share"] > 0.30
    assert [(item["type"], item["subject"]) for item in store.inbox("new")] == [
        ("latency_regression", "heavy")]


def test_code_owned_open_item_is_context_not_jev_control(monkeypatch, tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    series(store, "q", before_calls=100, after_calls=100,
           before_ms=10, after_ms=14)
    store.upsert_item("db", "monitoring_issue", "password_missing", 1.0,
                      "Monitoring password missing", {"connections": 4}, NOW - 100)
    client = ScriptedJev({"q": ("ignore", "latency_regression", 0.1)})
    client.status = "resolved"
    loop_for(monkeypatch, tmp_path, store, Clock(), client).cycle("db")
    supplied = client.requests[0]["open_items"][0]
    assert supplied["type"] == "monitoring_issue"
    assert supplied["at_filing"] == {"connections": 4.0}
    assert supplied["now"] == {"connections": 2.0}
    assert store.inbox("new")[0]["subject"] == "password_missing"


def test_slow_creep_is_digest_and_closes_on_measurements_not_jev(monkeypatch, tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    series(store, "q", before_calls=100, after_calls=100, before_ms=10, after_ms=14)
    clock = Clock()
    client = ScriptedJev({"q": ("notify", "slow_creep", 0.7)})
    loop = loop_for(monkeypatch, tmp_path, store, clock, client)
    loop.cycle("db")
    item = store.inbox("new")[0]
    assert (item["type"], item["delivery"]) == ("slow_creep", "digest")
    assert not [event for event in store.inbox_events() if event["kind"] == "notified"]
    client.status = "resolved"
    for index in range(1, 4):
        clock.now = NOW + index * 300
        for minute in range(5):
            store.record("db", clock.now - 300 + minute * 60,
                         [{"query_key": "q", "calls": 100, "total_ms": 100 * 10}],
                         {}, [], None)
        loop.cycle("db")
    resolved = store.inbox("resolved")[0]
    # Jev saying "resolved" never closes an item; three cycles back in band do.
    assert (resolved["id"], resolved["resolved_by"]) == (item["id"], "code")
    assert json.loads(resolved["body_json"])["resolution"]["after"]["mean_ms"] == 10


def test_cap_dry_run_login_and_retry_after(monkeypatch, tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    series(store, "q", before_calls=100, after_calls=100, before_ms=10, after_ms=14)
    clock = Clock()
    client = ScriptedJev({"q": ("notify", "latency_regression", 0.8)},
                          failures=[JevClientError("LOGIN_REQUIRED", 401, 600),
                                    JevClientError("LOGIN_REQUIRED", 401, 300), None])

    def next_cycle(step):
        clock.now += step
        series(store, "q", now=clock.now, before_calls=100, after_calls=100,
               before_ms=10, after_ms=14)
        loop.cycle("db")

    loop = loop_for(monkeypatch, tmp_path, store, clock, client)
    loop.cycle("db")
    # One refused call is not a sign-in problem yet.
    assert not [item for item in store.inbox() if item["subject"] == "jev_login"]
    next_cycle(300)
    assert len(client.requests) == 1
    next_cycle(300)
    assert len(client.requests) == 2
    assert store.inbox("new")[0]["subject"] == "jev_login"
    monkeypatch.setenv("RDST_MONITOR_JUDGE_DRY_RUN", "1")
    next_cycle(300)
    assert len(client.requests) == 3
    assert store.inbox("resolved")[0]["subject"] == "jev_login"
    assert not store.inbox("new")
    assert store.read("SELECT outcome FROM judge_run ORDER BY id") == [
        {"outcome": "error"}, {"outcome": "backoff"}, {"outcome": "error"},
        {"outcome": "dry_run"}]
    monkeypatch.setenv("RDST_MONITOR_JEV_DAILY_CAP", "3")
    clock.now += 300
    for minute in range(5):
        store.record("db", clock.now - 300 + minute * 60,
                     [{"query_key": "q", "calls": 100, "total_ms": 3000}],
                     {}, [], None)
    loop.cycle("db")
    assert len(client.requests) == 3
    assert store.read("SELECT outcome FROM judge_run ORDER BY id DESC LIMIT 1")[0]["outcome"] == "daily_cap"


def test_judge_run_records_billable_call(monkeypatch, tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    series(store, "q", before_calls=100, after_calls=100, before_ms=10, after_ms=14)

    class BillingJev(ScriptedJev):
        def attention(self, **request):
            record_billable_call()
            return super().attention(**request)

    loop_for(monkeypatch, tmp_path, store, Clock(), BillingJev()).cycle("db")
    assert store.read("SELECT billable_calls FROM judge_run") == [{"billable_calls": 1}]


def test_dismissed_item_stays_silent_while_the_episode_continues(monkeypatch, tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    series(store, "q", before_calls=100, after_calls=100,
           before_ms=10, after_ms=14)
    clock = Clock()
    client = ScriptedJev({"q": ("notify", "latency_regression", 0.8)})
    loop_for(monkeypatch, tmp_path, store, clock, client).cycle("db")
    item = store.inbox("new")[0]
    store.action_item(item["id"], "dismiss", NOW + 1)
    chosen = json.loads(store.read("SELECT shortlist_json FROM judge_run")[0]["shortlist_json"])
    clock.now = NOW + 7 * 86400 + 2
    AttentionJudge(store, client=client, clock=clock)(chosen, {"target": "db"})
    # Reassessed, but dismissed + still happening = no notification.
    assert len(client.requests) == 2
    assert store.inbox("dismissed")[0]["id"] == item["id"]
    assert len([event for event in store.inbox_events() if event["kind"] == "notified"]) == 1


@pytest.mark.parametrize("failure,delay", [
    (JevClientError("JEV_RATE_LIMITED", 429, 900), 900),
    (ConnectionError("offline"), 60),
])
def test_judge_errors_do_not_file_and_back_off(monkeypatch, tmp_path, failure, delay):
    # A used-up Jev allowance files an item instead (test_monitor_database_limit.py).
    store = MonitorStore(tmp_path / "monitor.db")
    series(store, "q", before_calls=100, after_calls=100,
           before_ms=10, after_ms=14)
    clock = Clock()
    client = ScriptedJev(failures=[failure])
    loop = loop_for(monkeypatch, tmp_path, store, clock, client)
    loop.cycle("db")
    assert not store.inbox("new")
    recorded = store.read("SELECT outcome,response_json,billable_calls FROM judge_run")[0]
    assert recorded["outcome"] == "error"
    assert recorded["billable_calls"] == 0
    assert json.loads(recorded["response_json"])["retry_after"] == delay
    clock.now += min(300, delay - 1)
    series(store, "q", now=clock.now, before_calls=100, after_calls=100,
           before_ms=10, after_ms=14)
    loop.cycle("db")
    assert len(client.requests) == 1
    assert store.read("SELECT outcome FROM judge_run ORDER BY id DESC LIMIT 1")[0]["outcome"] == "backoff"


def test_judge_request_limits_inbox_history_reads(tmp_path, monkeypatch):
    from features.monitor.judge import _request

    store = MonitorStore(tmp_path / "monitor.db")
    for index in range(50):
        store.upsert_item("db", "traffic_spike", f"q{index}", .5,
                          "Traffic spike", {}, 100, notify=False)
    reads = []
    original_read = store.read

    def tracked_read(sql, params=()):
        if "FROM inbox_event WHERE item_id" in sql:
            reads.append(params)
        return original_read(sql, params)

    monkeypatch.setattr(store, "read", tracked_read)
    request = _request(store, "db", 100, [], {
        "inbox": store.inbox(target="db"), "measured": [], "database": {}})
    assert len(request["open_items"]) == 30
    assert len(reads) <= 30


@pytest.mark.parametrize("repeat_ms", [30, 60])
def test_production_spaced_recovery_and_repeat_presentation(tmp_path, repeat_ms):
    from features.monitor.presentation import present_inbox_item, settle_status

    store = MonitorStore(tmp_path / "monitor.db")
    clock = Clock()
    judge = AttentionJudge(store, client=ScriptedJev(), clock=clock)
    keys = ("orders_list", "orders_count")
    _group_cycle(judge, store, clock(), keys)
    [original] = store.inbox("new")
    normal = [{"query_key": key, "calls": 300, "mean_ms": 10, "qps": 1,
               "db_time_share": .2} for key in keys]
    for cycle in range(1, 4):
        clock.now = NOW + cycle * 300
        reconcile_recovery(store, "db", clock(), normal)
        # Replaying a reading within the same production window cannot advance recovery.
        reconcile_recovery(store, "db", clock() + 1, normal)
        [item] = store.inbox(target="db")
        assert (item["state"] == "resolved") == (cycle == 3)
    body = json.loads(item["body_json"])
    presented = settle_status(present_inbox_item(item, {}, [], set(),
        texts={key: "SELECT * FROM orders" for key in keys}), body, None)
    assert item["resolved_by"] == "code"
    assert presented["severity_band"] == "recovered"
    assert presented["status"] == "back_to_normal"
    assert presented["band_reason"].startswith("Back to normal:")
    assert item["resolved_at"] == NOW + 900
    assert presented["status_at"] == item["resolved_at"]
    assert all(entry["retired_at"] == NOW + 900
               for entry in body["recovered_queries"].values())
    assert len([e for e in store.inbox_events() if e["kind"] == "notified"]) == 1

    clock.now += 300
    rows = [{"query_key": key, "calls": 300, "mean_ms": repeat_ms, "qps": 1,
             "db_time_share": .2, "baseline": {"1h": {"mean_ms": 10}}} for key in keys]
    verdict = {"event_scope": "database_wide", "queries": [
        {"query_key": key, "kind": "latency_regression", "attention": "notify",
         "severity": .8} for key in keys], "open_items": []}
    for _ in range(2):
        judge._file("db", clock(), rows, {"target_numbers": {}}, verdict)
    [item] = store.inbox("new")
    body = json.loads(item["body_json"])
    presented = settle_status(present_inbox_item(item, {}, [], set(),
        texts={key: "SELECT * FROM orders" for key in keys}), body, None)
    assert item["id"] == original["id"]
    assert item["reopened_count"] == 1
    assert presented["severity_band"] == "act_now"
    assert presented["status"] != "back_to_normal"
    assert len(presented["occurrences"]) == 2
    assert presented["occurrences"][0]["start"] == clock()
    assert presented["occurrences"][0]["end"] is None
    assert len([e for e in store.inbox_events() if e["kind"] == "notified"]) == 2
