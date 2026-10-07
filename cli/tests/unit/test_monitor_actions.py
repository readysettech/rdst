from features.monitor import actions
from features.monitor.index_plan import build_plan
from shared.monitor.monitor_store import MonitorStore

NOW = 1_000_000


def _plan(**inputs):
    base = {"engine": "postgresql", "now": NOW, "observed_s": 3600, "counter_age_s": 30 * 86400,
            "existing": [], "tables": {}, "queries": []}
    return build_plan({**base, **inputs})


def _orders_plan():
    return _plan(queries=[{"key": "q1", "name": "SELECT on orders", "share": 0.4, "kind": "select",
                           "suggestions": [{"table": "orders", "columns": ["customer_id"],
                                            "gain_pct": 80, "verified": True}]}])


def _snapshot(store, *indexes):
    store.sync_indexes("db", [{"schema": "public", "table": table, "index_name": name,
                               "definition": f"CREATE INDEX {name} ON public.{table} USING btree ({cols})"}
                              for table, name, cols in indexes], NOW - 600)


def test_a_new_candidate_opens_with_fixed_wording(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    _snapshot(store)
    found = actions.candidates(store, "db", _orders_plan(), [], [])
    [opened] = actions.reconcile(store, "db", NOW, found)
    assert opened["type"] == "add_index" and opened["state"] == "open"
    assert opened["payload"]["title"] == "Add an index on orders(customer_id)"
    assert opened["payload"]["summary"] == "Should speed up 1 query that takes 40% of database time"
    assert [event["kind"] for event in store.action_events(opened["id"])] == ["opened"]


def test_an_index_that_shows_up_closes_the_action_as_applied(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    _snapshot(store)
    found = actions.candidates(store, "db", _orders_plan(), [], [])
    actions.reconcile(store, "db", NOW, found)
    _snapshot(store, ("orders", "orders_customer", "customer_id, created_at"))
    # The next plan no longer lists it because the new index covers it.
    assert actions.reconcile(store, "db", NOW + 300, []) == []
    [row] = store.actions("db")
    assert (row["state"], row["closed_reason"]) == ("applied", "index_added")
    assert store.action_events(row["id"])[-1]["detail"] == {"index_name": "orders_customer"}


def test_evidence_that_fades_retires_and_a_dismissal_holds(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    _snapshot(store)
    found = actions.candidates(store, "db", _orders_plan(), [], [])
    [row] = actions.reconcile(store, "db", NOW, found)
    actions.set_state(store, "db", row["id"], "dismiss", NOW + 10, reason="we batch these")
    assert actions.reconcile(store, "db", NOW + 300, found) == []
    assert store.actions("db")[0]["dismiss_reason"] == "we batch these"
    actions.set_state(store, "db", row["id"], "reopen", NOW + 400)
    actions.reconcile(store, "db", NOW + 600, [])
    assert store.actions("db")[0]["closed_reason"] == "evidence_faded"


def test_a_snooze_lapses(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    _snapshot(store)
    found = actions.candidates(store, "db", _orders_plan(), [], [])
    [row] = actions.reconcile(store, "db", NOW, found)
    actions.set_state(store, "db", row["id"], "snooze", NOW, until=NOW + 1000)
    assert actions.reconcile(store, "db", NOW + 500, found) == []
    assert [item["id"] for item in actions.reconcile(store, "db", NOW + 1500, found)] == [row["id"]]


def test_setting_actions_close_when_the_setting_changes(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    _snapshot(store)
    database = [{"id": "setting_work_mem", "check": "settings", "title": "Raise work_mem",
                 "severity": "bad", "finding": "work_mem is 4MB.", "sql": "ALTER SYSTEM ...",
                 "setting": {"name": "work_mem", "current": "4MB", "target": "16MB"},
                 "restart": False}]
    reading = {"engine": "postgresql", "version": 160000, "counter_since": 1, "tables": [],
               "indexes": []}
    store.record_stats("db", {**reading, "taken_at": NOW - 60,
                              "settings": {"work_mem": {"value": "4096", "unit": "kB"}}})
    found = actions.candidates(store, "db", _plan(), [], database)
    [row] = actions.reconcile(store, "db", NOW, found)
    assert row["type"] == "change_setting" and row["payload"]["apply"] == "setting"
    # Between checkups the stored database actions still list it; the setting read closes it.
    store.record_stats("db", {**reading, "taken_at": NOW + 300,
                              "settings": {"work_mem": {"value": "16384", "unit": "kB"}}})
    assert actions.reconcile(store, "db", NOW + 300, found, database_refreshed=False) == []
    assert store.actions("db")[0]["closed_reason"] == "setting_changed"


def test_database_actions_wait_for_the_next_checkup_before_retiring(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    _snapshot(store)
    database = [{"id": "vacuum", "check": "vacuum", "title": "Vacuum 2 tables",
                 "severity": "warn", "finding": "Dead rows piled up.", "sql": "VACUUM a;"}]
    actions.reconcile(store, "db", NOW, actions.candidates(store, "db", _plan(), [], database))
    assert len(actions.reconcile(store, "db", NOW + 300, [], database_refreshed=False)) == 1
    assert actions.reconcile(store, "db", NOW + 600, [], database_refreshed=True) == []


def test_drop_actions_rank_write_heavy_unused_indexes_above_duplicates(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    existing = [{"schema": "public", "table": "events", "index_name": "events_kind",
                 "columns": ["kind"], "scans": 0, "window_scans": 0, "size_bytes": 1 << 20},
                {"schema": "public", "table": "orders", "index_name": "orders_a",
                 "columns": ["a"], "scans": 5, "window_scans": 5, "size_bytes": 2 << 20},
                {"schema": "public", "table": "orders", "index_name": "orders_ab",
                 "columns": ["a", "b"], "scans": 5, "window_scans": 5}]
    plan = _plan(existing=existing, tables={"events": {"writes_per_s": 300}})
    found = actions.candidates(store, "db", plan, [], [])
    ordered = sorted(found, key=lambda item: -item["score"])
    assert [item["payload"]["index_name"] for item in ordered] == ["events_kind", "orders_a"]
    assert ordered[0]["payload"]["summary"] == (
        "Not read in the time RDST can see, and events takes 300 writes/s; frees 1.0 MB")


class _Jev:
    def __init__(self, answers):
        self.answers, self.requests = answers, []

    def action_priority(self, **request):
        self.requests.append(request)
        return {"actions": [{"id": action["id"], **self.answers[action["type"]]}
                            for action in request["actions"]]}


def test_jev_reorders_open_actions_and_retires_ones_it_is_sure_are_stale(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    _snapshot(store)
    plan = _plan(queries=[{"key": "q1", "name": "SELECT on orders", "share": 0.4, "kind": "select",
                           "suggestions": [{"table": "orders", "columns": ["customer_id"],
                                            "gain_pct": 10, "verified": True}]}])
    database = [{"id": "setting_work_mem", "title": "Raise work_mem", "severity": "bad",
                 "finding": "work_mem is 4MB.", "setting": {"name": "work_mem"}},
                {"id": "vacuum", "title": "Vacuum 2 tables", "severity": "warn",
                 "finding": "Dead rows piled up.", "sql": "VACUUM a;"}]
    actions.reconcile(store, "db", NOW, actions.candidates(store, "db", plan, [], database))
    jev = _Jev({"add_index": {"priority": 0.25, "relevance": "relevant", "stale_probability": 0.1},
                "change_setting": {"priority": 1.0, "relevance": "relevant", "stale_probability": 0.0},
                "run_maintenance": {"priority": 0.5, "relevance": "stale", "stale_probability": 0.95}})
    assert actions.jev_rank(store, "db", NOW + 60, jev)
    assert [action["type"] for action in jev.requests[0]["actions"]] == [
        "change_setting", "run_maintenance", "add_index"]
    page = actions.present(store, "db", NOW + 60)
    assert [card["type"] for card in page["actions"]] == ["change_setting", "add_index"]
    retired = next(row for row in store.actions("db") if row["type"] == "run_maintenance")
    assert (retired["state"], retired["closed_reason"]) == ("retired", "jev_stale")


def test_settings_and_maintenance_fold_into_one_card_each(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    _snapshot(store)
    database = [{"id": f"setting_{name}", "title": title, "severity": severity, "finding": "x",
                 "setting": {"name": name}}
                for name, title, severity in (("work_mem", "Raise work_mem so sorts stay in memory", "bad"),
                                              ("random_page_cost", "Lower random_page_cost", "bad"),
                                              ("shared_buffers", "Give PostgreSQL more memory", "bad"))]
    database += [{"id": "vacuum", "title": "Vacuum 6 bloated tables", "severity": "warn",
                  "finding": "x", "sql": "VACUUM a;"},
                 {"id": "analyze", "title": "Refresh statistics on 3 tables", "severity": "info",
                  "finding": "x", "sql": "ANALYZE a;"}]
    actions.reconcile(store, "db", NOW, actions.candidates(store, "db", _plan(), [], database))
    page = actions.present(store, "db", NOW)
    assert [(card["type"], card["title"]) for card in page["actions"]] == [
        ("settings", "Tune 3 server settings"), ("maintenance", "Run 2 maintenance tasks")]
    assert page["actions"][0]["summary"] == "Start with: lower random_page_cost, then 2 more"
    assert page["actions"][1]["summary"] == "Vacuum 6 bloated tables, then refresh statistics on 3 tables"
    assert page["open_count"] == 5


def test_measured_spills_back_the_work_mem_action(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    _snapshot(store)
    store.record_stats("db", {"engine": "postgresql", "version": 160000, "counter_since": 1,
                              "taken_at": NOW - 60, "tables": [], "indexes": [], "settings": {}})
    store.record("db", NOW - 120, [{"query_key": "report", "calls": 3, "total_ms": 900,
                                    "temp_blks": 20_000}], {"report": "SELECT ..."}, [])
    database = [{"id": "setting_work_mem", "title": "Raise work_mem so sorts stay in memory",
                 "severity": "bad", "finding": "work_mem is 4 MB.", "setting": {"name": "work_mem"}}]
    [found] = actions.candidates(store, "db", _plan(), [], database)
    assert found["payload"]["summary"] == "Sorts and hashes wrote 156.2 MB to disk in the last day"
    assert found["payload"]["queries"] == ["report"]
    assert found["score"] == actions.SEVERITY_SCORE["bad"]


def test_settings_off_only_a_rule_of_thumb_stay_off_the_actions(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    _snapshot(store)
    database = [{"id": "setting_random_page_cost", "title": "Lower random_page_cost",
                 "severity": "warn", "finding": "random_page_cost is 4."},
                {"id": "setting_shared_buffers", "title": "Give PostgreSQL more memory",
                 "severity": "info", "finding": "shared_buffers is 128 MB."},
                {"id": "setting_innodb_buffer_pool_size", "title": "Give InnoDB more memory",
                 "severity": "warn", "finding": "Cache hit fell to 81%.",
                 "setting": {"name": "innodb_buffer_pool_size"}},
                {"id": "idle_in_transaction", "title": "Close transactions the app leaves open",
                 "severity": "warn", "finding": "1 session sat idle in a transaction."}]
    found = actions.candidates(store, "db", _plan(), [], database)
    assert sorted(item["key"] for item in found) == [
        "db:idle_in_transaction", "db:setting_innodb_buffer_pool_size"]


def test_a_significant_tested_rewrite_becomes_an_action(tmp_path, monkeypatch):
    store = MonitorStore(tmp_path / "monitor.db")
    _snapshot(store)
    best = {"rewritten_sql": "SELECT id FROM customers WHERE email = $1",
            "improvement": {"execution_time": {"improvement_pct": 80.0, "is_better": True},
                            "cost_estimate": {"improvement_pct": 95.0, "is_better": True},
                            "overall": {"improvement_pct": 85.0, "is_better": True,
                                        "significant": True}}}
    analysis = type("A", (), {"display_payload": {"rewrite_testing": {"tested": True,
                                                                       "best_rewrite": best}}})()
    monkeypatch.setattr(actions, "latest_done_job", lambda store, target, key: {"analysis_ref": "r"})
    monkeypatch.setattr(actions, "load_analysis", lambda job: analysis)
    actions._tested_rewrite.cache_clear()
    queries = [{"key": "login", "name": "SELECT on customers by email", "share": 0.2, "kind": "select"}]
    [found] = actions.candidates(store, "db", _plan(), queries, [])
    assert found["type"] == "rewrite_query"
    assert found["payload"]["summary"] == (
        "A tested rewrite ran 5.0x faster; the query takes 20% of database time")
    assert found["score"] == 0.2 * 0.8


def test_a_rewrite_that_ran_slower_never_shows_whatever_the_planner_thought(tmp_path, monkeypatch):
    store = MonitorStore(tmp_path / "monitor.db")
    _snapshot(store)
    best = {"rewritten_sql": "SELECT id FROM customers WHERE email = $1",
            "improvement": {"execution_time": {"improvement_pct": -20.0, "is_better": False},
                            "cost_estimate": {"improvement_pct": 90.0, "is_better": True},
                            "overall": {"improvement_pct": 30.0, "is_better": True,
                                        "significant": True}}}
    analysis = type("A", (), {"display_payload": {"rewrite_testing": {"tested": True,
                                                                       "best_rewrite": best}}})()
    monkeypatch.setattr(actions, "latest_done_job", lambda store, target, key: {"analysis_ref": "r"})
    monkeypatch.setattr(actions, "load_analysis", lambda job: analysis)
    actions._tested_rewrite.cache_clear()
    queries = [{"key": "login", "name": "SELECT on customers by email", "share": 0.2, "kind": "select"}]
    assert actions.candidates(store, "db", _plan(), queries, []) == []


def _work_mem_store(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    _snapshot(store)
    reading = {"engine": "postgresql", "version": 160000, "counter_since": 1, "tables": [],
               "indexes": []}
    store.record_stats("db", {**reading, "taken_at": NOW - 60,
                              "settings": {"work_mem": {"value": "4096", "unit": "kB"}}})
    database = [{"id": "setting_work_mem", "title": "Raise work_mem so sorts stay in memory",
                 "severity": "bad", "finding": "x", "setting": {"name": "work_mem"}}]
    found = actions.candidates(store, "db", _plan(), [], database)
    actions.reconcile(store, "db", NOW, found)
    return store, reading, found


def test_a_setting_moved_the_wrong_way_keeps_its_action_open(tmp_path):
    store, reading, found = _work_mem_store(tmp_path)
    store.record_stats("db", {**reading, "taken_at": NOW + 300,
                              "settings": {"work_mem": {"value": "1024", "unit": "kB"}}})
    assert len(actions.reconcile(store, "db", NOW + 300, found, database_refreshed=False)) == 1


def test_a_stale_checkup_does_not_reopen_an_applied_setting(tmp_path):
    store, reading, found = _work_mem_store(tmp_path)
    store.record_stats("db", {**reading, "taken_at": NOW + 300,
                              "settings": {"work_mem": {"value": "32768", "unit": "kB"}}})
    actions.reconcile(store, "db", NOW + 300, found, database_refreshed=False)
    assert actions.reconcile(store, "db", NOW + 600, found, database_refreshed=False) == []
    [row] = store.actions("db")
    assert row["state"] == "applied"
    assert [event["kind"] for event in store.action_events(row["id"])] == ["opened", "applied"]


def test_a_dismissal_holds_when_the_index_appears(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    _snapshot(store)
    found = actions.candidates(store, "db", _orders_plan(), [], [])
    [row] = actions.reconcile(store, "db", NOW, found)
    actions.set_state(store, "db", row["id"], "dismiss", NOW + 10)
    _snapshot(store, ("orders", "orders_customer", "customer_id"))
    actions.reconcile(store, "db", NOW + 300, [])
    assert store.actions("db")[0]["state"] == "dismissed"


def test_only_drops_worth_doing_become_actions(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    existing = [{"schema": "public", "table": "lists", "index_name": "lists_user_idx",
                 "columns": ["user_id"], "scans": 0, "window_scans": 0, "size_bytes": 16384},
                {"schema": "public", "table": "events", "index_name": "events_kind",
                 "columns": ["kind"], "scans": 0, "window_scans": 0, "size_bytes": 4 << 20}]
    young = _plan(existing=existing, observed_s=600, counter_age_s=1200,
                  tables={"events": {"writes_per_s": 300}, "lists": {"writes_per_s": 0}})
    found = actions.candidates(store, "db", young, [], [])
    # A young window still flags the index a write-heavy table pays for; the tiny idle one waits.
    assert [item["payload"]["index_name"] for item in found] == ["events_kind"]


def test_maintenance_shows_done_only_when_it_really_ran(tmp_path):
    def reading(ts, dead, vacuumed_at):
        return {"engine": "postgresql", "version": 160000, "counter_since": 1_000, "taken_at": ts,
                "settings": {}, "indexes": [],
                "tables": [{"schema": "public", "table": "items", "ins": 0, "upd": 0, "del": 0,
                            "hot_upd": 0, "seq_scan": 0, "idx_scan": 0, "scan_rows": 0,
                            "live_rows": 1000 - dead, "dead_rows": dead, "size_bytes": 8 << 20,
                            "vacuumed_at": vacuumed_at, "analyzed_at": None}]}

    database = [{"id": "vacuum", "check": "vacuum", "title": "Vacuum 1 bloated table",
                 "severity": "warn", "finding": "items is 18.0% dead rows.", "sql": "VACUUM items;",
                 "rows": [{"name": "items", "value": "18.0% dead"}]}]

    def outcome(name, dead, vacuumed_at):
        store = MonitorStore(tmp_path / f"{name}.db")
        actions.reconcile(store, "db", NOW, actions.candidates(store, "db", _plan(), [], database))
        store.record_stats("db", reading(NOW + 300, dead, vacuumed_at))
        actions.reconcile(store, "db", NOW + 600, [], database_refreshed=True)
        row = store.actions("db")[0]
        return row["state"], row["closed_reason"]

    # PostgreSQL says when a table was vacuumed: that settles it either way.
    assert outcome("vacuumed", 150, NOW + 200) == ("applied", "cleared")
    assert outcome("recounted", 20, NOW - 100) == ("retired", "evidence_faded")
    # Without the timestamp, dead rows have to have really fallen.
    assert outcome("fell", 20, None) == ("applied", "cleared")
    assert outcome("still_dead", 150, None) == ("retired", "evidence_faded")


def test_cards_carry_a_category_and_a_severity_on_one_scale():
    assert [actions._severity(score) for score in (0.15, 0.05, 0.01)] == ["high", "medium", "low"]
    assert actions.CATEGORY["index_plan"] == "indexes"
    assert actions.CATEGORY["change_setting"] == "settings"
    assert actions.CATEGORY["rewrite_query"] == "rewrites"


def test_an_applied_index_is_verified_or_found_not_to_help_by_measurement(tmp_path):
    def run(after_ms, wait_s):
        store = MonitorStore(tmp_path / f"{after_ms}-{wait_s}.db")
        closed = NOW
        for minute in range(-50, int(wait_s // 60), 5):
            ms = 10.0 if minute < 0 else after_ms
            store.record("db", closed + minute * 60, [{"query_key": "q1", "calls": 10,
                                                       "total_ms": 10 * ms}],
                         {"q1": "SELECT * FROM orders WHERE customer_id = 1"}, [])
        store.write_actions("db", closed, [{
            "key": "add:orders", "type": "add_index", "state": "applied", "score": 0.3,
            "closed_at": closed, "payload": {"title": "Add an index", "summary": "",
                                             "apply": "run_sql", "queries": ["q1"]}}])
        actions.verify(store, "db", closed + wait_s)
        row = store.actions("db")[0]
        return row["state"], row["payload"].get("result")

    state, result = run(2.0, 20 * 60)
    assert state == "confirmed" and result["after_ms"] < result["before_ms"] * 0.8
    # Too soon to judge a fix that hasn't moved; two hours on, it didn't help.
    assert run(9.8, 20 * 60)[0] == "applied"
    assert run(9.8, 2 * 3600 + 60)[0] == "no_effect"


def test_database_actions_come_from_the_newer_of_checkup_and_checks(tmp_path):
    from features.monitor.actions import database_checked_at, latest_database_actions

    store = MonitorStore(tmp_path / "monitor.db")
    run, _ = store.claim_report("db", "user", 1_000)
    store.update_report(run["id"], report={"snapshot": {"actions": [{"id": "from_checkup"}]}},
                        state="done", finished_at=1_100)
    assert latest_database_actions(store, "db") == [{"id": "from_checkup"}]
    store.save_database_check("db", 2_000, [{"id": "from_checks"}])
    assert latest_database_actions(store, "db") == [{"id": "from_checks"}]
    assert database_checked_at(store, "db", 1_100) == 2_000


def test_settings_say_they_are_measuring_until_traffic_gives_rates(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    store._write(lambda conn: conn.execute(
        "INSERT OR REPLACE INTO monitor_setting VALUES ('db','enabled_at','1000')"))
    store.save_database_check("db", 1010, [])
    coverage = actions._coverage({"state": "first_pass", "finished_at": None}, None, {}, 1010,
                                 actions._measuring(store, "db"))
    assert coverage["settings"]["state"] == "checking"
    assert coverage["settings"]["waiting_on"].startswith("Measuring two minutes of traffic")
    store.save_database_check("db", 1130, [])
    coverage = actions._coverage({"state": "first_pass", "finished_at": None}, None, {}, 1130,
                                 actions._measuring(store, "db"))
    assert coverage["settings"]["state"] == "checked"


def test_rewrites_are_checked_once_the_first_pass_ends_while_the_checkup_still_runs():
    coverage = actions._coverage({"state": "running", "finished_at": None},
                                 {"computed_at": 1200, "load": {}}, {}, 1130)
    assert coverage["rewrites"]["state"] == "checked"
    assert coverage["indexes"]["state"] == "checked"
