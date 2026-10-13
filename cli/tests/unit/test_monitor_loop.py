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
    # The third failed check in a row files the lost connection.
    for clock in (1120, 1180, 1240):
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
            now[0] += 180
        else:
            active._stop.set()
    monkeypatch.setattr(monitor_loop.asyncio, "sleep", advance)
    await active._run_connections()
    assert store.inbox()[0]["subject"] == "lost_connection"
    assert store.inbox()[0]["created_at"] == 1180


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


async def test_a_passing_setup_check_unblocks_readings_without_a_restart(monkeypatch, tmp_path):
    from features.monitor.api import routes as monitor_routes
    from features.query_registry.discovery import (
        QueryDiscoveryCollector,
        QueryDiscoveryCoordinator,
    )

    class Config:
        def load(self):
            pass

        def get(self, target):
            return {"engine": "mysql"}

    monkeypatch.setattr("shared.config.targets.TargetsConfig", Config)
    coordinator = QueryDiscoveryCoordinator(collector_factory=QueryDiscoveryCollector)
    monkeypatch.setattr(monitor_loop, "query_discovery", coordinator)
    collector = coordinator.collector_for("db")
    # A cycle before the GRANT found performance_schema unreadable and cached the gap.
    collector._engine = "mysql"
    collector._legacy_fallback = True
    collector._source_unavailable = {"error_code": "permission_denied", "error": "OperationalError"}
    collector._snapshot = {**collector._snapshot, "state": "watching",
                           "capture_warning": collector._capture_warning()}
    store = MonitorStore(tmp_path / "monitor.db")
    store.set_settings("db", {"enabled": True})
    active = monitor_loop.MonitorLoop(store, clock=lambda: 1000, index_reader=lambda target: [],
                                      judge=lambda chosen, context: None,
                                      setup_checker=lambda target, config: {"checks": []})
    monkeypatch.setattr(monitor_loop, "_active_loop", active)
    active._monitoring_issues("db", 1000, False)
    readings = SimpleNamespace(next_reading_at=lambda target: None,
                               collection_status=lambda target: {})
    assert monitor_routes._readings(store, "db", readings)["setup_blocked"] is True

    collected: list[str] = []
    scheduler = SimpleNamespace(collect_soon=collected.append)
    await monitor_loop.setup_check_passed("db", scheduler)
    # Polling the passing check again finds nothing cached.
    await monitor_loop.setup_check_passed("db", scheduler)

    assert monitor_routes._readings(store, "db", readings).get("setup_blocked") is None
    assert collected == ["db"] and active._wake.is_set()
    # A pass before the next reading doesn't file the cached gap again.
    active._monitoring_issues("db", 1060, False)
    assert monitor_routes._readings(store, "db", readings).get("setup_blocked") is None
    await collector._apply_reconnect()
    assert collector._source_unavailable is None and not collector._legacy_fallback


def test_database_checks_run_every_fifteen_minutes_without_a_model(monkeypatch, tmp_path):
    from features.monitor import snapshot

    store = MonitorStore(tmp_path / "monitor.db")
    reads = []
    monkeypatch.setattr(snapshot, "database_check",
                        lambda store, target, now, health: [{"id": "vacuum", "at": now}])
    loop = monitor_loop.MonitorLoop(store, clock=lambda: 1000, index_reader=lambda target: [],
                                    health_reader=lambda target: reads.append(target) or {})
    assert loop._database_check("db", 1000) == [{"id": "vacuum", "at": 1000}]
    assert store.database_check("db") == {"at": 1000, "actions": [{"id": "vacuum", "at": 1000}]}
    # Not due again until fifteen minutes on; the stored findings hold meanwhile.
    assert loop._database_check("db", 1000 + 600) is None
    assert loop._database_check("db", 1000 + 900) == [{"id": "vacuum", "at": 1900}]
    assert reads == ["db", "db"]


def test_failed_database_checks_keep_the_last_findings(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    store.save_database_check("db", 10, [{"id": "kept"}])
    loop = monitor_loop.MonitorLoop(store, clock=lambda: 5000, index_reader=lambda target: [],
                                    health_reader=lambda target: {"collection_error": "down"})
    assert loop._database_check("db", 5000) is None
    assert store.database_check("db")["actions"] == [{"id": "kept"}]


def test_a_check_with_a_failed_section_keeps_what_it_couldnt_confirm(monkeypatch, tmp_path):
    from features.monitor import snapshot

    store = MonitorStore(tmp_path / "monitor.db")
    store.save_database_check("db", 10, [{"id": "inactive_slots"}, {"id": "vacuum"}])
    monkeypatch.setattr(snapshot, "database_check",
                        lambda store, target, now, health: [{"id": "vacuum", "fresh": True}])
    loop = monitor_loop.MonitorLoop(
        store, clock=lambda: 5000, index_reader=lambda target: [],
        health_reader=lambda target: {"section_errors": {"replication": "timeout"}})
    assert loop._database_check("db", 5000) == [{"id": "vacuum", "fresh": True},
                                                {"id": "inactive_slots"}]


def test_the_first_database_checks_run_at_once_then_again_with_rates(monkeypatch, tmp_path):
    from features.monitor import snapshot

    store = MonitorStore(tmp_path / "monitor.db")
    store._write(lambda conn: conn.execute(
        "INSERT OR REPLACE INTO monitor_setting VALUES ('db','enabled_at','1000')"))
    monkeypatch.setattr(snapshot, "database_check", lambda store, target, now, health: [])
    loop = monitor_loop.MonitorLoop(store, clock=lambda: 1000, index_reader=lambda target: [],
                                    health_reader=lambda target: {})
    assert loop._database_check("db", 1000 + 10) == []
    assert loop._database_check("db", 1000 + 100) is None
    # Two minutes of readings give rates: the second check, then every 15 minutes.
    assert loop._database_check("db", 1000 + 130) == []
    assert loop._database_check("db", 1000 + 130 + 600) is None
    assert loop._database_check("db", 1000 + 130 + 900) == []


def test_a_database_without_its_first_statistics_is_settling(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    store._write(lambda conn: conn.execute(
        "INSERT OR REPLACE INTO monitor_setting VALUES ('db','enabled_at','900')"))
    loop = monitor_loop.MonitorLoop(store, clock=lambda: 1000, index_reader=lambda target: [],
                                    stats_reader=lambda target: {}, health_reader=lambda t: {})
    assert loop._settling({"db"})
    # Past its first hour, an unreadable database waits for the normal pass.
    loop.clock = lambda: 900 + monitor_loop.SETTLING_S
    assert not loop._settling({"db"})
    loop.clock = lambda: 1000
    store._write(lambda conn: conn.execute(
        "INSERT INTO stats_meta (target,engine,counter_since,observed_since,taken_at) "
        "VALUES ('db','postgresql',1,1,1)"))
    # The check at add time leaves it settling until the one with rates runs.
    store.save_database_check("db", 910, [])
    assert loop._settling({"db"})
    store.save_database_check("db", 1000 + monitor_loop.actions.FIRST_DATABASE_CHECK_S, [])
    assert not loop._settling({"db"})
