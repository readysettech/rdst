"""Incident windows on inbox items (DESIGN_V5 section 3): when each change began and
ended, measured from the per-query minute series around filing."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from features.monitor import presentation
from features.monitor.api import routes, views
from shared.monitor.monitor_store import MonitorStore

NOW = 1_800_000_000 // 60 * 60
SPIKE = "5b1ce0000001"
CALM = "5b1ce0000002"
LATE = "5b1ce0000003"


def _series(values: list[float | None], start: int = 0) -> list[tuple[int, float | None]]:
    return [(start + index * 60, value) for index, value in enumerate(values)]


def test_a_jump_starts_at_its_first_minute_and_ends_after_ten_calm_ones():
    points = _series([4.0] * 40 + [16.0] * 20 + [4.0] * 30)
    span = presentation.incident_span(points, 55 * 60, metric="qps", direction="up",
                                      before=4.0, after=16.0)
    assert span["start"] == 40 * 60
    assert span["end"] == 60 * 60
    assert span["change"] == pytest.approx(3.0)


def test_a_change_that_lasts_has_no_end():
    points = _series([4.0] * 40 + [16.0] * 30)
    span = presentation.incident_span(points, 55 * 60, metric="qps", direction="up",
                                      before=4.0, after=16.0)
    assert (span["start"], span["end"]) == (40 * 60, None)


def test_a_short_dip_does_not_split_the_incident():
    points = _series([4.0] * 30 + [16.0] * 10 + [4.0] * 2 + [16.0] * 10 + [4.0] * 20)
    span = presentation.incident_span(points, 50 * 60, metric="qps", direction="up",
                                      before=4.0, after=16.0)
    assert (span["start"], span["end"]) == (30 * 60, 52 * 60)


def test_latency_skips_minutes_without_calls():
    values: list[float | None] = [10.0, None] * 20 + [40.0, None] * 10
    span = presentation.incident_span(_series(values), 58 * 60, metric="mean_ms",
                                      direction="up", before=10.0, after=40.0)
    assert span["start"] == 40 * 60 and span["end"] is None


def test_a_stopped_query_starts_at_its_first_silent_minute():
    points = _series([2.0] * 30 + [0.0] * 30)
    span = presentation.incident_span(points, 59 * 60, metric="qps", direction="down",
                                      before=2.0, after=0.0)
    assert span["start"] == 30 * 60 and span["end"] is None
    assert span["change"] == pytest.approx(-1.0)


def test_a_new_query_starts_with_its_first_reading_even_with_gaps():
    values = [0.0] * 20 + [0.5, 0.0, 0.0, 0.0, 0.0, 0.0] * 5
    span = presentation.incident_span(_series(values), 49 * 60, metric="qps", direction="up",
                                      before=0.0, after=0.1)
    assert span["start"] == 20 * 60


def test_no_change_near_filing_gives_no_window():
    points = _series([4.0] * 60)
    assert presentation.incident_span(points, 59 * 60, metric="qps", direction="up",
                                      before=4.0, after=16.0) is None
    assert presentation.incident_span(points, 59 * 60, metric="qps", direction="up",
                                      before=16.0, after=4.0) is None


# Through the API

@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("RDST_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(routes, "_now", lambda: NOW)
    monkeypatch.setattr(views, "registry_entries", lambda keys=None: {})
    monkeypatch.setattr(views, "assessments", lambda target, keys=None: {})
    monitor = MonitorStore(clock=lambda: NOW)
    monkeypatch.setattr(routes, "_store", lambda: monitor)
    return monitor


@pytest.fixture
def client(store):
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/monitor")
    return TestClient(app)


def _record(store: MonitorStore, minutes: int = 120) -> None:
    """Two hours of readings: SPIKE runs 4x as often from 50 to 30 minutes ago, LATE
    doubles 20 minutes ago and stays there, CALM never changes."""
    texts = {SPIKE: "SELECT * FROM orders WHERE id = $1",
             CALM: "SELECT * FROM customers WHERE id = $1",
             LATE: "SELECT * FROM carts WHERE id = $1"}
    for minute in range(minutes):
        ts = NOW - (minutes - minute) * 60
        ago = (NOW - ts) // 60
        spike = 240 if 30 < ago <= 50 else 60
        late = 120 if ago <= 20 else 60
        rows = [{"query_key": SPIKE, "calls": spike, "total_ms": spike * 2.0},
                {"query_key": CALM, "calls": 60, "total_ms": 60.0},
                {"query_key": LATE, "calls": late, "total_ms": late * 1.0}]
        store.record("db", ts, rows, texts, [],
                     {"calls": spike + 60 + late, "total_ms": 500.0, "active_conns": 3,
                      "max_conns": 100, "cache_hit_ratio": 0.99, "xact_commit": 10,
                      "xact_rollback": 0, "deadlocks": 0})


def test_a_traffic_item_carries_its_measured_window(client, store):
    _record(store)
    item = store.upsert_item("db", "traffic_spike", SPIKE, 0.7, "x",
                             {"query_key": SPIKE, "baseline": {"1h": {"qps": 1.0}},
                              "reported_numbers": {"qps": 4.0}}, NOW - 40 * 60)
    incident = client.get(f"/api/monitor/inbox/{item['id']}").json()["incident"]
    assert incident["measured"] is True
    assert incident["start"] == NOW - 50 * 60
    assert incident["end"] == NOW - 30 * 60
    assert incident["ongoing"] is False
    assert incident["metric"] == "calls"
    assert incident["change"] == pytest.approx(3.0)


def test_an_unmeasured_item_falls_back_to_its_own_times(client, store):
    _record(store, minutes=10)
    item = store.upsert_item("db", "cache_hit_ratio_drop", "database", 0.9, "x",
                             {"numbers": {"cache_hit_ratio": 0.8,
                                          "cache_hit_ratio_before": 0.99}}, NOW - 300)
    incident = client.get(f"/api/monitor/inbox/{item['id']}").json()["incident"]
    assert incident == {"start": NOW - 300, "end": None, "ongoing": True, "measured": False,
                        "metric": None, "change": pytest.approx(0.8 / 0.99 - 1),
                        "queries": []}
    assert client.get("/api/monitor/inbox/99999").status_code == 404


def test_group_members_each_carry_their_onset_and_sparkline(client, store):
    _record(store)
    store.upsert_item("db", "many_queries_changed", "database", 0.7, "x",
                      {"query_keys": [SPIKE, CALM, LATE], "recovery_queries": {
                          key: {"kind": "traffic_spike"} for key in (SPIKE, CALM, LATE)}},
                      NOW - 10 * 60)
    item = client.get("/api/monitor/inbox?state=open&incidents=true").json()["items"][0]
    assert item["title"].startswith("Traffic jumped")
    incident = item["incident"]
    members = {query["query_key"]: query for query in incident["queries"]}
    assert members[LATE]["start"] == NOW - 20 * 60 and members[LATE]["end"] is None
    assert members[LATE]["change"] == pytest.approx(1.0)
    assert members[CALM]["start"] is None
    # The spike ended before filing, so the member readings around filing don't show it.
    assert members[SPIKE]["start"] is None
    assert incident["start"] == NOW - 20 * 60 and incident["ongoing"] is True
    assert all(query["spark"] for query in incident["queries"])
    stamps = [point["ts"] for point in members[LATE]["spark"]]
    assert stamps == sorted(stamps) and stamps[0] <= incident["start"] - 1800


def test_a_move_under_twenty_percent_is_not_the_change():
    points = _series([10.0] * 40 + [11.0] * 20)
    assert presentation.incident_span(points, 55 * 60, metric="qps", direction="up",
                                      before=10.0, after=11.0) is None


def test_a_sparse_hour_before_falls_back_to_the_filing_numbers():
    # Two calls in the hour before: too few to measure a factor against.
    values = [0.0] * 30 + [1 / 60] + [0.0] * 28 + [1 / 60] + [0.2] * 20
    span = presentation.incident_span(_series(values), 75 * 60, metric="qps",
                                      direction="up", before=0.05, after=0.2)
    assert span["start"] == 60 * 60
    assert span["change"] == pytest.approx(3.0)


def test_members_are_anchored_on_when_they_joined(client, store):
    """A member that joined the episode later is measured around its own filing."""
    _record(store)
    store.upsert_item("db", "many_queries_changed", "database", 0.7, "x",
                      {"query_keys": [LATE, CALM], "recovery_queries": {
                          key: {"kind": "traffic_spike", "filed_at": NOW - 10 * 60,
                                "baseline_qps": 1.0, "baseline_mean_ms": 1.0,
                                "at_filing": {"qps": qps, "mean_ms": 1.0}}
                          for key, qps in ((LATE, 2.0), (CALM, 1.0))}},
                      NOW - 5 * 3600)
    item = client.get("/api/monitor/inbox?state=open&incidents=true").json()["items"][0]
    late = next(query for query in item["incident"]["queries"] if query["query_key"] == LATE)
    assert late["start"] == NOW - 20 * 60


def test_the_list_measures_windows_only_when_asked(client, store):
    _record(store)
    store.upsert_item("db", "many_queries_changed", "database", 0.7, "x",
                      {"query_keys": [SPIKE, CALM, LATE], "recovery_queries": {
                          key: {"kind": "traffic_spike"} for key in (SPIKE, CALM, LATE)}},
                      NOW - 10 * 60)
    assert client.get("/api/monitor/inbox?state=open").json()["items"][0]["incident"] is None
    assert client.get("/api/monitor/inbox?state=open&incidents=true").json()["items"][0][
        "incident"] is not None


def test_a_noisy_run_that_began_earlier_moves_the_onset_back():
    # Calls rose 80 minutes before filing; a sparse query's minutes come in bursts, so the
    # first pass starts 20 minutes back with an hour before it that already reads changed.
    bursts = [0.75] * 4 + [0.0] * 8
    values = [0.1] * 60 + bursts * 5 + [0.28] * 20
    span = presentation.incident_span(_series(values), 139 * 60, metric="qps",
                                      direction="up", before=0.1, after=0.3)
    assert span["start"] == 60 * 60
    assert span["change"] == pytest.approx(1.6, abs=0.1)


def test_a_one_minute_latency_spike_has_an_onset():
    values = [5.0] * 60 + [200.0] + [5.0] * 20
    span = presentation.incident_span(_series(values), 62 * 60, metric="mean_ms",
                                      direction="up", before=5.0, after=60.0)
    assert (span["start"], span["end"]) == (60 * 60, 61 * 60)


def test_members_follow_their_own_metric_and_carry_their_numbers(client, store):
    """A slower member of a traffic group is measured on latency; a recovered member
    without a join time is anchored on its first changed reading before it recovered."""
    texts = {SPIKE: "SELECT * FROM orders WHERE id = $1",
             LATE: "SELECT * FROM carts WHERE id = $1"}
    for minute in range(360):
        ts = NOW - (360 - minute) * 60
        ago = (NOW - ts) // 60
        late = 120 if ago <= 20 else 60
        slow = 8.0 if 200 <= ago < 240 else 1.0
        store.record("db", ts, [{"query_key": SPIKE, "calls": 60, "total_ms": 60 * slow},
                                {"query_key": LATE, "calls": late, "total_ms": late * 1.0}],
                     texts, [], {"calls": 60 + late, "total_ms": 500.0, "active_conns": 3,
                                 "max_conns": 100, "cache_hit_ratio": 0.99,
                                 "xact_commit": 10, "xact_rollback": 0, "deadlocks": 0})
    store.upsert_item("db", "many_queries_changed", "database", 0.7, "x", {
        "query_keys": [LATE], "episode_query_keys": [LATE, SPIKE],
        "recovery_queries": {LATE: {"kind": "traffic_spike", "filed_at": NOW - 10 * 60,
                                    "baseline_qps": 1.0, "baseline_mean_ms": 1.0,
                                    "at_filing": {"qps": 2.0, "mean_ms": 1.0}}},
        "recovered_queries": {SPIKE: {"kind": "latency_regression",
                                      "baseline_mean_ms": 1.0, "baseline_qps": 1.0,
                                      "before": {"mean_ms": 8.0, "qps": 1.0},
                                      "after": {"mean_ms": 1.0, "qps": 1.0},
                                      "retired_at": NOW - 150 * 60}}},
        NOW - 300 * 60)
    item = client.get("/api/monitor/inbox?state=open&incidents=true").json()["items"][0]
    members = {query["query_key"]: query for query in item["incident"]["queries"]}
    assert members[LATE]["metric"] == "calls"
    assert (members[LATE]["before"], members[LATE]["after"]) == (1.0, 2.0)
    spike = members[SPIKE]
    assert (spike["metric"], spike["before"], spike["after"]) == ("latency", 1.0, 8.0)
    assert (spike["start"], spike["end"]) == (NOW - 239 * 60, NOW - 199 * 60)
