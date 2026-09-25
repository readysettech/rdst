from types import SimpleNamespace
import asyncio
import threading
import selectors

from features.monitor import loop as monitor_loop
from shared.monitor.monitor_store import MonitorStore


def test_loop_files_code_rules_closes_when_clear_and_records_dry_run(monkeypatch, tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    now = 100_000
    store.record("db", now - 60, [{"query_key": "q", "calls": 2, "total_ms": 20}],
                 {}, [{"pid": 12, "query_key": "q", "duration_s": 90, "state": "active"}],
                 {"calls": 2, "total_ms": 20, "active_conns": 9, "max_conns": 10})
    store.sync_stuck_items("db", now - 60, [{"query_key": "q"}])
    class Config:
        def load(self):
            pass
        def get(self, target):
            return {}
    monkeypatch.setattr("shared.config.targets.TargetsConfig", Config)
    monkeypatch.setattr(monitor_loop.query_discovery, "collector_for",
                        lambda target: SimpleNamespace(_snapshot={}))
    active = monitor_loop.MonitorLoop(store, clock=lambda: now,
                                      index_reader=lambda target: [], interval=300,
                                      judge=lambda chosen, context: store.record_judge_run(
                                          context["target"], int(active.clock()), chosen))
    active.cycle("db")
    assert {item["type"] for item in store.inbox("new")} == {
        "stuck_query", "connections_near_max"}
    assert store.read("SELECT dry_run,outcome FROM judge_run") == [{"dry_run": 1, "outcome": "noop"}]
    store.sync_stuck_items("db", now + 60, [])
    active.clock = lambda: now + 360
    active.cycle("db")
    assert {item["type"] for item in store.inbox("resolved")} == {
        "stuck_query", "connections_near_max"}


def test_in_flight_only_stuck_query_reaches_shortlist(monkeypatch, tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    store.record("db", 940, [], {"stuck": "SELECT pg_sleep(180) FROM orders"},
                 [{"pid": 7, "query_key": "stuck", "duration_s": 75,
                   "state": "active"}])
    monkeypatch.setattr(monitor_loop.MonitorLoop, "_monitoring_issues", lambda *args: None)
    chosen = []
    loop = monitor_loop.MonitorLoop(store, clock=lambda: 1000,
                                    index_reader=lambda target: [],
                                    judge=lambda rows, context: chosen.extend(rows))
    loop.cycle("db")
    assert [(row["query_key"], row["reason"]) for row in chosen] == [
        ("stuck", "stuck")]


def test_loop_files_and_closes_connection_issue(monkeypatch, tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    class Config:
        def load(self):
            pass
        def get(self, target):
            return {}
    monkeypatch.setattr("shared.config.targets.TargetsConfig", Config)
    monkeypatch.setattr(monitor_loop.query_discovery, "collector_for",
                        lambda target: SimpleNamespace(_snapshot={}))
    def failed(target):
        raise ConnectionError("lost")
    now = [1000]
    active = monitor_loop.MonitorLoop(store, clock=lambda: now[0], index_reader=failed,
                                      judge=lambda chosen, context: None)
    active.cycle("db")
    # One failed check is a blip; the second one files it.
    assert store.inbox("new") == []
    now[0] = 1300
    active.cycle("db")
    assert store.inbox("new")[0]["subject"] == "lost_connection"
    active.index_reader = lambda target: []
    now[0] = 1600
    active.cycle("db")
    assert store.inbox("resolved")[0]["subject"] == "lost_connection"


def test_scheduler_and_capture_failures_become_monitoring_issues(monkeypatch, tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    class Config:
        def load(self):
            pass
        def get(self, target):
            return {"password_env": "RDST_MONITOR_TEST_PASSWORD"}
    monkeypatch.delenv("RDST_MONITOR_TEST_PASSWORD", raising=False)
    monkeypatch.setattr("shared.config.targets.TargetsConfig", Config)
    collector = SimpleNamespace(_snapshot={"capture_warning": {
        "source": "pg_stat_statements"}})
    monkeypatch.setattr(monitor_loop.query_discovery, "collector_for", lambda target: collector)
    schedule = SimpleNamespace(consecutive_failures=1)
    scheduler = SimpleNamespace(_schedules={"db": schedule})
    active = monitor_loop.MonitorLoop(store, clock=lambda: 1000,
                                      index_reader=lambda target: [], scheduler=scheduler,
                                      judge=lambda chosen, context: None)
    active.cycle("db")
    assert {item["subject"] for item in store.inbox("new")} == {
        "password_missing", "pg_stat_statements_missing"}
    schedule.consecutive_failures = 2
    active.clock = lambda: 1060
    active.cycle("db")
    # A missing password has its own item, so failures don't also file a lost connection.
    assert {item["subject"] for item in store.inbox("new")} == {
        "password_missing", "pg_stat_statements_missing"}
    monkeypatch.setenv("RDST_MONITOR_TEST_PASSWORD", "present")
    for clock in (1120, 1180):
        active.clock = lambda clock=clock: clock
        active.cycle("db")
    assert {item["subject"] for item in store.inbox("new")} == {
        "lost_connection", "pg_stat_statements_missing"}
    schedule.consecutive_failures = 0
    collector._snapshot = {"state": "watching"}
    active.clock = lambda: 1300
    active.cycle("db")
    assert len(store.inbox("resolved")) == 3


async def test_process_loop_is_reused_and_stops(monkeypatch, tmp_path):
    monkeypatch.setenv("RDST_DATA_DIR", str(tmp_path))
    first = await monitor_loop.start_monitor_loop()
    second = await monitor_loop.start_monitor_loop()
    assert first is second
    await first.stop()
    assert first._task is None
    assert first._connection_task is None


async def test_connection_deadline_runs_without_an_attention_cycle(monkeypatch, tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    store.set_settings("db", {"enabled": True})
    now = [1000]
    active = monitor_loop.MonitorLoop(store, clock=lambda: now[0], interval=300)
    active.watch.collection("db", False)
    async def advance(seconds):
        if now[0] == 1000:
            assert store.inbox() == []
            now[0] += 120
        else:
            active._stop.set()
    monkeypatch.setattr(monitor_loop.asyncio, "sleep", advance)
    await active._run_connections()
    assert store.inbox()[0]["subject"] == "lost_connection"
    assert store.inbox()[0]["created_at"] == 1120


def test_wired_loop_does_not_reobserve_a_failure_after_collection_recovers(monkeypatch, tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    store.set_settings("db", {"enabled": True})
    monkeypatch.setattr(monitor_loop.query_discovery, "collector_for",
                        lambda target: SimpleNamespace(_snapshot={}))
    now = [1000]
    scheduler = SimpleNamespace(set_outcome_listener=lambda listener: None)
    active = monitor_loop.MonitorLoop(store, clock=lambda: now[0], scheduler=scheduler)
    active.watch.collection("db", False)
    now[0] += 10
    active.watch.collection("db", True)
    active._monitoring_issues("db", 1000, True)
    active._monitoring_issues("db", 1000, True)
    now[0] += 120
    active.watch.tick()
    assert store.inbox() == []


async def test_blocked_reader_cannot_write_after_stop_or_overlap_restart(monkeypatch, tmp_path):
    original_select = selectors.EpollSelector.select
    monkeypatch.setattr(selectors.EpollSelector, "select",
                        lambda self, timeout=None: original_select(
                            self, 0.01 if timeout is None else min(timeout, 0.01)))
    store = MonitorStore(tmp_path / "monitor.db")
    store.set_settings("db", {"enabled": True})
    entered = threading.Event()
    release = threading.Event()
    restarted = threading.Event()

    def reader(_target):
        entered.set()
        release.wait(30)
        return [{"schema": "public", "table": "t", "index_name": "idx",
                 "definition": "CREATE INDEX idx ON t (id)"}]

    old = monitor_loop.MonitorLoop(store, index_reader=reader, interval=300)
    await old.start()
    assert await asyncio.to_thread(entered.wait, 2)
    stopping = asyncio.create_task(old.stop())
    await asyncio.sleep(0)
    new = monitor_loop.MonitorLoop(
        store, index_reader=lambda _target: restarted.set() or [], interval=300)
    await new.start()
    await asyncio.sleep(0.05)
    assert not restarted.is_set()
    release.set()
    await stopping
    assert not store.read("SELECT * FROM index_snapshot WHERE target='db'")
    assert not store.read("SELECT * FROM index_change WHERE target='db'")
    assert await asyncio.to_thread(restarted.wait, 2)
    await new.stop()


async def test_locked_store_skips_a_pass_without_ending_the_loop(monkeypatch, tmp_path):
    import sqlite3

    monkeypatch.setattr(monitor_loop, "TARGETS_RETRY_S", 0.01)
    store = MonitorStore(tmp_path / "monitor.db")
    store.set_settings("db", {"enabled": True})
    calls = {"n": 0}
    original = store.monitored_targets

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return original()

    store.monitored_targets = flaky
    cycled = threading.Event()
    active = monitor_loop.MonitorLoop(store, judge=lambda *args: None, interval=3600,
                                      assessments=lambda targets: None)
    active.cycle = lambda target: cycled.set()
    await active.start()
    assert await asyncio.to_thread(cycled.wait, 2)
    assert not active._task.done()
    await active.stop()


def test_mysql_performance_schema_gap_is_a_monitoring_issue(monkeypatch, tmp_path):
    from features.monitor.presentation import current_title, inbox_advice, inbox_link

    store = MonitorStore(tmp_path / "monitor.db")

    class Config:
        def load(self):
            pass

        def get(self, target):
            return {"engine": "mysql"}

    monkeypatch.setattr("shared.config.targets.TargetsConfig", Config)
    collector = SimpleNamespace(_snapshot={"capture_warning": {"source": "performance_schema"}})
    monkeypatch.setattr(monitor_loop.query_discovery, "collector_for", lambda target: collector)
    active = monitor_loop.MonitorLoop(store, clock=lambda: 1000, index_reader=lambda target: [],
                                      judge=lambda chosen, context: None,
                                      setup_checker=lambda target, config: {"checks": []})
    active._monitoring_issues("db", 1000, False)
    item = store.inbox("new")[0]
    assert item["subject"] == "performance_schema_missing"
    assert current_title(item) == "performance_schema isn't available"
    assert inbox_link(item, {}, [])["kind"] == "setup"
    assert "performance_schema" in inbox_advice(item, {}, "mysql", {}, [])["summary"]
    # A restart leaves no snapshot until the first reading; that isn't a recovery.
    collector._snapshot = {}
    active._monitoring_issues("db", 1030, False)
    assert store.inbox("new")[0]["subject"] == "performance_schema_missing"
    collector._snapshot = {"state": "watching"}
    active._monitoring_issues("db", 1060, False)
    assert store.inbox("resolved")[0]["subject"] == "performance_schema_missing"
