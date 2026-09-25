"""An item's state agrees with what its latest occurrence measured (owner round 9)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from features.monitor import loop as monitor_loop
from features.monitor.api import views
from features.monitor.presentation import band_reason, item_status, settle_status
from shared.monitor.monitor_store import MonitorStore

NOW = 2_000_000
MIN = 60


def presented(**fields):
    item = {"id": 1, "target": "db", "type": "many_queries_changed", "state": "seen",
            "severity_band": "act_now", "tone": "negative", "created_at": NOW - 3600,
            "resolved_at": None, "title": "3 queries got slower at the same time",
            "query_keys": ["a", "b", "c", "d"], "relevance_rule": None, "occurrences": [],
            "measurement": {"metric": "latency", "direction": "up", "change": 2.2}}
    return {**item, **fields}


def window(*queries, ongoing=True, end=None):
    return {"start": NOW - 3000, "end": end, "ongoing": ongoing, "measured": True,
            "metric": "latency", "change": 2.2,
            "queries": [{"query_key": key, "start": start, "end": stop}
                        for key, start, stop in queries]}


LATEST = NOW - 1800
REPEAT_BODY = {"occurrences": [
    {"start": NOW - 7200, "end": NOW - 6600, "keys": ["a", "b"], "verdict": None},
    {"start": LATEST, "end": None, "keys": ["a", "b", "c", "d"], "verdict": None}]}
REPEAT_OCCURRENCES = [{"start": LATEST, "end": None, "size": 3.0, "query_count": 4,
                       "quiet": False, "verdict": None},
                      {"start": NOW - 7200, "end": NOW - 6600, "size": 3.0, "query_count": 2,
                       "quiet": False, "verdict": None}]


def test_a_repeat_whose_latest_occurrence_measured_back_to_normal_is_not_act_now():
    # a, b, c changed with the latest occurrence and ended; d already read high 40 minutes
    # earlier (a level that never moved) and isn't part of this occurrence.
    incident = window(("a", LATEST - 60, LATEST + 600), ("b", LATEST, LATEST + 780),
                      ("c", LATEST + 60, LATEST + 300), ("d", LATEST - 40 * MIN, None))
    item = presented(occurrences=REPEAT_OCCURRENCES)
    assert item_status(item, REPEAT_BODY, incident) == ("back_to_normal", LATEST + 780)
    settled = settle_status(item, REPEAT_BODY, incident)
    assert (settled["severity_band"], settled["tone"]) == ("recovered", "positive")
    assert settled["occurrences"][0]["end"] == LATEST + 780
    assert settled["incident"]["ongoing"] is False
    assert settled["incident"]["end"] == LATEST + 780
    assert settled["band_reason"] == "Back to normal: latency up 3.2x on 4 queries at once"
    # The measured window is cached; settling never changes it in place.
    assert incident["ongoing"] is True and incident["end"] is None


def test_a_repeat_with_a_member_of_its_latest_occurrence_still_changed_stays_ongoing():
    incident = window(("a", LATEST, LATEST + 600), ("b", LATEST + 120, None))
    item = presented(occurrences=REPEAT_OCCURRENCES)
    assert item_status(item, REPEAT_BODY, incident) == ("ongoing", LATEST)
    settled = settle_status(item, REPEAT_BODY, incident)
    assert settled["severity_band"] == "act_now"
    assert settled["occurrences"][0]["end"] is None
    assert settled["band_reason"] == (
        "Act now: latency up 3.2x on 4 queries at once, still happening")


def test_a_repeat_without_measured_members_follows_its_stored_occurrence():
    item = presented(occurrences=REPEAT_OCCURRENCES)
    assert item_status(item, REPEAT_BODY, None) == ("ongoing", LATEST)
    ended = {"occurrences": [REPEAT_BODY["occurrences"][0],
                             {**REPEAT_BODY["occurrences"][1], "end": LATEST + 900}]}
    assert item_status(item, ended, None) == ("back_to_normal", LATEST + 900)
    assert settle_status(item, ended, None)["severity_band"] == "recovered"


def test_a_routine_repeat_says_so():
    body = {"occurrences": [{**entry, "verdict": "usual"}
                            for entry in REPEAT_BODY["occurrences"]]}
    item = presented(severity_band="watch", tone="warning", occurrences=REPEAT_OCCURRENCES)
    settled = settle_status(item, body, window(("a", LATEST, None)))
    assert settled["band_reason"] == "Watch: a routine burst, still happening"
    closed = {"occurrences": [*body["occurrences"][:-1],
                              {**body["occurrences"][-1], "end": LATEST + 600}]}
    assert settle_status(item, closed, None)["band_reason"] == (
        "Back to normal: a routine burst")


def single(**fields):
    return presented(type="latency_regression", title="Query latency increased",
                     query_keys=["a"], relevance_rule="top_10_24h", **fields)


def test_a_single_item_follows_its_measured_window():
    item = single()
    assert item_status(item, {}, window(("a", NOW - 600, None))) == ("ongoing", NOW - 3000)
    settled = settle_status(item, {}, window(("a", NOW - 600, None)))
    assert settled["band_reason"] == (
        "Act now: latency up 3.2x on a top-10 query today, still happening")
    ended = window(("a", NOW - 600, NOW - 60), ongoing=False, end=NOW - 60)
    settled = settle_status(item, {}, ended)
    assert (settled["status"], settled["status_at"]) == ("back_to_normal", NOW - 60)
    assert settled["severity_band"] == "recovered"
    assert settled["band_reason"] == "Back to normal: latency up 3.2x on a top-10 query today"


def test_closed_items_keep_their_band():
    recovered = single(state="resolved", severity_band="recovered", resolved_at=NOW - 5)
    assert item_status(recovered, {}, None) == ("back_to_normal", NOW - 5)
    dismissed = single(state="dismissed", severity_band="watch", resolved_at=None,
                       dismissed_at=NOW - 9)
    assert item_status(dismissed, {}, None) == ("closed", NOW - 9)
    assert settle_status(dismissed, {}, None)["severity_band"] == "watch"


def test_reason_lines_for_other_kinds():
    stuck = presented(type="stuck_query", query_keys=["a"], measurement=None)
    assert band_reason(stuck, {}, "ongoing") == (
        "Act now: a query has been running for over a minute, still happening")
    report = presented(type="daily_summary", severity_band="fyi", query_keys=[])
    assert band_reason(report, {}, "unknown") == "Watch: the daily report is ready"
    spike = presented(type="traffic_spike", severity_band="watch", query_keys=["a"],
                      relevance_rule="share_1h",
                      measurement={"metric": "calls", "direction": "up", "change": 0.4})
    assert band_reason(spike, {}, "ongoing") == (
        "Watch: calls up 40% on a query with over 2% of database time this hour, "
        "still happening")


def test_open_items_carry_their_status_in_every_view(tmp_path):
    """The Database page and the badge read the inbox without incident windows; an open
    repeat whose latest occurrence ended reads back to normal there too."""
    store = MonitorStore(tmp_path / "monitor.db")
    body = {"query_keys": ["aaaaaaaaaaa1", "aaaaaaaaaaa2"],
            "occurrences": [
                {"start": NOW - 7200, "end": NOW - 6600, "keys": ["aaaaaaaaaaa1"],
                 "top": None, "size": 2.0, "quiet": False, "verdict": None},
                {"start": NOW - 1800, "end": NOW - 1200, "keys": ["aaaaaaaaaaa1"],
                 "top": None, "size": 2.0, "quiet": False, "verdict": None}]}
    store.upsert_item("db", "many_queries_changed", "burst:1", 0.9,
                      "2 queries got slower at the same time", body, NOW - 7200)
    [row] = store.inbox(target="db")
    [item] = views.inbox_items(store, [row])
    assert (item["status"], item["severity_band"]) == ("back_to_normal", "recovered")
    assert item["incident"] is None
    assert item["band_reason"].startswith("Back to normal:")


def test_a_restart_inside_the_hour_does_not_dilute_the_normal_rate(monkeypatch, tmp_path):
    """23 collected minutes of a steady 25 calls a minute are a normal rate of 25 a minute,
    not 23 * 25 calls spread over a full hour (a false 2.6x traffic jump)."""
    store = MonitorStore(tmp_path / "monitor.db")
    now = NOW // 60 * 60
    minutes = [now - 23 * 60 - 300 + index * 60 for index in range(23)]
    minutes += [now - 300 + index * 60 for index in range(5)]

    def write(conn):
        conn.execute("INSERT OR REPLACE INTO query_text VALUES (?,?,?,?,?)",
                     ("db", "steady", "SELECT * FROM orders", now - 7 * 86400, now))
        conn.executemany("INSERT OR REPLACE INTO stmt_series (target,query_key,res,bucket_start,calls,total_ms,rows,blks_hit,blks_read) VALUES (?,?,?,?,?,?,?,?,?)",
                         [("db", "steady", 60, ts, 25, 250.0, 0, 0, 0) for ts in minutes])
    store._write(write)
    captured = []
    real = monitor_loop.baselines

    def spy(*args, **kwargs):
        captured.extend(real(*args, **kwargs))
        return captured

    monkeypatch.setattr(monitor_loop, "baselines", spy)
    monkeypatch.setattr(monitor_loop.query_discovery, "collector_for",
                        lambda target: SimpleNamespace(_snapshot={}))
    monkeypatch.setattr(monitor_loop.MonitorLoop, "_monitoring_issues", lambda *args: None)
    loop = monitor_loop.MonitorLoop(store, clock=lambda: now, judge=lambda *args: None,
                                    index_reader=lambda target: [])
    loop.cycle("db")
    [steady] = [entry for entry in captured if entry["query_key"] == "steady"]
    assert steady["baseline"]["1h"]["qps"] == pytest.approx(25 / 60)
    assert steady["ratios"]["1h"]["qps"] == pytest.approx(1.0)


@pytest.mark.parametrize("state,band", [("resolved", "act_now"), ("resolved", "recovered"),
                                       ("seen", "recovered")])
def test_settled_stuck_queries_have_no_ongoing_copy(state, band):
    item = presented(type="stuck_query", state=state, severity_band=band,
                     resolved_at=NOW, summary="Running for over a minute and still going.",
                     occurrences=REPEAT_OCCURRENCES)
    settled = settle_status(item, {}, window(("a", LATEST, None)))
    assert "still going" not in settled["summary"]
    assert "Act now" not in settled["band_reason"]
    assert "still happening" not in settled["band_reason"]
    assert settled["incident"]["ongoing"] is False
    if band == "recovered":
        assert settled["occurrences"][0]["end"] == NOW


def test_resolved_monitoring_issue_does_not_claim_monitoring_is_blocked():
    item = presented(type="monitoring_issue", state="resolved", resolved_at=NOW)
    settled = settle_status(item, {}, None)
    assert settled["band_reason"] == "Closed: the monitoring issue is resolved"
