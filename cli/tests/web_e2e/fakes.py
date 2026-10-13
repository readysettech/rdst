"""Deterministic subclasses of the production services for browser tests.

Each fake extends its real service and overrides only the methods that would
contact a database, Readyset, an LLM, or a remote host. Overridden methods
keep the exact production signatures (test_web_e2e_service_fakes.py enforces
this), unfaked methods run the real code, and fixture payloads are validated
into the same typed events the real services yield. Where a real read path
depends on state a faked method would have written (audit history, the
semantic layer), the fake persists that state through the production storage
so the read paths stay real.
"""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter

from pydantic import TypeAdapter

from tests.web_e2e.fixture_store import FixtureStore


fixtures = FixtureStore()


monitor_fake_router = APIRouter(prefix="/api/testing/monitor")


@monitor_fake_router.post("/seed")
def seed_monitor_fixture():
    """Seed the real monitor store for browser tests without database access."""
    from shared.monitor.monitor_store import MonitorStore

    store = MonitorStore()
    target = "e2e-guard"
    now = int(time.time()) // 60 * 60

    def clear(conn):
        conn.execute("DELETE FROM inbox_event WHERE item_id IN (SELECT id FROM inbox_item WHERE target=?)", (target,))
        conn.execute("DELETE FROM report_event WHERE report_id IN (SELECT id FROM report_run WHERE target=?)", (target,))
        # Settings go too: a removed target stays switched off, and its readings are refused.
        conn.execute("DELETE FROM action_event WHERE action_id IN (SELECT id FROM action WHERE target=?)", (target,))
        for table in ("stmt_series", "db_series", "query_text", "index_change", "report_run",
                      "inbox_item", "mute_rule", "monitor_pulse", "fix_verdict", "analyze_job",
                      "monitor_setting", "action", "index_plan", "stats_meta", "table_state",
                      "index_state", "setting_state", "table_activity", "index_activity",
                      "index_snapshot"):
            conn.execute(f"DELETE FROM {table} WHERE target=?", (target,))

    store._write(clear)
    # Orders lookup leads the load; a count and a DROP (which RDST won't analyze) follow.
    texts = {"orders-e2e": "SELECT id, total FROM orders WHERE customer_id = $1",
             "count-e2e": "SELECT count(*) FROM orders WHERE status = $1",
             "drop-e2e": "DROP INDEX IF EXISTS idx_orders_total"}
    for offset, calls, total_ms in ((-180, 300, 12000), (-120, 360, 18000), (-60, 420, 25200)):
        store.record(target, now + offset,
                     [{"query_key": "orders-e2e", "calls": calls, "total_ms": total_ms,
                       "rows": calls, "blks_hit": 800, "blks_read": 0},
                      {"query_key": "count-e2e", "calls": 60, "total_ms": 1800,
                       "rows": 60, "blks_hit": 100, "blks_read": 0},
                      {"query_key": "drop-e2e", "calls": 1, "total_ms": 40,
                       "rows": 0, "blks_hit": 0, "blks_read": 0}],
                     texts, [],
                     {"calls": calls + 61, "total_ms": total_ms + 1840, "active_conns": 8,
                      "max_conns": 100, "cache_hit_ratio": 0.99,
                      "xact_commit": calls, "xact_rollback": 2, "deadlocks": 0})
    run, _ = store.claim_report(target, "nightly", now - 60)
    store.update_report(run["id"], top=["orders-e2e", "count-e2e"], state="done", report={
        "version": 2,
        "computed": {
            "top_queries": [
                {"query_key": "orders-e2e", "text": texts["orders-e2e"], "total_ms": 55200.0,
                 "db_time_share": 0.906},
                {"query_key": "count-e2e", "text": texts["count-e2e"], "total_ms": 5400.0,
                 "db_time_share": 0.089}],
            "analyses": [],
            "deltas": {"24h": {
                "database": {"total_ms": {"current": 60920.0, "prior": 41000.0},
                             "calls": {"current": 1263.0, "prior": 1100.0},
                             "mean_ms": {"current": 48.2, "prior": 37.3}},
                "queries": {"orders-e2e": {"total_ms": {"current": 55200.0, "prior": 36000.0},
                                           "calls": {"current": 1080.0, "prior": 960.0}}}}}},
        "summary": "Orders lookup accounts for most database time.",
        "fix_first": [{"query_key": "orders-e2e", "why": "High database time share",
                       "action": "Review the orders lookup index"}],
        "one_fix_several_queries": [],
        "health": [{"check": "Connections", "status": "ok", "note": "8 of 100 active"},
                   {"check": "vacuum_bloat", "status": "warn", "note": "orders has 18% dead rows",
                    "action": "Run VACUUM (ANALYZE) orders."}],
        "changed_since_yesterday": ["Orders lookup is slower"],
        "digest": [{"id": 9001, "type": "traffic_spike", "title": "Query traffic increased",
                    "summary": "Orders lookup ran 3x more often for 20 minutes.",
                    "query_keys": ["orders-e2e"], "subject_label": "Orders lookup",
                    "ts": now - 600, "reason": "push_limit"}]})
    # One item per delivery level and band (NOTIFICATION_MODEL.md).
    store.upsert_item(target, "latency_regression", "orders-e2e", 0.9,
                      "Orders lookup is 3x slower",
                      {"query_key": "orders-e2e", "baseline": {"1h": {"mean_ms": 20}},
                       "reported_numbers": {"mean_ms": 60, "qps": 7, "db_time_share": 0.8}},
                      now, delivery="notify")
    store.upsert_item(target, "stuck_query", "c0ffee0e2e00", 0.9, "Stuck query",
                      {"query_key": "c0ffee0e2e00"}, now - 30, delivery="notify")
    spike = store.upsert_item(target, "traffic_spike", "0e2e0e2e0e2e", 0.7,
                              "Query traffic increased", {"query_key": "0e2e0e2e0e2e"},
                              now - 3600, delivery="inbox")
    store.update_item_body(spike["id"], {
        "query_key": "0e2e0e2e0e2e", "baseline": {"1h": {"qps": 1.0}},
        "resolution": {"before": {"qps": 4.0}, "after": {"qps": 1.1}}})
    store.action_item(spike["id"], "resolve", now - 120, resolved_by="code",
                      measured_recovery=True)
    store.upsert_item(target, "query_improved", "orders-e2e", 0.4, "Query improved",
                      {"query_key": "orders-e2e"}, now - 90, delivery="digest")
    # A group alert: the title says which way the members moved.
    store.upsert_item(target, "many_queries_changed", f"burst:{now - 300}", 0.7,
                      "2 queries got slower",
                      {"query_keys": ["orders-e2e", "count-e2e"], "burst_start": now - 300,
                       "burst_last_join": now - 300, "smaller_query_keys": [
                           "0e2ea0000001", "0e2ea0000002"], "recovery_queries": {
                          key: {"kind": "latency_regression", "baseline_mean_ms": base,
                                "baseline_qps": 1.0, "filed_at": now - 300,
                                "at_filing": {"mean_ms": base * 3, "qps": 1.0,
                                              "db_time_share": share}}
                          for key, base, share in (("orders-e2e", 20.0, 0.9),
                                                   ("count-e2e", 10.0, 0.09))}},
                      now - 300, delivery="inbox")
    store.upsert_item(target, "daily_summary", "today", 0.4, "Daily report ready",
                      {"report_id": run["id"], "summary": "Orders lookup accounts for most "
                       "database time."}, now - 60, delivery="inbox")
    _seed_traffic_jump(store, target, now)
    # The report's daily snapshot, built by the real code from the rows seeded above.
    from features.monitor import snapshot
    from shared.monitor.monitor_store import local_day_bounds

    start = min(local_day_bounds(now)[0], now - 3600)
    # The checks the Detailed report reads: bloat, connections, the settings audit, indexes.
    collectors = {
        "vacuum_bloat": {"tables": [{"schema": "public", "table": "orders",
                                     "live_tuples": 8200, "dead_tuples": 1800,
                                     "dead_ratio_pct": 18.0, "status": "warn",
                                     "size_bytes": 64 * 1024 * 1024}]},
        "connections": {"total_connections": 8, "max_connections": 100},
        "config_audit": {"settings": [
            {"parameter": "shared_buffers", "current": "128MB", "recommended": "2GB",
             "status": "warn", "note": "For 8 GB of memory."},
            {"parameter": "work_mem", "current": "4MB", "recommended": "32MB", "status": "warn",
             "note": "Sorts spill to disk."},
            {"parameter": "random_page_cost", "current": "4", "recommended": "1.1",
             "status": "warn", "note": "SSD storage."}]},
        "index_health": {"total_indexes": 2, "unused_indexes": [], "duplicates": []}}
    day = snapshot.build(store, target, start, now + 60, now + 60, partial=True,
                         collectors=collectors)
    report = json.loads(store.report(run["id"])["report_json"])
    store.update_report(run["id"], report={
        **report, "version": 3, "snapshot": day,
        "computed": {**report["computed"], "health_collectors": collectors}},
        period=(start, now + 60))
    _seed_agent_activity(target, now)
    return {"target": target}


@monitor_fake_router.post("/seed-actions")
def seed_actions_fixture():
    """Actions and an index plan for e2e-guard, computed by the real code from seeded facts:
    five index adds, one setting and one maintenance task in the plan; a verified change, an
    undone one and two dismissed ones with reasons in Applied."""
    from features.monitor import actions
    from features.monitor.index_plan import build_plan
    from shared.monitor.monitor_store import MonitorStore

    store = MonitorStore()
    target, now = "e2e-guard", int(time.time())
    # Readings for the queries the plan speeds up, so their shares and latencies are real.
    texts = {"votes-e2e": "SELECT id, votetypeid FROM votes WHERE postid = $1",
             "comments-e2e": "SELECT count(*) FROM comments WHERE postid = $1",
             "posts-e2e": "SELECT id, title FROM posts WHERE owneruserid = $1 "
                          "ORDER BY creationdate DESC",
             "badges-e2e": "SELECT name FROM badges WHERE userid = $1"}
    for offset in (-180, -120, -60):
        store.record(target, now // 60 * 60 + offset,
                     [{"query_key": key, "calls": calls, "total_ms": calls * mean, "rows": calls,
                       "blks_hit": 100, "blks_read": 0}
                      for key, calls, mean in (("votes-e2e", 180, 48.0),
                                               ("comments-e2e", 140, 41.0),
                                               ("posts-e2e", 60, 71.0), ("badges-e2e", 40, 52.0))],
                     texts, [])
    store.sync_indexes(target, [
        {"schema": "public", "table": "orders", "index_name": "orders_pkey",
         "definition": "CREATE UNIQUE INDEX orders_pkey ON public.orders USING btree (id)"},
        {"schema": "public", "table": "orders", "index_name": "idx_orders_status_old",
         "definition": "CREATE INDEX idx_orders_status_old ON public.orders USING btree (status)"}],
        now - 600)

    def table(name, writes):
        return {"schema": "public", "table": name, "ins": writes, "upd": 0, "del": 0, "hot_upd": 0,
                "seq_scan": 0, "idx_scan": 0, "scan_rows": 0, "live_rows": 8200, "dead_rows": 0,
                "size_bytes": 1 << 20}

    for step, writes in ((0, 1000), (1, 4000)):
        store.record_stats(target, {
            "engine": "postgresql", "version": 160000, "counter_since": now - 40 * 86400,
            "taken_at": now - 3600 + step * 3600, "tables": [table("orders", writes)],
            "indexes": [{"schema": "public", "table": "orders", "index_name": name, "scans": scans,
                         "size_bytes": 1 << 19, "unique": name.endswith("pkey"),
                         "primary": name.endswith("pkey"), "last_scan_at": None}
                        for name, scans in (("orders_pkey", 900), ("idx_orders_status_old", 40))],
            "settings": {"work_mem": {"value": "4096", "unit": "kB", "pending_restart": False}}})

    def query(key, name, share, table, columns, gain):
        return {"key": key, "name": name, "share": share, "kind": "select", "suggestions": [
            {"table": table, "columns": columns, "gain_pct": gain, "verified": True}]}

    inputs = {"engine": "postgresql", "now": now, "observed_s": 3600, "counter_age_s": 40 * 86400,
              "existing": [
                  {"schema": "public", "table": "orders", "index_name": "orders_pkey",
                   "columns": ["id"], "unique": True, "primary": True, "scans": 900,
                   "window_scans": 900, "size_bytes": 1 << 19},
                  {"schema": "public", "table": "orders", "index_name": "idx_orders_status_old",
                   "columns": ["status"], "scans": 40, "window_scans": 40, "size_bytes": 4 << 20}],
              "tables": {"orders": {"writes_per_s": 0.8, "peak_writes_per_s": 1.5},
                         "votes": {"writes_per_s": 40.0, "peak_writes_per_s": 60.0}},
              "queries": [
                  query("orders-e2e", "SELECT on orders by customer_id", 0.46, "orders",
                        ["customer_id"], 92.0),
                  query("votes-e2e", "SELECT on votes by postid", 0.17, "votes", ["postid"], 88.0),
                  query("comments-e2e", "COUNT on comments by postid", 0.12, "comments",
                        ["postid"], 85.0),
                  query("posts-e2e", "SELECT on posts by owneruserid", 0.08, "posts",
                        ["owneruserid", "creationdate"], 70.0),
                  query("badges-e2e", "SELECT on badges by userid", 0.04, "badges", ["userid"],
                        60.0)]}
    plan = build_plan(inputs)
    plan["explanation"] = ("One index on orders speeds up the lookup that takes most of the "
                           "database time; four more cover the next busiest queries.")
    store.save_index_plan(target, plan)
    database = [{"id": "setting_work_mem", "title": "Raise work_mem so sorts stay in memory",
                 "severity": "bad", "finding": "work_mem is 4 MB.",
                 "setting": {"name": "work_mem", "current": "4 MB", "target": "16 MB"},
                 "sql": "ALTER SYSTEM SET work_mem = '16MB';", "restart": False},
                {"id": "vacuum", "title": "Vacuum 1 bloated table", "severity": "warn",
                 "finding": "orders is 18% dead rows.", "sql": "VACUUM (ANALYZE) public.orders;"}]
    found = actions.candidates(store, target, plan, [], database)
    actions.reconcile(store, target, now, found)
    rows = {row["type"]: row for row in store.actions(target)}
    orders = next(row for row in store.actions(target) if row["payload"].get("table") == "orders")
    actions.apply_explanations(store, target, now, {"action_picks": [
        {"action_id": f"a{orders['id']}",
         "why": "The orders lookup takes most of the database time."},
        {"action_id": f"a{rows['change_setting']['id']}", "why": "Sorts spill to disk."},
        {"action_id": f"a{rows['run_maintenance']['id']}", "why": "orders has many dead rows."}]})
    _seed_applied(store, target, now)
    return {"target": target}


def _seed_applied(store, target: str, now: int) -> None:
    """Applied's history: a verified index with its closed Fix confirmed, an undone index, and
    two dismissed changes with their reasons."""
    from features.monitor import actions

    def index(key, table, columns, *, queries=(), extra=None, **fields):
        name = f"idx_{table}_{'_'.join(columns)}"
        return {"key": key, "type": "add_index", "score": 0.1, "opened_at": now - 3 * 86400,
                **fields, "payload": {
                    "title": f"Add an index on {table}({', '.join(columns)})", "summary": "",
                    "apply": "run_sql", "table": table, "columns": columns,
                    "queries": list(queries),
                    "sql": f"CREATE INDEX CONCURRENTLY {name} ON {table} ({', '.join(columns)});",
                    "detail": {"undo_sql": f"DROP INDEX CONCURRENTLY public.{name};"},
                    **(extra or {})}}

    verified = index("add:e2e-tags", "tags", ["count", "tagname"], state="confirmed",
                     closed_at=now - 7200, closed_reason="index_added", queries=["count-e2e"],
                     extra={"result": {"before_ms": 38.0, "after_ms": 4.1, "calls": 120,
                                       "at": now - 3600}})
    store.write_actions(target, now - 3600, [
        verified,
        index("add:e2e-posts-score", "posts", ["score"], state="undone",
              closed_at=now - 3 * 86400, closed_reason="undone",
              dismiss_reason="It slowed the nightly import."),
        index("add:e2e-users-location", "users", ["location"], state="dismissed",
              closed_at=now - 86400, closed_reason="dismissed",
              dismiss_reason="One nightly report reads it, not worth a cost on every write."),
        {"key": "db:setting_e2e_work_mem_64", "type": "change_setting", "state": "dismissed",
         "score": 0.05, "opened_at": now - 4 * 86400, "closed_at": now - 2 * 86400,
         "closed_reason": "dismissed", "dismiss_reason": "The host caps memory at 16 GB.",
         "payload": {"title": "Raise work_mem to 64 MB", "summary": "", "apply": "setting",
                     "setting": {"name": "work_mem", "current": "4MB", "target": "64MB"},
                     "queries": []}}])
    actions._file_verification(store, target, now - 3600, verified)


def _seed_traffic_jump(store, target: str, now: int) -> None:
    """A group alert filed 7 hours ago (apart from the other group alert, so the Inbox
    lists it on its own) on two light queries whose traffic jumped 4x: one from 30
    minutes before filing to 5 after, one from 22 minutes before to 10 after (DESIGN_V5
    section 3)."""
    filed = now - 7 * 3600
    texts = {"0e2ea0000001": "SELECT * FROM carts WHERE id = $1",
             "0e2ea0000002": "SELECT * FROM sessions WHERE token = $1"}
    for before in range(120, -40, -1):
        calls = {"0e2ea0000001": 80 if -5 < before <= 30 else 20,
                 "0e2ea0000002": 80 if -10 < before <= 22 else 20}
        store.record(target, filed - before * 60,
                     [{"query_key": key, "calls": count, "total_ms": count * 0.5,
                       "rows": count, "blks_hit": 10, "blks_read": 0}
                      for key, count in calls.items()], texts, [])
    store.upsert_item(target, "many_queries_changed", f"burst:{filed}", 0.7,
                      "Traffic jumped on 2 queries",
                      {"query_keys": list(texts), "burst_start": filed,
                       "burst_last_join": filed, "recovery_queries": {
                          key: {"kind": "traffic_spike", "filed_at": filed} for key in texts}},
                      filed, delivery="inbox")


def _seed_agent_activity(target: str, now: int) -> None:
    """One deep analysis with its statements and AI call, and an hour of checks."""
    from shared.agent_activity.store import AgentActivityStore

    store = AgentActivityStore()
    store.ensure_open()

    def clear(conn):
        for table in ("activity_statement", "activity_ai_call"):
            conn.execute(f"DELETE FROM {table} WHERE entry_id IN "
                         "(SELECT id FROM activity_entry WHERE target=?)", (target,))
        conn.execute("DELETE FROM activity_entry WHERE target=?", (target,))

    store._write(clear)
    hour = now // 3600 * 3600
    for minute in (0, 5, 10):
        store.append({"ts": hour + minute * 60, "target": target,
                      "action": "attention_check", "outcome": "done",
                      "summary": "Nothing new to file"}, [],
                     [{"provider": "readyset-jev", "model": "jev-1.13.0",
                       "operation": "monitor_attention", "purpose": "attention_check",
                       "data_kinds": ["SQL text", "query statistics"], "tokens_in": 900,
                       "tokens_out": 20, "cost_usd": 0.000038, "cost_estimated": True}])
    store.append(
        {"ts": now - 120, "target": target, "action": "deep_analysis",
         "subject": "SELECT on orders by customer_id", "outcome": "done",
         "decision": "Ran EXPLAIN ANALYZE with a 1.5 s timeout",
         "details": {"parameters": "1 parameter sampled from live sessions"}},
        [{"sql": "SET LOCAL statement_timeout = 1500", "kind": "session"},
         {"sql": "EXPLAIN (ANALYZE true, VERBOSE true, COSTS true, BUFFERS true, "
                 "FORMAT JSON) SELECT id, total FROM orders WHERE customer_id = ?",
          "kind": "explain_analyze", "duration_ms": 42.0, "timeout_ms": 1500}],
        [{"provider": "readyset", "model": "z-ai/glm-5.3-flash",
          "operation": "monitor_analyze", "purpose": "monitor_analyze",
          "data_kinds": ["SQL text with sampled parameter values", "query plan", "schema"],
          "tokens_in": 5200, "tokens_out": 640, "cost_usd": 0.0031}])


from features.analyze.events import AnalyzeEvent
from features.analyze.service import AnalyzeService
from features.ask.events import AskEvent
from features.ask.service import AskService
from features.audit.events import AuditEvent
from features.audit.service import AuditService
from features.bootstrap.events import BootstrapEvent
from features.bootstrap.service import TargetBootstrapService
from features.cache.events import CacheEvent
from features.cache.experiment_service import ReadysetExperimentService
from features.init.events import InitEvent
from features.init.service import InitService
from features.scan.events import ScanEvent
from features.scan.service import ScanService
from features.schema.events import SchemaEvent
from features.schema.models import SchemaStatus
from features.schema.semantic_models import SemanticLayer
from features.schema.service import SchemaService
from features.top.events import TopEvent
from features.top.service import TopService
from features.schema.assessment_context import AssessmentContext


ANALYZE_EVENT = TypeAdapter(AnalyzeEvent)
ASK_EVENT = TypeAdapter(AskEvent)
AUDIT_EVENT = TypeAdapter(AuditEvent)
BOOTSTRAP_EVENT = TypeAdapter(BootstrapEvent)
CACHE_EVENT = TypeAdapter(CacheEvent)
INIT_EVENT = TypeAdapter(InitEvent)
SCAN_EVENT = TypeAdapter(ScanEvent)
SCHEMA_EVENT = TypeAdapter(SchemaEvent)
SCHEMA_STATUS = TypeAdapter(SchemaStatus)
TOP_EVENT = TypeAdapter(TopEvent)


class FakeAnalyzeService(AnalyzeService):
    async def analyze(self, input, options):
        del input, options
        async for event in fixtures.events("analyze", ANALYZE_EVENT):
            yield event

    async def suggest_parameter_values(
        self, sql, target, target_config=None, query_hash=None
    ):
        del sql, target, target_config, query_hash
        return fixtures.value(
            "parameter_suggestions", default={"placeholders": [], "sample": None}
        )


class FakeAskService(AskService):
    async def ask(self, input, options):
        del input, options
        async for event in fixtures.events("ask", ASK_EVENT):
            yield event

    async def resume(self, session_id, clarification_answers=None):
        del session_id, clarification_answers
        async for event in fixtures.events("ask_resume", ASK_EVENT):
            yield event


class FakeAuditService(AuditService):
    async def audit_single(self, target_name, *, insights=False, save=True):
        del target_name, insights, save
        snapshot_id: str | None = None
        async for event in fixtures.events("audit", AUDIT_EVENT):
            if event.type == "snapshot_saved":
                snapshot_id = event.snapshot_id
            if event.type == "target_complete" and snapshot_id is not None:
                # Persist through the real store so the history routes,
                # which read it from disk, serve this run for real.
                from features.fleet import SnapshotStore

                await asyncio.to_thread(
                    SnapshotStore().save_raw, snapshot_id, event.result
                )
            yield event


class FakeTargetBootstrapService(TargetBootstrapService):
    async def run(
        self,
        target,
        target_config,
        options=None,
        key_wakeup=None,
    ):
        response = fixtures.take("bootstrap", default=None)
        if response is None:
            async for event in super().run(
                target,
                target_config,
                options,
                key_wakeup=key_wakeup,
            ):
                yield event
            return

        async for event in fixtures.replay(response, BOOTSTRAP_EVENT):
            yield event
            if event.type == "needs_key":
                if key_wakeup is None:
                    raise RuntimeError("Bootstrap fixture needs a resume event")
                await key_wakeup.wait()
                key_wakeup.clear()


class FakeReadysetExperimentService(ReadysetExperimentService):
    async def compare(
        self,
        *,
        owner_id,
        target,
        query,
        iterations=15,
        warmup=5,
        interval_ms=None,
        concurrency=None,
        duration_seconds=None,
        query_hash="",
    ):
        del (
            owner_id,
            target,
            query,
            iterations,
            warmup,
            interval_ms,
            concurrency,
            duration_seconds,
            query_hash,
        )
        async for event in fixtures.events("speed_test", CACHE_EVENT):
            yield event

    async def compare_live(
        self,
        *,
        owner_id,
        target,
        query,
        duration_seconds,
        controller,
        query_hash="",
    ):
        del owner_id, target, query, duration_seconds, controller, query_hash
        async for event in fixtures.events("cache_compare", CACHE_EVENT):
            yield event


_ABSENT_SANDBOX = {
    "phase": "absent",
    "current_target": None,
    "generation": 0,
    "lease_owner": None,
    "lease_purpose": None,
    "queued_requests": 0,
    "dirty_reason": None,
    "failed_target": None,
    "last_error": None,
    "last_released_at": None,
    "expires_at": None,
    "container_name": "rdst-readyset-sandbox",
    "healthy": False,
}


class _FakeSandboxConnection:
    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config

    def as_target_config(self) -> dict[str, Any]:
        return dict(self._config)


class _FakeSandboxLease:
    def __init__(self, target: str, config: dict[str, Any]) -> None:
        self.target = target
        self.connection = _FakeSandboxConnection(config)
        self.dirty_reason: str | None = None

    async def mark_dirty(self, reason):
        self.dirty_reason = reason


class FakeSandboxManager:
    """No-Docker lifecycle boundary for browser-integration tests."""

    async def start(self):
        return None

    async def stop(self):
        return None

    @asynccontextmanager
    async def reserve_measurement(
        self, *, owner_id, purpose, priority=None, progress=None
    ):
        del owner_id, purpose, priority, progress
        yield None

    @asynccontextmanager
    async def lease(
        self, *, target, owner_id, purpose, priority=None, progress=None
    ):
        """Lease the sandbox a fixture describes; without one there is none.

        A load test that asks for the Readyset lane and finds no fixture
        measures the origin alone, which is what a machine without a sandbox
        does.
        """
        del owner_id, purpose, priority, progress
        connection = fixtures.value("sandbox_lease", default=None)
        if not connection:
            raise RuntimeError("No Readyset sandbox is available.")
        yield _FakeSandboxLease(target, connection)

    async def diagnostics(self):
        return fixtures.value(
            "sandbox_diagnostics",
            default=dict(_ABSENT_SANDBOX),
        )

    def request_prewarm(self, target):
        del target
        fixtures.take("sandbox_prewarm", default={"value": True})

    async def remove_target(self, target):
        async with self.retire_target(target) as removed:
            return removed

    @asynccontextmanager
    async def retire_target(self, target):
        del target
        yield False


fake_sandbox_manager = FakeSandboxManager()


async def fake_docker_runtime_status():
    return fixtures.value(
        "docker_runtime",
        default={"installed": True, "running": True},
    )


async def fake_probe_upstream(target_config):
    del target_config
    return fixtures.value(
        "upstream_probe",
        default={"success": True, "error": None},
    )


class FakeScanService(ScanService):
    async def scan_directory(self, input_data, options):
        del input_data, options
        async for event in fixtures.events("scan", SCAN_EVENT):
            yield event


class FakeSchemaService(SchemaService):
    """Fakes only the database-touching operations.

    get_schema and delete are inherited real; init_events persists the
    fixture's layer through the real manager so those read paths, and the
    real get_status, serve real on-disk state. get_status stays overridable
    by a fixture so specs can inject status failures.
    """

    def get_status(self, target):
        response = fixtures.take("semantic_status", default=None)
        if response is None:
            return super().get_status(target)
        if "value" not in response:
            raise RuntimeError("Fixture 'semantic_status' must contain a value")
        return SCHEMA_STATUS.validate_python(response["value"])

    async def init_events(self, target, target_config, options=None):
        del target_config, options
        response = fixtures.take("semantic_init")
        layer_data = response.get("layer")
        if layer_data is not None:
            self._manager.save(SemanticLayer.from_dict({**layer_data, "target": target}))
        async for event in fixtures.replay(response, SCHEMA_EVENT):
            yield event

    def refresh(self, target, target_config):
        del target, target_config
        return fixtures.value("semantic_refresh")


class FakeTopService(TopService):
    async def get_top_queries(self, input, options):
        del input, options
        async for event in fixtures.events("top_historical", TOP_EVENT):
            yield event

    async def stream_realtime(self, input, options, duration=None):
        del input, options, duration
        async for event in fixtures.events("top_realtime", TOP_EVENT):
            yield event


class FakeInitService(InitService):
    async def validate_all_events(self, target_names=None):
        del target_names
        async for event in fixtures.events("init_validate", INIT_EVENT):
            yield event


SERVICE_FAKES = [
    FakeAnalyzeService,
    FakeAskService,
    FakeAuditService,
    FakeTargetBootstrapService,
    FakeReadysetExperimentService,
    FakeInitService,
    FakeScanService,
    FakeSchemaService,
    FakeTopService,
]


def fake_autocomplete_schema(
    target_config: dict[str, Any], target: str | None = None
) -> dict[str, Any]:
    del target_config, target
    return {
        "success": True,
        "tables": fixtures.value("autocomplete_schema", default={}),
    }


class FakeJevClient:
    def assess(self, **kwargs):
        # Lanes run concurrently, so a fixture that names the SQL it answers
        # is matched by content rather than consumed in arrival order.
        sql = str(kwargs.get("sql") or "")
        response = None
        for candidate in fixtures.responses("jev_assessment"):
            match = candidate.get("match_sql")
            if match and match in sql:
                response = candidate
                break
        if response is None:
            response = fixtures.take("jev_assessment", default=None)
        if response is None:
            from shared.llm_manager.jev_client import JevClientError

            raise JevClientError("JEV_NOT_CONFIGURED", 503)
        time.sleep(float(response.get("delay_ms", 0)) / 1000)
        return response["value"]


def fake_assessment_context(sql, *, target, target_config, store):
    del sql, target, target_config, store
    return AssessmentContext(
        payload={"tables": ["orders"], "schema": "Table: orders"},
        fingerprint="e2e-schema",
        collected_at="2026-09-21T12:00:00Z",
        coverage="relevant tables",
        table_set_hash="orders",
    )
