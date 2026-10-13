"""Cancelling an analysis: the job ends skipped as cancelled with an event, a running
worker is told to stop, its late result never lands, and the query's latest analysis
stays as it was."""

from __future__ import annotations

import threading

from features.monitor import analyze_queue
from features.monitor.api import views
from features.monitor.tests.conftest import KEYS, NOW, seed


def _job(store, key: str, state: str, ts: int, *, ref: str | None = None) -> int:
    def write(conn):
        cursor = conn.execute(
            "INSERT INTO analyze_job (target,query_key,reason,state,priority,enqueued_at,"
            "started_at,finished_at,analysis_ref,owner,attempt) VALUES ('db',?,'user',?,1.0,?,?,?,?,"
            "'worker',1)", (key, state, ts, ts if state == "running" else None,
                            ts if state == "done" else None, ref))
        return cursor.lastrowid
    return store._write(write)


class _Run:
    def __init__(self) -> None:
        self.stopped = threading.Event()


def test_cancel_marks_the_job_stops_its_run_and_keeps_the_last_analysis(client, store,
                                                                        monkeypatch):
    seed(store, minutes=3)
    done = _job(store, KEYS[0], "done", NOW - 600, ref=f"/api/query-registry/{KEYS[0]}/analysis/a")
    running = _job(store, KEYS[0], "running", NOW - 30)
    queue = analyze_queue.AnalyzeQueue(store, service=object(), clock=lambda: NOW)
    run = _Run()
    queue._active_runs[running] = run
    stopped = []
    monkeypatch.setattr(analyze_queue.analysis_lane, "stop", stopped.append)
    monkeypatch.setattr(analyze_queue, "_active_queue", queue)
    page = client.post(f"/api/monitor/targets/db/analyze-jobs/{running}/cancel").json()
    assert (page["id"], page["state"], page["cancelled"], page["reason_text"]) == \
        (running, "skipped", True, "Cancelled.")
    assert run.stopped.is_set() and stopped == [run]
    row = store.read("SELECT * FROM analyze_job WHERE id=?", (running,))[0]
    assert (row["state"], row["error"], row["finished_at"]) == ("skipped", "cancelled", NOW)
    assert store.pulses("db", 1)[0]["detail"] == f"Analysis cancelled for {KEYS[0]}"
    # The worker's late finish no longer applies, so nothing files from it.
    assert store.finish_analysis(running, "done", NOW + 5, analysis_ref="/late", owner="worker",
                                 attempt=1) is False
    assert store.read("SELECT state FROM analyze_job WHERE id=?", (running,))[0]["state"] == "skipped"
    # The query's latest analysis is still the finished one.
    queries = views.queries(store, "db", "1h", NOW, sort="db_time_share", filter="all", q="",
                            limit=10, offset=0)
    row = next(item for item in queries["queries"] if item["key"] == KEYS[0])
    assert row["last_analysis"]["state"] == "done"
    assert row["last_analysis"]["analysis_ref"].endswith("/analysis/a")
    assert row["status"] != "analyzing"
    assert client.post(f"/api/monitor/targets/db/analyze-jobs/{running}/cancel").status_code == 409
    assert client.post(f"/api/monitor/targets/db/analyze-jobs/{done}/cancel").status_code == 409
    assert client.post("/api/monitor/targets/db/analyze-jobs/999/cancel").status_code == 404


def test_the_per_query_form_cancels_the_queued_job(client, store, monkeypatch):
    seed(store, minutes=3)
    monkeypatch.setattr(analyze_queue, "_active_queue", None)
    queued = _job(store, KEYS[1], "queued", NOW - 10)
    assert client.post(f"/api/monitor/targets/db/queries/{KEYS[1]}/analyze/cancel"
                       ).status_code == 200
    assert store.read("SELECT state,error FROM analyze_job WHERE id=?", (queued,))[0] == \
        {"state": "skipped", "error": "cancelled"}
    assert client.post(f"/api/monitor/targets/db/queries/{KEYS[1]}/analyze/cancel"
                       ).status_code == 409
