"""The Report page's shape: verdict, the day, Fix first, Health, the run, numbers only when
signed out, and the merge rules (dedupe, clamped shares, one index for several queries)."""

from __future__ import annotations

from datetime import datetime

import pytest

from features.monitor import database_limit, report_v2
from features.monitor.api import models
from features.monitor.tests.conftest import KEYS, NOW, TEXTS, seed

DAY = int(datetime(2026, 10, 7).timestamp())
POSTS_SQL = "CREATE INDEX idx_posts_owneruserid ON posts (owneruserid, creationdate);"
VOTES_SQL = "CREATE INDEX idx_votes_postid ON votes (postid);"


def _digest(key: str, sql: str) -> dict:
    table = "votes" if "votes" in sql else "posts"
    return {"query_key": key, "analysis_ref": f"/api/query-registry/{key}/analysis/a_{key}",
            "analyzed_at": NOW - 600, "mode": "explain", "mode_label": "Plan only (EXPLAIN)",
            "rating": "fair", "efficiency_score": 42, "plan_summary": None,
            "findings": [f"Sequential scan on {table}."],
            "fix": {"kind": "index", "title": f"Add an index on {table}(x)", "sql": sql,
                    "why": "No index.", "expected_gain": None},
            "other_fixes": [{"kind": "rewrite", "title": "Rewrite the query", "sql": "SELECT 1",
                             "why": "w", "expected_gain": None}] if key == KEYS[0] else [],
            "headline": f"Needs an index on {table}(x)", "fix_ready": True}


def _check(check: str, label: str, status: str, value: str) -> dict:
    return {"check": check, "label": label, "status": status, "value": value}


def _action(**fields) -> dict:
    base = {"evidence": [], "steps": "", "rows": [], "sql": None, "setting": None,
            "restart": False, "caveat": None, "query_keys": [], "why": ""}
    return {**base, **fields}


def _snapshot() -> dict:
    numbers = [("qps", 11.7, 11.0), ("mean_ms", 15.9, 15.9), ("active_conns_peak", 8, 8),
               ("cache_hit_ratio_min", 0.115, 0.94), ("xact_rollback", 143, 20), ("deadlocks", 0, 0)]
    return {
        "version": 3,
        "period": {"start": DAY, "end": DAY + 86400, "partial": False, "day": "2026-10-07"},
        "numbers": [{"metric": metric, "label": metric, "unit": "count", "value": value,
                     "prior": prior, "change": value / prior - 1 if prior else None,
                     "better": None} for metric, value, prior in numbers],
        "peak": {"qps": 15.9, "ts": DAY + 9 * 3600 + 50 * 60},
        "chart": None, "events": [], "queries": [{"query_key": key} for key in KEYS[:4] * 3],
        "look_at": [], "health": {"problems": [], "fine": [], "unavailable": []},
        "overview": {"status": "bad", "label": "Needs attention", "headline": "", "checks": [
            _check("cache", "Cache hit", "bad", "Fell to 11.5%"),
            _check("connections", "Connections", "ok", "8 of 100"),
            _check("vacuum", "Vacuum and bloat", "ok", "Fine"),
            _check("indexes", "Indexes", "ok", "Fine"),
            _check("settings", "Settings", "warn", "4 things to change"),
            _check("replication", "Replication", "ok", "None"),
            _check("transactions", "Rollbacks and deadlocks", "warn", "143 rollbacks"),
            _check("sequential_scans", "Full table scans", "ok", "None")]},
        "actions": [
            _action(id="setting_shared_buffers", check="settings",
                    title="Raise shared_buffers to 2 GB", severity="bad",
                    finding="shared_buffers is 128 MB, PostgreSQL's out-of-the-box default.",
                    why="Lifts cache hit for every query.",
                    sql="ALTER SYSTEM SET shared_buffers = '2GB';",
                    setting={"name": "shared_buffers", "current": "128MB", "target": "2GB"},
                    restart=True),
            _action(id="rollbacks", check="transactions", title="Find why transactions roll back",
                    severity="warn", finding="143 transactions rolled back.",
                    why="Nothing in the queries explains it yet."),
            _action(id="unused_indexes", check="indexes", title="Drop 1 unused index",
                    severity="warn", finding="1 index has no reads, 41 MB in all.",
                    sql="DROP INDEX CONCURRENTLY public.votes_creationdate_idx;")],
        "vitals": None, "spikes": [], "engine": "postgresql", "names": {}, "facts": [],
        "brief": [], "brief_source": "facts"}


def _stored(share_scale: float = 1.0) -> dict:
    top = [{"query_key": KEYS[0], "text": TEXTS[KEYS[0]], "total_ms": 1900,
            "db_time_share": 0.19 * share_scale},
           {"query_key": KEYS[1], "text": TEXTS[KEYS[1]], "total_ms": 1700,
            "db_time_share": 0.17 * share_scale},
           {"query_key": KEYS[2], "text": TEXTS[KEYS[2]], "total_ms": 1200,
            "db_time_share": 0.12 * share_scale}]
    card = {"query_key": KEYS[0], "title": "Add an index on posts", "why": "w",
            "action": "Add an index on posts(owneruserid, creationdate)"}
    return {"summary": "The day in one line.",
            "fix_first": [card, {**card, "query_key": KEYS[1], "title": "Add an index on votes",
                                 "action": "Add an index on votes(postid)"},
                          {**card, "query_key": KEYS[2]}, dict(card)],
            "one_fix_several_queries": [
                {"table": "posts", "columns": ["owneruserid", "creationdate"],
                 "query_keys": [KEYS[0], KEYS[2], KEYS[2]], "action": POSTS_SQL},
                {"table": "posts", "columns": ["owneruserid", "creationdate"],
                 "query_keys": [KEYS[0]], "action": POSTS_SQL}],
            "health": [{"check": "cache", "status": "bad", "note": "n"},
                       {"check": "cache", "status": "bad", "note": "again"}],
            "changed_since_yesterday": [],
            "computed": {"top_queries": top, "deltas": {},
                         "analyses": [{"query_key": key, "analysis_ref": f"/a/{key}",
                                       "mode": "explain"} for key in KEYS[:3]] +
                         [{"query_key": KEYS[3], "provisional": True}] * 9},
            "snapshot": _snapshot(), "model": "Claude Sonnet 4.6", "provisional_query_keys": [],
            "narrative_error": None}


@pytest.fixture
def signed_in(monkeypatch):
    monkeypatch.setattr(database_limit, "_signed_in", lambda: True)


@pytest.fixture
def digests(monkeypatch):
    found = {KEYS[0]: _digest(KEYS[0], POSTS_SQL), KEYS[1]: _digest(KEYS[1], VOTES_SQL),
             KEYS[2]: _digest(KEYS[2], POSTS_SQL)}
    monkeypatch.setattr(report_v2, "digest_for_ref",
                        lambda target, key, ref, mode, finished_at=None: found.get(key))


def _run(store, report: dict | None = None) -> int:
    seed(store, minutes=3)
    run, _ = store.claim_report("db", "nightly", DAY + 86400, DAY)
    store.update_report(run["id"], state="done", finished_at=DAY + 86400 + 61,
                        report=report or _stored(),
                        period=(DAY, DAY + 86400))
    fixed = store.upsert_item("db", "fix_confirmed", KEYS[3], .5, "Fix confirmed: SELECT on users",
                              {"query_key": KEYS[3], "table": "users", "columns": ["id"],
                               "index_name": "idx_users_id", "detected_at": DAY + 8 * 3600,
                               "before_ms": 27.6, "after_ms": 1.9, "calls": 40},
                              DAY + 8 * 3600, delivery="inbox")
    store.action_item(fixed["id"], "resolve", DAY + 8 * 3600 + 120, resolved_by="code")
    return run["id"]


def test_report_reads_as_verdict_day_fix_first_health_and_run(client, store, signed_in, digests):
    run_id = _run(store)
    page = client.get(f"/api/monitor/targets/db/report/{run_id}").json()
    assert page["narrative_missing"] is False
    assert page["verdict"]["lead"].endswith(":")
    # Every warning the page shows as needing a look counts, rollbacks included.
    assert "3 fixes are ready, 3 of 8 checks need you, everything else held." in page["verdict"]["rest"]
    day = page["day"]
    assert day["label"] == "October 7, vs the day before" and day["query_count"] == 12
    tiles = {tile["metric"]: tile for tile in day["tiles"]}
    assert (tiles["qps"]["value"], tiles["qps"]["detail"]) == (11.7, "peak 15.9 at 9:50")
    assert tiles["mean_ms"]["detail"] == "+0% vs the day before"
    assert (tiles["connections"]["value"], tiles["connections"]["limit"],
            tiles["connections"]["detail"]) == (8, 100, "0 deadlocks")
    assert (tiles["cache_hit"]["state"], tiles["cache_hit"]["reason"]) == \
        ("bad", "shared_buffers is the default")
    fixes = page["fix_first"]
    assert [(fix["rank"], fix["state"]) for fix in fixes] == [
        (1, "needs_attention"), (2, "ready"), (3, "ready"), (4, "worth_doing"), (None, "fixed"),
        (5, "smaller")]
    assert fixes[0]["title"] == "Raise shared_buffers to 2 GB"
    assert fixes[0]["detail"] == "Restart needed. Lifts cache hit for every query."
    assert fixes[0]["restart"] and fixes[0]["kind"] == "config" and fixes[0]["sql"].startswith("ALTER")
    assert fixes[1]["title"] == "Index posts (owneruserid, creationdate)"
    assert fixes[1]["detail"] == "2 queries, 31% of time" and fixes[1]["share"] == pytest.approx(0.31)
    assert fixes[1]["group"] == "posts (owneruserid, creationdate)"
    assert fixes[1]["query_keys"] == [KEYS[0], KEYS[2]]
    assert fixes[1]["sql"].startswith("CREATE INDEX CONCURRENTLY")
    assert (fixes[2]["title"], fixes[2]["detail"]) == ("Index votes (postid)", "17% of time")
    assert fixes[3]["title"] == "Find why transactions roll back"
    assert (fixes[4]["title"], fixes[4]["before_ms"], fixes[4]["after_ms"]) == \
        ("Index users (id)", 27.6, 1.9)
    assert fixes[4]["detail"].startswith("Fixed ") and "27.6 ms to 1.9 ms" in fixes[4]["detail"]
    assert fixes[5]["title"] == "Drop 1 unused index"
    health = page["health"]
    assert [(row["check"], row["state"], row["fix_rank"]) for row in health] == [
        ("cache", "bad", 1), ("settings", "warn", 1), ("transactions", "warn", 4)]
    assert [(row["needs_you"], row["in_actions"]) for row in health] == [
        (True, True), (True, True), (True, False)]
    assert health[0]["finding"] == "Cache hit fell to 11.5%"
    assert health[2]["detail"] == "Nothing in the queries explains it yet."
    assert page["health_fine"] == ["Connections", "Vacuum and bloat", "Indexes", "Replication",
                                   "Full table scans"]
    assert page["run"] == {"number": 1, "trigger": "nightly", "ran_at": DAY + 86400,
                           "model": "Claude Sonnet 4.6", "analyses": 3, "rewrites": 1}
    # The merge deduplicated the repeated card, the repeated health check and the shared fix.
    body = page["report_json"]
    assert [card["query_key"] for card in body["fix_first"]] == KEYS[:3]
    assert len(body["health"]) == 1
    assert len(body["one_fix_several_queries"]) == 1
    assert body["one_fix_several_queries"][0]["query_keys"] == [KEYS[0], KEYS[2]]
    assert body["one_fix_several_queries"][0]["group"] == "posts (owneruserid, creationdate)"


def test_signed_out_report_is_numbers_only(client, store, digests, monkeypatch):
    monkeypatch.setattr(database_limit, "_signed_in", lambda: False)
    run_id = _run(store)
    page = client.get(f"/api/monitor/targets/db/report/{run_id}").json()
    assert page["narrative_missing"] is True and page["verdict"] is None
    assert page["day"]["tiles"][0]["value"] == 11.7
    assert [fix["title"] for fix in page["fix_first"]][:2] == \
        ["Raise shared_buffers to 2 GB", "Index posts (owneruserid, creationdate)"]
    assert all(fix["sql"] is None for fix in page["fix_first"])
    assert page["health"][0]["check"] == "cache"


def test_shares_over_one_are_percentages_and_clamp(client, store, signed_in, digests):
    run_id = _run(store, _stored(share_scale=100))
    body = client.get(f"/api/monitor/targets/db/report/{run_id}").json()["report_json"]
    assert [card["share"] for card in body["fix_first"]] == pytest.approx([0.19, 0.17, 0.12])
    assert body["one_fix_several_queries"][0]["share"] == pytest.approx(0.31)
    assert report_v2.clamp_share(-0.2) == 0.0 and report_v2.clamp_share(250) == 1.0


def test_a_running_or_empty_run_has_an_empty_view(client, store, signed_in):
    seed(store, minutes=3)
    run, _ = store.claim_report("db", "user", NOW - 30, NOW - 30)
    page = client.get(f"/api/monitor/targets/db/report/{run['id']}").json()
    assert page["state"] == "running" and page["day"] is None and page["fix_first"] == []
    assert page["run"] is None and page["narrative_missing"] is False
    assert page["copy_count"] == 0 and page["detailed"] is None


def test_detailed_view_adds_its_sections_from_the_same_numbers(client, store, signed_in, digests):
    run_id = _run(store)
    store.sync_indexes("db", [], DAY + 3600)
    store.sync_indexes("db", [{"schema": "public", "table": "users",
                               "index_name": "idx_users_displayname",
                               "definition": "CREATE INDEX idx_users_displayname ON public.users "
                                             "USING btree (displayname)"}], DAY + 16 * 3600)
    store.upsert_item("db", "traffic_spike", KEYS[0], .7, "Traffic jumped", {}, DAY + 9 * 3600)
    quick = client.get(f"/api/monitor/targets/db/report/{run_id}").json()
    assert quick["detailed"] is None
    # Copy all counts the rows that carry SQL: shared_buffers, two indexes and the drop.
    assert quick["copy_count"] == 4
    page = client.get(f"/api/monitor/targets/db/report/{run_id}?view=detailed").json()
    models.ReportDetailed.model_validate(page["detailed"])
    assert {key: page[key] for key in ("verdict", "fix_first", "copy_count")} == \
        {key: quick[key] for key in ("verdict", "fix_first", "copy_count")}
    detailed = page["detailed"]
    tiles = {tile["metric"]: tile for tile in detailed["tiles"]}
    assert list(tiles) == ["qps", "mean_ms", "p95_ms", "connections", "cache_hit",
                           "rollbacks_deadlocks"]
    assert (tiles["qps"]["value"], tiles["qps"]["prior"]) == (11.7, 11.0)
    assert (tiles["cache_hit"]["state"], tiles["cache_hit"]["prior"]) == ("bad", 0.94)
    assert (tiles["connections"]["limit"], tiles["rollbacks_deadlocks"]["value"],
            tiles["rollbacks_deadlocks"]["second"]) == (100, 143, 0)
    assert tiles["p95_ms"]["state"] == "collecting"
    checks = {row["check"]: row for row in detailed["health_checks"]}
    assert len(checks) == 8 and detailed["health_checks"][0]["check"] == "cache"
    assert (checks["cache"]["number"], checks["cache"]["fix_rank"]) == ("11.5% lowest", 1)
    assert checks["connections"]["number"] == "8 of 100"
    assert (checks["transactions"]["number"], checks["transactions"]["fix_rank"]) == \
        ("143 and 0", 4)
    assert checks["replication"]["fix_rank"] is None
    assert {name for name, row in checks.items() if row["needs_you"]} == \
        {"cache", "settings", "transactions"}
    assert {name for name, row in checks.items() if row["in_actions"]} == {"cache", "settings"}
    assert detailed["compared"] is True and tiles["qps"]["prior_note"] is None
    assert detailed["fixes_confirmed"] == [{
        "title": "Index users (id)", "applied_at": DAY + 8 * 3600,
        "confirmed_at": DAY + 8 * 3600 + 120, "before_ms": 27.6, "after_ms": 1.9,
        "query_keys": [KEYS[3]]}]
    [change] = detailed["index_changes_seen"]
    assert (change["kind"], change["index_name"], change["from_rdst"]) == \
        ("added", "idx_users_displayname", False)
    assert [row["text"] for row in detailed["what_rdst_did"]] == [
        "Filed an Inbox item: Traffic jumped"]
    assert detailed["what_changed"] == [] and detailed["spikes"] == []
    totals = detailed["totals"]
    assert (totals["queries_seen"], totals["report_number"], totals["data_kept_days"]) == \
        (12, 1, 7)
    latest = client.get("/api/monitor/targets/db/report/latest?view=detailed").json()
    assert latest["detailed"]["tiles"] == detailed["tiles"]


def test_everything_else_held_only_without_slower_or_new_heavy_queries(
        client, store, signed_in, digests):
    stored = _stored()
    stored["snapshot"]["queries"] = [
        {"query_key": KEYS[0], "display_name": "SELECT on posts", "share": 0.17,
         "mean_ms": 65.0, "prior_mean_ms": 48.7, "prior_calls": 100, "latency_change": 0.335,
         "new": False, "text": TEXTS[KEYS[0]]},
        {"query_key": KEYS[3], "display_name": "SELECT on users", "share": 0.01,
         "mean_ms": 1.9, "prior_mean_ms": 27.6, "prior_calls": 100, "latency_change": -0.93,
         "new": False, "text": TEXTS[KEYS[3]]},
        {"query_key": KEYS[4], "display_name": "COUNT on posts", "share": 0.096,
         "mean_ms": 63.3, "prior_mean_ms": None, "prior_calls": 0, "latency_change": None,
         "new": True, "text": TEXTS[KEYS[4]]}]
    stored["snapshot"]["events"] = [{"ts": DAY + 8 * 3600, "kind": "index", "type": "index_added",
                                     "what": "Index added", "title": "idx_users_id on users",
                                     "query_keys": []}]
    run_id = _run(store, stored)
    page = client.get(f"/api/monitor/targets/db/report/{run_id}?view=detailed").json()
    assert page["verdict"]["rest"].endswith(
        "3 of 8 checks need you, 1 query got slower and 1 new heavy query.")
    rows = [(row["kind"], row["query_key"]) for row in page["detailed"]["what_changed"]]
    assert rows == [("slower", KEYS[0]), ("faster", KEYS[3]), ("new_heavy", KEYS[4])]
    faster = page["detailed"]["what_changed"][1]
    assert faster["detail"].startswith("After the index added at ")
    assert (faster["before_ms"], faster["after_ms"]) == (27.6, 1.9)


def test_a_first_day_says_the_day_before_has_no_data(client, store, signed_in, digests):
    stored = _stored()
    day = stored["snapshot"]
    day["period"]["prior_start"] = day["period"]["prior_end"] = None
    day["numbers"] = [{**row, "prior": None, "change": None} for row in day["numbers"]]
    run_id = _run(store, stored)
    page = client.get(f"/api/monitor/targets/db/report/{run_id}?view=detailed").json()
    models.ReportDay.model_validate(page["day"])
    models.ReportDetailed.model_validate(page["detailed"])
    assert page["day"]["label"] == "October 7, no data for the day before"
    assert page["day"]["compared"] is False and page["detailed"]["compared"] is False
    assert page["verdict"]["rest"].endswith("3 of 8 checks need you.")
    assert {tile["prior_note"] for tile in page["detailed"]["tiles"]} == \
        {"No data for the day before"}
    assert all(tile["prior"] is None and tile["change"] is None
               for tile in page["detailed"]["tiles"])


def test_the_settings_audit_agrees_with_the_settings_on_actions(client, store, signed_in, digests):
    from features.monitor import actions

    stored = _stored()
    day = stored["snapshot"]
    # Two settings off a rule of thumb: the audit lists them, Actions files neither.
    day["actions"] = [
        {**action, "id": f"setting_{name}", "check": "settings", "severity": "warn",
         "setting": {"name": name, "current": current, "target": target}}
        for action in day["actions"][:1]
        for name, current, target in (("work_mem", "4MB", "64MB"),
                                      ("random_page_cost", "4", "1.1"))]
    stored["computed"]["health_collectors"] = {"config_audit": {"settings": [
        {"parameter": "work_mem", "current": "4MB", "recommended": "64MB", "status": "warn"},
        {"parameter": "random_page_cost", "current": "4", "recommended": "1.1",
         "status": "warn"}]}}
    run_id = _run(store, stored)
    page = client.get(f"/api/monitor/targets/db/report/{run_id}?view=detailed").json()
    detailed = page["detailed"]
    assert [row["in_actions"] for row in detailed["settings_audit"]] == [False, False]
    assert actions._database(day["actions"], {}) == []
    checks = {row["check"]: row for row in detailed["health_checks"]}
    # The settings still need a look; only the report lists them.
    assert (checks["settings"]["number"], checks["settings"]["needs_you"],
            checks["settings"]["in_actions"]) == ("2 off recommendation", True, False)
    assert page["verdict"]["rest"].endswith("3 of 8 checks need you, everything else held.")


def test_busiest_stretches_stand_in_when_no_spike_crossed():
    xs = [DAY + minute * 300 for minute in range(12)]
    load = [1, 1, 1, 6, 7, 1, 1, 1, 1, 4, 1, 1]
    chart = {"res": 300, "xs": xs, "qps": [load, [1] * 12],
             "ms": [[value * 10 for value in load], [5] * 12],
             "bands": [{"key": KEYS[0], "kind": "query", "display_name": "SELECT on posts"},
                       {"key": "other", "kind": "other", "display_name": "Everything else"}]}
    vitals = {"xs": xs, "xact_rollback": [0, 0, 0, 100, 21, 0, 0, 0, 0, 3, 0, 0]}
    stretches = report_v2.busy_stretches({"chart": chart, "vitals": vitals, "spikes": []})
    assert [(stretch["kind"], stretch["start"], stretch["end"]) for stretch in stretches] == [
        ("busiest", xs[3], xs[4] + 300), ("busiest", xs[9], xs[9] + 300)]
    assert stretches[0]["rollbacks"] == 121 and stretches[0]["queries"][0]["query_key"] == KEYS[0]
    assert stretches[0]["queries"][0]["share"] == pytest.approx(130 / 140, abs=1e-4)
