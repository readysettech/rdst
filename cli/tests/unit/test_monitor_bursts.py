"""Round 4 alert model: one burst per item, the importance gate and recurring bursts."""

from __future__ import annotations

import json
import re

import pytest

from features.monitor.delivery import (
    LoadWindow,
    Relevance,
    attention_gate,
    importance,
    material,
)
from features.monitor.judge import AttentionJudge, reconcile_delivery
from features.monitor.recurring import BUCKET_S, bursts, pattern, quiet_between_bursts
from shared.monitor.monitor_store import MonitorStore

NOW = 2_000_040
HOUR = 3600


def Top():
    """Relevance where every query named top* is in the top 10 by database time."""
    return Relevance(LoadWindow.of({f"top{index}": 100.0 for index in range(3)} |
                                   {"top": 100.0}))


def row(key, *, calls=300, mean_ms=30.0, base_ms=10.0, base_qps=1.0, reason="mover"):
    ratio = mean_ms / base_ms if base_ms else None
    return {"query_key": key, "calls": calls, "total_ms": calls * mean_ms, "mean_ms": mean_ms,
            "qps": calls / 300, "db_time_share": .2, "reason": reason,
            "baseline": {"1h": {"mean_ms": base_ms, "qps": base_qps}},
            "ratios": {"1h": {"mean_ms": ratio,
                              "qps": calls / 300 / base_qps if base_qps else None,
                              "db_time_share": 1}}}


# Importance gate

def test_only_queries_that_carry_load_reach_jev():
    rel = Relevance(LoadWindow.of({"top": 100.0, "other": 1.0}), LoadWindow())
    assert importance(row("top"), rel, 10_000) == "top_10_24h"
    # Not ranked, but its change adds over 5% of all recorded time.
    assert importance(row("big", calls=300, mean_ms=30), Relevance(), 20_000) == "big_change"
    assert importance(row("tiny", calls=3, mean_ms=1, base_ms=0.5), Relevance(), 20_000) is None
    assert importance(row("new", reason="new_heavy"), Relevance(), 20_000) == "new_heavy"


def test_busy_but_cheap_queries_never_qualify_by_calls():
    rel = Relevance(calls_hour=LoadWindow.of({"chatty": 10_000.0, "quiet": 1.0}))
    assert rel.rule("chatty") is None
    assert importance(row("chatty", calls=3000, mean_ms=0.02, base_ms=0.01), rel,
                      50_000) is None
    # A top 10 query by share alone (2% but ranked 11th) no longer files either.
    day = LoadWindow.of({**{f"q{index}": 100.0 for index in range(10)}, "eleventh": 30.0})
    assert importance(row("eleventh", mean_ms=12), Relevance(day), 50_000) is None


def test_a_move_must_be_material_in_ratio_and_in_database_time():
    # 3x latency on a query that shifts 18% of recorded time.
    assert material(row("top"), 50_000)
    # 1.8x is not yet double, unless it is big on its own.
    assert not material(row("top", mean_ms=18), 50_000)
    assert material(row("top", mean_ms=18), 10_000)
    # 2.5x on a query so fast the move is a rounding error.
    assert not material(row("top", calls=1800, mean_ms=0.1, base_ms=0.04), 50_000)


def test_gate_says_why_it_kept_a_query_out():
    rel = Top()
    gate = attention_gate(rel, 50_000)
    assert gate(row("small", calls=3, mean_ms=1, base_ms=0.5)) == {"reason": "small"}
    assert gate(row("top", mean_ms=18)) == {"reason": "minor"}
    assert gate(row("top")) is None
    assert gate(row("stuck", reason="stuck")) is None


# Recurring bursts

def series(onsets, *, length=2, height=30.0, base=0.0, span=None):
    """Five-minute calls with bursts of `length` buckets at each onset."""
    end = span or (max(onsets) + length * BUCKET_S)
    points = []
    for ts in range(0, end, BUCKET_S):
        high = any(onset <= ts < onset + length * BUCKET_S for onset in onsets)
        points.append((ts, height if high else base))
    return points


def test_bursts_split_on_the_series_own_level():
    found = bursts(series([HOUR, 3 * HOUR]), "calls")
    assert [(burst.start, burst.duration) for burst in found] == [
        (HOUR, 2 * BUCKET_S), (3 * HOUR, 2 * BUCKET_S)]


def test_a_steady_schedule_is_routine_and_a_bigger_burst_is_not():
    onsets = [HOUR + index * 55 * 60 for index in range(6)]
    now = onsets[-1] + BUCKET_S
    usual = pattern(series(onsets), now, "calls")
    assert usual and usual.kind == "period" and usual.verdict == "usual"
    assert 50 * 60 <= usual.period_s <= 60 * 60
    points = series(onsets)
    bigger = [(ts, value * 3 if ts >= onsets[-1] else value) for ts, value in points]
    assert pattern(bigger, now, "calls").verdict == "bigger"


def test_a_burst_that_overstays_is_longer_than_usual():
    onsets = [HOUR + index * 55 * 60 for index in range(6)]
    points = series(onsets[:-1]) + [
        (ts, 30.0) for ts in range(onsets[-1], onsets[-1] + 8 * BUCKET_S, BUCKET_S)]
    points = sorted(dict(points).items())
    now = onsets[-1] + 8 * BUCKET_S
    assert pattern(points, now, "calls").verdict == "longer"


def test_irregular_bursts_are_not_a_pattern():
    onsets = [HOUR, 2 * HOUR, 6 * HOUR, 7 * HOUR, 15 * HOUR]
    assert pattern(series(onsets), 15 * HOUR + BUCKET_S, "calls") is None


def test_same_time_each_day_is_routine():
    onsets = [2 * HOUR + day * 86400 for day in range(4)]
    found = pattern(series(onsets, base=5.0), onsets[-1] + BUCKET_S, "calls")
    assert found and found.kind == "daily" and found.repeats == 3


def test_a_query_between_its_usual_bursts_is_not_stopped():
    onsets = [HOUR + index * 45 * 60 for index in range(6)]
    points = series(onsets, span=onsets[-1] + 40 * 60)
    assert quiet_between_bursts(points, onsets[-1] + 40 * 60) is not None
    # Silent far past its usual gap: that is a real stop.
    late = series(onsets, span=onsets[-1] + 3 * HOUR)
    assert quiet_between_bursts(late, onsets[-1] + 3 * HOUR) is None


# One burst, one item

class Jev:
    def attention(self, **request):
        raise AssertionError("not called")


def _burst(judge, ts, keys, *, gated=None):
    rows = [row(key) for key in keys]
    verdict = {"event_scope": "database_wide", "queries": [
        {"query_key": key, "kind": "latency_regression", "attention": "inbox",
         "severity": .7} for key in keys], "open_items": []}
    judge._file("db", ts, rows, {"target_numbers": {}}, verdict, gated=gated)


def test_each_burst_is_its_own_item_and_members_join_within_the_gap(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    judge = AttentionJudge(store, client=Jev(), clock=lambda: NOW)
    _burst(judge, NOW, ["a", "b"], gated={"s1": {"reason": "small"},
                                         "s2": {"reason": "small"},
                                         "m": {"reason": "minor"}})
    _burst(judge, NOW + 600, ["a", "b", "c"])
    # Forty minutes on, a new burst: a new item. The earlier members stay in theirs.
    _burst(judge, NOW + 2400, ["a", "d", "e"])
    items = sorted(store.inbox(target="db"), key=lambda item: item["created_at"])
    assert [item["subject"] for item in items] == [f"burst:{NOW}", f"burst:{NOW + 2400}"]
    first, second = (json.loads(item["body_json"]) for item in items)
    assert set(first["episode_query_keys"]) == {"a", "b", "c"}
    assert first["burst_last_join"] == NOW + 600
    assert first["smaller_query_keys"] == ["s1", "s2"]
    assert set(second["episode_query_keys"]) == {"d", "e"}


def test_the_old_database_wide_episode_retires_to_the_digest(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    store.upsert_item("db", "many_queries_changed", "database", .9, "77 queries changed",
                      {"query_keys": ["a", "b"]}, NOW)
    reconcile_delivery(store, "db", NOW + 300, Relevance(), learning=False)
    [item] = store.inbox(target="db")
    assert (item["state"], item["delivery"]) == ("resolved", "digest")
    assert json.loads(item["body_json"])["retired"] == "burst_split"


def test_gate_evidence_is_recorded_and_never_sent_to_jev(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    sent = []

    class Recording:
        def attention(self, **request):
            sent.append(request)
            return {"request_id": request["request_id"], "event_scope": "separate",
                    "queries": [], "open_items": []}

    store.record("db", NOW, [{"query_key": "top", "calls": 1, "total_ms": 10}],
                 {"top": "SELECT * FROM orders"}, [])
    judge = AttentionJudge(store, client=Recording(), clock=lambda: NOW)
    pattern_seen = {"kind": "period", "period_s": 3300, "verdict": "usual"}
    judge([row("top")], {"target": "db", "measured": [], "relevance": Top(),
                         "gated": {"s": {"reason": "small"},
                                   "r": {"reason": "recurring", "pattern": pattern_seen}}})
    assert "gate" not in sent[0]
    recorded = json.loads(store.read("SELECT request_json FROM judge_run")[0]["request_json"])
    assert recorded["gate"] == {"smaller": ["s"], "minor": [], "intermittent": [],
                                "recurring": {"r": pattern_seen}}


@pytest.mark.parametrize("verdict", ["bigger", "longer"])
def test_items_say_when_a_scheduled_burst_broke_its_pattern(tmp_path, verdict):
    from features.monitor.presentation import recurring_note

    body = {"recurring": {"a": {"kind": "period", "period_s": 2700, "repeats": 4,
                                "last_onset": NOW, "verdict": verdict}}}
    assert recurring_note(body) == {"kind": "period", "period_s": 2700, "repeats": 4,
                                    "last_onset": NOW, "verdict": verdict}
    assert recurring_note({"recurring": {"a": {"verdict": "usual"}}}) is None


# Recovery: a burst closes once every member's readings are back to normal

def _collect(store, ts):
    store.record("db", ts - 60, [], {}, [], {"calls": 0, "total_ms": 0})


def _traffic_burst(judge, keys, *, base_qps):
    # A report query: no calls in the hour before, a few over the day.
    rows = [{**row(key, calls=300, mean_ms=5, base_ms=5, base_qps=base_qps)} for key in keys]
    for entry in rows:
        entry["baseline"]["1d"] = {"mean_ms": 5, "qps": 0.01}
    verdict = {"event_scope": "database_wide", "queries": [
        {"query_key": key, "kind": "traffic_spike", "attention": "inbox", "severity": .9}
        for key in keys], "open_items": []}
    judge._file("db", NOW, rows, {"target_numbers": {}}, verdict)


def test_report_queries_with_no_traffic_before_recover_when_they_go_quiet(tmp_path):
    from features.monitor.judge import reconcile_recovery

    store = MonitorStore(tmp_path / "monitor.db")
    judge = AttentionJudge(store, client=Jev(), clock=lambda: NOW)
    _traffic_burst(judge, ["report_a", "report_b"], base_qps=0)
    [burst] = store.inbox("new")
    for cycle in range(1, 4):
        _collect(store, NOW + cycle * 300)
        reconcile_recovery(store, "db", NOW + cycle * 300, [])
    [closed] = store.inbox("resolved")
    assert closed["id"] == burst["id"] and closed["resolved_by"] == "code"
    assert set(json.loads(closed["body_json"])["resolution"]["after"]) == {
        "report_a", "report_b"}


def test_a_burst_back_to_its_quiet_rate_closes(tmp_path):
    from features.monitor.judge import reconcile_recovery

    store = MonitorStore(tmp_path / "monitor.db")
    judge = AttentionJudge(store, client=Jev(), clock=lambda: NOW)
    _traffic_burst(judge, ["a", "b"], base_qps=0.1)
    # Filed at 1 call a second over a usual 0.1: 0.25 is under the middle (0.32).
    quiet = [{"query_key": key, "calls": 75, "mean_ms": 5, "qps": 0.25, "db_time_share": .1}
             for key in ("a", "b")]
    for cycle in range(1, 4):
        _collect(store, NOW + cycle * 300)
        reconcile_recovery(store, "db", NOW + cycle * 300, quiet)
    assert store.inbox("resolved") and not store.inbox("new")


def test_a_burst_still_elevated_stays_open(tmp_path):
    from features.monitor.judge import reconcile_recovery

    store = MonitorStore(tmp_path / "monitor.db")
    judge = AttentionJudge(store, client=Jev(), clock=lambda: NOW)
    _traffic_burst(judge, ["a", "b"], base_qps=0.1)
    busy = [{"query_key": key, "calls": 240, "mean_ms": 5, "qps": 0.8, "db_time_share": .1}
            for key in ("a", "b")]
    for cycle in range(1, 5):
        _collect(store, NOW + cycle * 300)
        reconcile_recovery(store, "db", NOW + cycle * 300, busy)
    assert len(store.inbox("new")) == 1 and not store.inbox("resolved")


def test_slowed_members_that_stop_running_are_back_to_normal(tmp_path):
    from features.monitor.judge import reconcile_recovery

    store = MonitorStore(tmp_path / "monitor.db")
    judge = AttentionJudge(store, client=Jev(), clock=lambda: NOW)
    _burst(judge, NOW, ["a", "b"])
    for cycle in range(1, 4):
        _collect(store, NOW + cycle * 300)
        reconcile_recovery(store, "db", NOW + cycle * 300, [])
    assert store.inbox("resolved")[0]["resolved_by"] == "code"


def test_recovery_ceiling_matches_the_incident_line():
    from features.monitor.judge import recovery_ceiling

    assert recovery_ceiling(0, 5) == 0
    assert recovery_ceiling(10, 30) == pytest.approx(17.32, abs=0.01)
    assert recovery_ceiling(10, 11) == pytest.approx(12)


# Repeats: the same queries bursting again are occurrences of one item

GAP = 50 * 60


def _occurrence(judge, store, ts, keys, *, mean_ms=30.0):
    from features.monitor.judge import reconcile_recovery

    rows = [row(key, mean_ms=mean_ms) for key in keys]
    verdict = {"event_scope": "database_wide", "queries": [
        {"query_key": key, "kind": "latency_regression", "attention": "notify",
         "severity": .8} for key in keys], "open_items": []}
    judge._file("db", ts, rows, {"target_numbers": {}}, verdict)
    normal = [{**entry, "mean_ms": 10.0} for entry in rows]
    for cycle in range(1, 4):
        reconcile_recovery(store, "db", ts + cycle * 300, normal)


def test_the_same_queries_bursting_again_are_one_item_with_occurrences(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    judge = AttentionJudge(store, client=Jev(), clock=lambda: NOW)
    keys = ["a", "b", "c"]
    _occurrence(judge, store, NOW, keys)
    [first] = store.inbox(target="db")
    assert first["state"] == "resolved"
    # The same leading query and the same queries plus one more: the same pattern.
    _occurrence(judge, store, NOW + GAP, keys + ["d"])
    items = store.inbox(target="db")
    assert len(items) == 1 and items[0]["id"] == first["id"]
    body = json.loads(items[0]["body_json"])
    assert [entry["start"] for entry in body["occurrences"]] == [NOW, NOW + GAP]
    assert body["occurrences"][0]["size"] == 3.0
    # Its latest occurrence is over, but it waits for the quiet period before closing.
    assert items[0]["state"] == "new"


def test_a_routine_repeat_lists_quietly_and_a_bigger_one_resurfaces(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    judge = AttentionJudge(store, client=Jev(), clock=lambda: NOW)
    keys = ["a", "b"]
    for index in range(3):
        _occurrence(judge, store, NOW + index * GAP, keys)
    [item] = store.inbox(target="db")
    store.action_item(item["id"], "seen", NOW + 3 * GAP - 1)
    pushes = len([e for e in store.inbox_events() if e["kind"] == "notified"])
    # The fourth, on schedule and the usual size: routine.
    _occurrence(judge, store, NOW + 3 * GAP, keys)
    [item] = store.inbox(target="db")
    body = json.loads(item["body_json"])
    assert item["state"] == "seen" and body["occurrences"][-1]["verdict"] == "usual"
    assert len([e for e in store.inbox_events() if e["kind"] == "notified"]) == pushes
    # The fifth is twice as big: it resurfaces and says why.
    _occurrence(judge, store, NOW + 4 * GAP, keys, mean_ms=70.0)
    [item] = store.inbox(target="db")
    body = json.loads(item["body_json"])
    assert item["state"] == "new" and body["occurrences"][-1]["verdict"] == "bigger"
    assert next(iter(body["recurring"].values()))["verdict"] == "bigger"
    assert "worse" in [e["kind"] for e in store.inbox_events()]


def test_a_repeating_item_closes_after_twice_its_usual_gap(tmp_path):
    from features.monitor.judge import reconcile_recovery

    store = MonitorStore(tmp_path / "monitor.db")
    judge = AttentionJudge(store, client=Jev(), clock=lambda: NOW)
    for index in range(2):
        _occurrence(judge, store, NOW + index * GAP, ["a", "b"])
    last = NOW + GAP
    normal = [{**row(key), "mean_ms": 10.0} for key in ("a", "b")]
    reconcile_recovery(store, "db", last + 2 * GAP - 300, normal)
    assert store.inbox(target="db")[0]["state"] == "new"
    reconcile_recovery(store, "db", last + 2 * GAP, normal)
    [item] = store.inbox(target="db")
    assert (item["state"], item["resolved_by"]) == ("resolved", "code")
    assert json.loads(item["body_json"])["repeat_quiet_s"] == 2 * GAP


def test_a_repeating_item_reads_as_one_pattern(tmp_path):
    from features.monitor.presentation import present_inbox_item

    store = MonitorStore(tmp_path / "monitor.db")
    judge = AttentionJudge(store, client=Jev(), clock=lambda: NOW)
    keys = ["aaaaaaaaaaa1", "aaaaaaaaaaa2"]
    for index in range(3):
        _occurrence(judge, store, NOW + index * GAP, keys)
    [item] = store.inbox(target="db")
    shown = present_inbox_item(item, dict(zip(keys, ("Report by day", "Report by tier"))),
                               [], set())
    assert re.fullmatch(r"Report by (day|tier) and 1 more query burst 3 times "
                        r"(today|since yesterday|in \d+ days)", shown["title"])
    assert [entry["start"] for entry in shown["occurrences"]] == [
        NOW + 2 * GAP, NOW + GAP, NOW]
    assert shown["occurrences"][0]["query_count"] == 2


def test_routine_bursts_kept_from_jev_still_count_as_occurrences(tmp_path):
    from features.monitor.judge import reconcile_recovery

    store = MonitorStore(tmp_path / "monitor.db")
    judge = AttentionJudge(store, client=Jev(), clock=lambda: NOW)
    for index in range(2):
        _occurrence(judge, store, NOW + index * GAP, ["a", "b"])
    events = len(store.inbox_events())
    later = NOW + 2 * GAP
    gated = {key: {"reason": "recurring", "pattern": {}} for key in ("a", "b")}
    judge.note_routine_occurrences("db", later, gated)
    [item] = store.inbox(target="db")
    last = json.loads(item["body_json"])["occurrences"][-1]
    assert (last["start"], last["quiet"], last["end"]) == (later, True, None)
    assert len(store.inbox_events()) == events
    # Still flagged as routine: it lasts; then quiet: it ends.
    reconcile_recovery(store, "db", later + 300, [], {"a", "b"})
    reconcile_recovery(store, "db", later + 600, [], set())
    last = json.loads(store.inbox(target="db")[0]["body_json"])["occurrences"][-1]
    assert last["end"] == later + 600



# Intermittent queries going quiet is not news

def test_a_query_that_only_runs_in_bursts_does_not_file_as_stopped():
    from features.monitor.recurring import intermittent

    onsets = [HOUR + index * 55 * 60 for index in range(8)]
    points = series(onsets, span=onsets[-1] + HOUR)
    evidence = intermittent(points, onsets[-1] + HOUR)
    assert evidence and evidence["kind"] == "intermittent" and evidence["busy_share"] < .5


def test_a_steady_query_that_stops_still_files():
    from features.monitor.recurring import intermittent

    steady = [(ts, 30.0) for ts in range(0, 20 * HOUR, BUCKET_S)]
    silent = [(ts, 0.0) for ts in range(20 * HOUR, 21 * HOUR, BUCKET_S)]
    assert intermittent(steady + silent, 21 * HOUR) is None
    # Two earlier outages of 45 minutes don't make it intermittent either.
    points = [(ts, 0.0 if 5 * HOUR <= ts < 5.75 * HOUR or 12 * HOUR <= ts < 12.75 * HOUR
               else value) for ts, value in steady] + silent
    assert intermittent(points, 21 * HOUR) is None


def test_gate_keeps_an_intermittent_stop_from_jev():
    rel = Top()
    stopped = {**row("top", calls=0, mean_ms=0), "reason": "stopped"}
    gate = attention_gate(rel, 50_000, intermittent=lambda row: {"kind": "intermittent"})
    assert gate(stopped) == {"reason": "intermittent", "pattern": {"kind": "intermittent"}}
    assert attention_gate(rel, 50_000, intermittent=lambda row: None)(stopped) is None



def _led(judge, store, ts, keys, top):
    """A burst whose leading query by database time is `top`."""
    from features.monitor.judge import reconcile_recovery

    rows = [row(key, calls=3000 if key == top else 300) for key in keys]
    verdict = {"event_scope": "database_wide", "queries": [
        {"query_key": key, "kind": "latency_regression", "attention": "inbox",
         "severity": .8} for key in keys], "open_items": []}
    judge._file("db", ts, rows, {"target_numbers": {}}, verdict)
    for cycle in range(1, 4):
        reconcile_recovery(store, "db", ts + cycle * 300,
                           [{**entry, "mean_ms": 10.0} for entry in rows])


def test_repeats_match_on_their_leading_query(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    judge = AttentionJudge(store, client=Jev(), clock=lambda: NOW)
    _led(judge, store, NOW, ["top", "b", "c", "d"], "top")
    # Led by the same query, sharing half the smaller set (2 of 3): the same pattern, though
    # only 50% of the larger set overlaps.
    _led(judge, store, NOW + GAP, ["top", "b", "x"], "top")
    assert len(store.inbox(target="db")) == 1
    body = json.loads(store.inbox(target="db")[0]["body_json"])
    assert [entry["top"] for entry in body["occurrences"]] == ["top", "top"]
    # The same queries led by another one: a different pattern.
    _led(judge, store, NOW + 2 * GAP, ["top", "b", "x"], "x")
    assert len(store.inbox(target="db")) == 2


def test_a_heavier_newcomer_does_not_hide_a_repeat(tmp_path):
    from features.monitor.judge import reconcile_recovery

    store = MonitorStore(tmp_path / "monitor.db")
    judge = AttentionJudge(store, client=Jev(), clock=lambda: NOW)
    _led(judge, store, NOW, ["top", "b", "c"], "top")
    # The same burst, plus a query that wasn't in it and now outweighs its leader.
    keys = ["top", "b", "c", "new"]
    rows = [row(key, calls={"new": 9000, "top": 3000}.get(key, 300)) for key in keys]
    verdict = {"event_scope": "database_wide", "queries": [
        {"query_key": key, "kind": "latency_regression", "attention": "inbox",
         "severity": .8} for key in keys], "open_items": []}
    judge._file("db", NOW + GAP, rows, {"target_numbers": {}}, verdict)
    for cycle in range(1, 4):
        reconcile_recovery(store, "db", NOW + GAP + cycle * 300,
                           [{**entry, "mean_ms": 10.0} for entry in rows])
    [item] = store.inbox(target="db")
    body = json.loads(item["body_json"])
    assert [entry["start"] for entry in body["occurrences"]] == [NOW, NOW + GAP]


def test_a_repeat_judged_separate_still_joins_its_pattern(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    judge = AttentionJudge(store, client=Jev(), clock=lambda: NOW)
    _led(judge, store, NOW, ["top", "b", "c", "d"], "top")
    # The same burst, smaller: two of its queries, which the check called separate changes.
    keys = ["top", "b"]
    rows = [row(key, calls=3000 if key == "top" else 300) for key in keys]
    verdict = {"event_scope": "separate", "queries": [
        {"query_key": key, "kind": "latency_regression", "attention": "inbox",
         "severity": .8} for key in keys], "open_items": []}
    judge._file("db", NOW + GAP, rows, {"target_numbers": {}}, verdict)
    [item] = store.inbox(target="db")
    assert item["type"] == "many_queries_changed"
    body = json.loads(item["body_json"])
    assert [entry["start"] for entry in body["occurrences"]] == [NOW, NOW + GAP]
    # Unrelated queries judged separate stay separate items.
    others = ["x", "y"]
    verdict = {**verdict, "queries": [{**verdict["queries"][0], "query_key": key}
                                      for key in others]}
    judge._file("db", NOW + 2 * GAP, [row(key) for key in others], {"target_numbers": {}},
                verdict)
    assert {item["subject"] for item in store.inbox(target="db")} >= {"x", "y"}


def test_a_routine_repeat_ranks_watch_unless_a_bigger_occurrence_lasts():
    from features.monitor.presentation import routine_repeat

    usual = {"occurrences": [{"start": 1, "verdict": None}, {"start": 2, "verdict": "usual",
                                                            "end": None}]}
    assert routine_repeat(usual)
    bigger = {"occurrences": [{"start": 1, "verdict": "usual"},
                              {"start": 2, "verdict": "bigger", "end": None}]}
    assert not routine_repeat(bigger)
    bigger["occurrences"][-1]["end"] = 3
    assert routine_repeat(bigger)
    assert not routine_repeat({"occurrences": [{"start": 1, "verdict": None},
                                               {"start": 2, "verdict": None}]})
