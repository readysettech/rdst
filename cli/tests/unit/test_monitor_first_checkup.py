from features.monitor import report
from shared.monitor.monitor_store import MonitorStore

START = 1_000_000


def _store(tmp_path, queries=12):
    store = MonitorStore(tmp_path / "monitor.db")
    store.set_settings("db", {"enabled": True})
    store._write(lambda conn: conn.execute(
        "INSERT OR REPLACE INTO monitor_setting VALUES ('db','enabled_at',?)", (str(START),)))
    store.record("db", START + 60, [{"query_key": f"q{i}", "calls": 1, "total_ms": 10 - i % 10}
                                    for i in range(queries)],
                 {f"q{i}": f"SELECT {i}" for i in range(queries)}, [])
    return store


def _jobs(store, states):
    for index, state in enumerate(states):
        store._write(lambda conn, index=index, state=state: conn.execute(
            "INSERT INTO analyze_job (target,query_key,reason,state,priority,enqueued_at) "
            "VALUES ('db',?, 'new', ?, 1, ?)", (f"q{index}", state, START + 70)))


def test_the_first_checkup_waits_two_minutes_and_for_the_top_ten(tmp_path):
    store = _store(tmp_path)
    _jobs(store, ["done"] * 10)
    assert report.first_pass(store, "db", START + 100)["due"] is False
    progress = report.first_pass(store, "db", START + 130)
    assert (progress["analyzed"], progress["total"], progress["due"]) == (10, 10, True)


def test_unfinished_analyses_hold_it_until_ten_minutes(tmp_path):
    store = _store(tmp_path)
    _jobs(store, ["done"] * 7 + ["running"] * 3)
    assert report.first_pass(store, "db", START + 300)["due"] is False
    assert report.first_pass(store, "db", START + 600)["due"] is True


def test_a_small_database_needs_only_its_own_queries(tmp_path):
    store = _store(tmp_path, queries=4)
    _jobs(store, ["done"] * 4)
    assert report.first_pass(store, "db", START + 125)["due"] is True


def test_writes_never_hold_it_since_the_first_pass_analyzes_reads(tmp_path):
    store = _store(tmp_path, queries=7)
    store.record("db", START + 61, [{"query_key": "w", "calls": 1, "total_ms": 50}],
                 {"w": "INSERT INTO readings VALUES ($1)"}, [])
    _jobs(store, ["done"] * 7)
    progress = report.first_pass(store, "db", START + 125)
    assert (progress["analyzed"], progress["total"], progress["due"]) == (7, 7, True)


def test_the_scheduler_marks_the_first_pass_done_without_a_checkup(tmp_path, monkeypatch):
    store = _store(tmp_path)
    _jobs(store, ["done"] * 10)
    monkeypatch.setattr(report.database_limit, "_signed_in", lambda: True)
    monkeypatch.setattr(report.action_explain, "due", lambda store, target, now: False)
    scheduler = report.ReportScheduler(store, clock=lambda: START + 200)
    assert scheduler._plan(START + 200) == []
    assert report.first_pass(store, "db", START + 260) is None
    # A query found later in the first hour doesn't reopen it.
    store._write(lambda conn: conn.execute(
        "INSERT INTO analyze_job (target,query_key,reason,state,priority,enqueued_at) "
        "VALUES ('db','late','new','queued',1,?)", (START + 300,)))
    assert report.first_pass(store, "db", START + 320) is None


def test_explanations_are_planned_apart_from_checkups(tmp_path, monkeypatch):
    store = _store(tmp_path)
    monkeypatch.setattr(report.database_limit, "_signed_in", lambda: True)
    monkeypatch.setattr(report.action_explain, "due", lambda store, target, now: True)
    scheduler = report.ReportScheduler(store, clock=lambda: START + 7200)
    # The first report may be claimed in the same tick; the explanation is its own entry.
    plan = scheduler._plan(START + 7200)
    assert [entry for entry in plan if entry[0] != "first"] == [("explain", ("db",))]
    scheduler._explaining.add("db")
    # One call at a time per database.
    assert scheduler._plan(START + 7260) == []


async def test_checkups_run_a_few_at_a_time(tmp_path):
    import asyncio

    scheduler = report.ReportScheduler(MonitorStore(tmp_path / "monitor.db"))
    running = peak = 0

    async def work():
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1

    await asyncio.gather(*(scheduler._in_slot(work()) for _ in range(6)))
    assert peak == report.CHECKUP_SLOTS
