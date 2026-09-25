"""The first hour on a fake clock: the top N all queue at the first reading, then re-checks."""

import asyncio

import pytest

from features.monitor.analyze_queue import (AnalyzeQueue, enqueue_first_hour, enqueue_user,
                                            first_hour_facts, first_hour_max, queue_plan,
                                            started_today)
from shared.monitor.monitor_store import MonitorStore

T0 = 1_800_000_000


@pytest.fixture
def store(tmp_path, monkeypatch):
    # The default top 10, and a newcomer allowance of 2: the first hour has its own.
    monkeypatch.setenv("RDST_MONITOR_NEWCOMER_DAILY_CAP", "2")
    result = MonitorStore(tmp_path / "monitor.db", clock=lambda: T0)
    result.set_settings("db", {"enabled": True, "deep_analysis": True, "execute": True})
    result.start_first_hour("db", T0)
    return result


def add(store, key, weight, *, at=T0, sql=None):
    store.record("db", at, [{"query_key": key, "calls": 10, "total_ms": weight}],
                 {key: sql or f"SELECT * FROM t WHERE id = {key[1:] or 0}"}, [], None)


def reads(store, count, *, at=T0):
    for index in range(count):
        add(store, f"r{index}", 1000 - index, at=at)


def queued_keys(store):
    return {job["query_key"] for job in store.analyze_jobs("db") if job["state"] != "skipped"}


def test_the_whole_top_n_queues_at_the_first_reading(store):
    # A pass before the first reading finds nothing and doesn't hold up the next one.
    assert enqueue_first_hour("db", store=store, now=T0 + 1) == []
    reads(store, 14, at=T0 + 5)
    # Writes are heavier than every read and never count.
    add(store, "w1", 5000, at=T0 + 5, sql="UPDATE t SET x = 1")
    add(store, "w2", 4000, at=T0 + 5, sql="INSERT INTO t VALUES (1)")
    first = enqueue_first_hour("db", store=store, now=T0 + 10)
    assert [job["query_key"] for job in first] == [f"r{index}" for index in range(10)]
    assert enqueue_first_hour("db", store=store, now=T0 + 30) == []
    assert queued_keys(store) == {f"r{index}" for index in range(10)}
    # They start heaviest first, one at a time.
    plan = queue_plan(store, "db", T0 + 10)
    order = sorted(plan["queued"], key=lambda job: plan["jobs"][job["id"]]["queue_position"])
    assert [job["query_key"] for job in order] == [f"r{index}" for index in range(10)]


def test_the_top_n_follows_the_setting(store):
    store.set_settings("db", {"analysis_daily_cap": 4})
    reads(store, 8, at=T0 + 5)
    assert len(enqueue_first_hour("db", store=store, now=T0 + 10)) == 4
    assert first_hour_facts(store, "db", T0 + 10)["top"] == 4


def test_first_hour_analyses_leave_the_daily_allowances_alone(store):
    reads(store, 10)
    enqueue_first_hour("db", store=store, now=T0 + 10)
    plan = queue_plan(store, "db", T0 + 10)
    assert {fact["queue_state"] for fact in plan["jobs"].values()} == {"today"}
    assert sorted(fact["queue_position"] for fact in plan["jobs"].values()) == list(range(1, 11))
    for index in range(10):
        job = store.claim_analysis("db", T0 + 130 + index, cap=2, newcomer_cap=0)
        assert job is not None, index
        store.finish_analysis(job["id"], "done", T0 + 131 + index, billable_calls=1)
    assert started_today(store, "db", T0 + 200) == 0
    assert queue_plan(store, "db", T0 + 200)["used"] == 0


def test_the_top_list_is_read_on_every_reading_through_the_first_hour(store):
    # The queue follows the ranking: a query that reshuffles onto the first page is queued
    # on the next reading, not minutes later.
    reads(store, 10)
    enqueue_first_hour("db", store=store, now=T0 + 10)
    add(store, "hot", 9000, at=T0 + 100)
    assert [job["query_key"] for job in enqueue_first_hour("db", store=store, now=T0 + 120)] \
        == ["hot"]
    add(store, "hotter", 19000, at=T0 + 400)
    assert [job["query_key"] for job in enqueue_first_hour("db", store=store, now=T0 + 400)] \
        == ["hotter"]
    add(store, "hottest", 29000, at=T0 + 430)
    # Not due within the same minute.
    assert enqueue_first_hour("db", store=store, now=T0 + 440) == []
    assert [job["query_key"] for job in enqueue_first_hour("db", store=store, now=T0 + 460)] \
        == ["hottest"]
    facts = first_hour_facts(store, "db", T0 + 470)
    assert facts["top"] == 10 and facts["analyzed"] == 0 and facts["max"] == first_hour_max(10)
    assert facts["next_check_at"] == T0 + 480
    # Each recorded reading leaves a collect pulse; the first one is the first reading.
    assert facts["first_queries_at"] == T0 and facts["first_reading_at"] == T0


def test_at_most_three_times_n_queries_in_the_first_hour(store):
    reads(store, 10)
    at = T0 + 10
    enqueue_first_hour("db", store=store, now=at)
    weight = 10_000
    while at + 300 < T0 + 3600:
        at += 300
        # Ten new heavier reads every check: churn the whole top list.
        for index in range(10):
            add(store, f"n{at}{index}", weight + index, at=at - 1)
        weight += 1000
        enqueue_first_hour("db", store=store, now=at)
    # Waiting queries that fell out of the top were dropped along the way.
    assert 10 <= len(queued_keys(store)) <= first_hour_max(10) == 30


def test_ramp_skips_queries_analyzed_in_the_last_day(store):
    reads(store, 3)
    job = store.enqueue_analysis("db", "r0", "user", 1, T0 - 7200)
    store.claim_analysis("db", T0 - 7200)
    store.finish_analysis(job["id"], "done", T0 - 7100)
    assert [job["query_key"] for job in enqueue_first_hour("db", store=store, now=T0 + 10)] \
        == ["r1", "r2"]


def test_a_click_during_the_first_hour_goes_first(store):
    reads(store, 12)
    enqueue_first_hour("db", store=store, now=T0 + 10)
    click = enqueue_user("db", "r11", store=store, now=T0 + 20)
    assert click["already_queued"] is False
    assert queue_plan(store, "db", T0 + 20)["jobs"][click["id"]]["queue_position"] == 1
    again = enqueue_user("db", "r1", store=store, now=T0 + 20)
    assert again["already_queued"] is True
    assert queue_plan(store, "db", T0 + 20)["jobs"][again["id"]]["queue_position"] == 3


def test_no_ramp_without_a_turn_on_or_after_the_hour(tmp_path, store):
    reads(store, 5)
    assert enqueue_first_hour("db", store=store, now=T0 + 3600) == []
    assert first_hour_facts(store, "db", T0 + 3600) is None
    other = MonitorStore(tmp_path / "other.db", clock=lambda: T0)
    other.set_settings("db", {"enabled": True, "deep_analysis": True})
    assert enqueue_first_hour("db", store=other, now=T0 + 10) == []
    store.set_settings("db", {"deep_analysis": False})
    assert enqueue_first_hour("db", store=store, now=T0 + 10) == []


def test_queue_pass_covers_the_top_n_then_the_ongoing_policy(store):
    reads(store, 12)
    queue = AnalyzeQueue(store, clock=lambda: T0 + 10)
    queue._prepare(T0 + 10)
    assert len(queued_keys(store)) == 10
    # After the hour, a query entering the top 10 is a newcomer, within its allowance (2).
    for index, key in enumerate(("n1", "n2", "n3")):
        add(store, key, 5000 - index, at=T0 + 3500)
    queue._prepare(T0 + 3600)
    later = [job for job in store.analyze_jobs("db") if job["enqueued_at"] == T0 + 3600]
    assert [job["query_key"] for job in later] == ["n2", "n1"]
    assert all(job["reason"] == "new" for job in later)


@pytest.mark.asyncio
async def test_a_wake_while_busy_queues_new_top_queries(store):
    reads(store, 6)
    clock = {"now": T0 + 10}
    queue = AnalyzeQueue(store, clock=lambda: clock["now"])
    queue._prepare(T0 + 10)
    assert len(queued_keys(store)) == 6
    queue._task = asyncio.get_running_loop().create_future()
    queue._busy = True
    add(store, "hot", 9000, at=T0 + 50)
    clock["now"] = T0 + 61
    queue.wake()
    await queue._refresh
    assert len(queued_keys(store)) == 7


def test_turning_back_on_with_history_starts_no_first_hour(tmp_path):
    # Readings from before the turn-on mean the workload is known: no first-run ramp.
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: T0)
    store.set_settings("db", {"enabled": True, "deep_analysis": True, "execute": True})
    store.record("db", T0 - 86400, [], {}, [], {"calls": 10, "total_ms": 100,
                                                 "active_conns": 2, "max_conns": 10})
    store.start_first_hour("db", T0)
    assert store.first_hour("db") == (0, 0)
    assert store.settings("db")["enabled_at"] == T0
    assert store.settings("db")["first_hour_started_at"] is None
    reads(store, 5, at=T0 + 5)
    assert enqueue_first_hour("db", store=store, now=T0 + 10) == []
    assert first_hour_facts(store, "db", T0 + 10) is None


def test_a_true_first_run_keeps_its_first_hour(store):
    reads(store, 3, at=T0 + 5)
    assert store.first_hour("db") == (T0, T0 + 3600)
    assert store.settings("db")["enabled_at"] == T0
    assert store.settings("db")["first_hour_started_at"] == T0


def test_a_waiting_query_that_falls_well_out_of_the_top_makes_room(store):
    store.set_settings("db", {"analysis_daily_cap": 4})
    reads(store, 6)
    assert len(enqueue_first_hour("db", store=store, now=T0 + 10)) == 4
    # Six heavier reads arrive: r0..r3 now rank below the top 6 (1.5 x 4) and drop out.
    for index in range(6):
        add(store, f"h{index}", 5000 - index, at=T0 + 20)
    jobs = enqueue_first_hour("db", store=store, now=T0 + 30)
    assert [job["query_key"] for job in jobs] == ["h0", "h1", "h2", "h3"]
    assert queued_keys(store) == {"h0", "h1", "h2", "h3"}
    # Pushed just past the top N but still inside the band, h3 keeps its place.
    add(store, "x", 4999.5, at=T0 + 40)
    assert [job["query_key"] for job in enqueue_first_hour("db", store=store, now=T0 + 50)] \
        == ["x"]
    assert queued_keys(store) == {"h0", "h1", "h2", "h3", "x"}


def test_a_rollup_bucket_that_starts_before_the_turn_on_keeps_the_first_hour(store):
    # Minute readings from the first run roll up into a 5-minute bucket that starts before
    # the turn-on; that's the first run's own data, not earlier history.
    reads(store, 3, at=T0 + 5)
    store._write(lambda conn: conn.execute(
        "INSERT INTO db_series (target,res,bucket_start,calls,total_ms) VALUES ('db',300,?,1,1)",
        (T0 - 120,)))
    assert store.first_hour("db") == (T0, T0 + 3600)
    from features.monitor.delivery import discovery_until
    assert discovery_until(store, "db") == T0 + 3600
    # A bucket that ended before the turn-on is earlier history.
    store._write(lambda conn: conn.execute(
        "INSERT INTO db_series (target,res,bucket_start,calls,total_ms) VALUES ('db',300,?,1,1)",
        (T0 - 900,)))
    assert store.first_hour("db") == (0, 0)
    assert discovery_until(store, "db") is None


def test_spare_places_go_to_idle_reads_on_a_small_database(store):
    # Seen two days ago and idle since: outside the load window, still on the page.
    add(store, "idle", 500, at=T0 - 2 * 86400, sql="SELECT count(*) FROM orders")
    add(store, "write", 500, at=T0 - 2 * 86400, sql="UPDATE orders SET status = 1")
    reads(store, 2)
    enqueue_first_hour("db", store=store, now=T0 + 60)
    assert queued_keys(store) == {"r0", "r1", "idle"}


@pytest.mark.parametrize("state,error,count", [("done", None, 1), ("failed", None, 0),
                                               ("failed", "timeout", 0), ("skipped", None, 0)])
def test_first_hour_counts_only_completed_analyses(store, state, error, count):
    reads(store, 2)
    enqueue_first_hour("db", store=store, now=T0 + 10)
    job = store.claim_analysis("db", T0 + 20)
    store.finish_analysis(job["id"], state, T0 + 21, error=error)
    assert first_hour_facts(store, "db", T0 + 30)["analyzed"] == count


def test_nothing_is_queued_before_a_reading_has_measured_calls(store):
    # Monitoring just turned on: queries are known from their text, but no reading has
    # measured their calls yet, so nothing ranks and nothing is queued.
    store.record("db", T0, [], {f"idle{index}": f"SELECT c{index} FROM t" for index in range(5)},
                 [], None)
    assert enqueue_first_hour("db", store=store, now=T0 + 5) == []
    reads(store, 10)
    assert enqueue_first_hour("db", store=store, now=T0 + 70)
