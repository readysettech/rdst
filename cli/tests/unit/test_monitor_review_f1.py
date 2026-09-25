"""Regression coverage for review findings RV1-001, 002, 007, 008, and 009."""
import json
import sqlite3
from types import SimpleNamespace

import pytest
from features.monitor.api import routes, views
from features.monitor import report as reports
from shared.monitor.monitor_store import MonitorStore
from tests.unit.test_monitor_storage_plan import seed_series

NOW = 100 * 86400
KEY = "0123456789ab"


def test_retention_keeps_forward_event_and_pulse_cursors(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    item = store.upsert_item("db", "stuck_query", KEY, 1, "Stuck", {}, NOW)
    store.action_item(item["id"], "resolve", NOW + 1)
    cursor = store.inbox_events()[-1]["id"]
    store.record_pulse("db", NOW, "collect", True, "ok")
    pulse_cursor = store.pulses("db")[0]["id"]
    later = NOW + 31 * 86400
    store.rollup("db", later)
    store.upsert_item("db", "stuck_query", "another", 1, "Stuck", {}, later)
    store.record_pulse("db", later, "collect", True, "ok")
    assert store.inbox_events(cursor)
    assert store.pulses("db", after=pulse_cursor)


def test_reclaimed_analysis_rejects_old_completion_and_hold(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    store.enqueue_analysis("db", KEY, "user", 1, NOW)
    old = store.claim_analysis("db", NOW)
    store.recover_analysis(NOW + 601, 600)
    current = store.claim_analysis("db", NOW + 602)
    store.finish_analysis(old["id"], "done", NOW + 603, analysis_ref="stale")
    assert store.read("SELECT state FROM analyze_job")[0]["state"] == "running"
    assert not store.finish_analysis(old["id"], "done", NOW + 604,
                                     owner=old["owner"], attempt=old["attempt"])
    assert not store.hold_analysis(old["id"], owner=old["owner"], attempt=old["attempt"])
    assert not store.analysis_owned(old)
    assert store.analysis_owned(current)
    assert store.finish_analysis(current["id"], "done", NOW + 605, analysis_ref="fresh",
                                 owner=current["owner"], attempt=current["attempt"])
    assert store.read("SELECT analysis_ref FROM analyze_job")[0]["analysis_ref"] == "fresh"


@pytest.mark.parametrize("overlap", [False, True])
def test_expired_mute_reconciles_unchanged_item_without_push(tmp_path, monkeypatch, overlap):
    now = [NOW]
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: now[0])
    store.upsert_item("db", "stuck_query", KEY, 1, "Stuck", {}, NOW)
    store.create_mute_rule(target="db", query_key=KEY, type=None,
                           until=NOW + 60, note=None, now=NOW)
    active = store.create_mute_rule(target="db", query_key=None, type="stuck_query",
                                    until=None, note=None, now=NOW) if overlap else None
    cursor = store.inbox_events()[-1]["id"]
    now[0] += 120
    monkeypatch.setattr(routes, "_store", lambda: store)
    monkeypatch.setattr(routes, "_now", lambda: now[0])
    expected = active["id"] if active else None
    assert views.inbox_items(store, store.inbox())[0]["muted_by"] == expected
    updated = store.upsert_item("db", "stuck_query", KEY, 1, "Stuck", {}, now[0])
    assert updated["muted_by"] == expected
    assert bool(routes.inbox(state="open")["items"]) is not overlap
    assert not store.inbox_events(cursor)


@pytest.mark.parametrize("closed", [False, True])
def test_existing_incident_keeps_old_evidence_until_item_deleted(tmp_path, closed):
    now = [NOW]
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: now[0])
    item = store.upsert_item("db", "latency_regression", KEY, 1, "Slow", {}, NOW)
    if closed:
        store.action_item(item["id"], "resolve", NOW + 600)
    seed_series(store, "db", [KEY], [NOW, NOW + 5 * 86400])
    now[0] += 31 * 86400
    store.rollup("db", now[0])
    store.maintain(pause_s=0)
    assert store.read("SELECT 1 FROM stmt_series WHERE res=60 AND bucket_start=?", (NOW,))
    assert not store.read("SELECT 1 FROM stmt_series WHERE res=60 AND bucket_start=?",
                          (NOW + 5 * 86400,))
    def delete(conn):
        conn.execute("DELETE FROM inbox_event WHERE item_id=?", (item["id"],))
        conn.execute("DELETE FROM inbox_item WHERE id=?", (item["id"],))
    store._write(delete)
    store.rollup("db", now[0])
    store.maintain(pause_s=0)
    assert not store.read("SELECT 1 FROM stmt_series WHERE res=60")


@pytest.mark.asyncio
async def test_narrative_retry_sends_required_database_header(tmp_path, monkeypatch):
    store = MonitorStore(tmp_path / "monitor.db")
    row, _ = store.claim_report("db", "nightly", NOW)
    report = {"computed": {"top_queries": [], "fix_applied": []}, "provisional_query_keys": [], "narrative_error": "temporary"}
    monkeypatch.setattr(reports.database_limit, "paused", lambda *args: False)
    monkeypatch.setattr(reports.database_limit, "target_fingerprint", lambda target: "database-fingerprint")
    from shared.llm_manager.base import ProviderRequest
    from shared.llm_manager.hosted_glm_provider import HostedGLMProvider
    narrative = {"summary": "Ready", "fix_first": [], "one_fix_several_queries": [],
                 "health": [], "changed_since_yesterday": []}
    sent = []
    encoded = json.dumps(narrative)
    def post(url, *, json: dict, headers, timeout):
        sent.append(headers)
        assert headers.get("X-RDST-Monitor-Database") == "database-fingerprint"
        return SimpleNamespace(status_code=200, json=lambda: {"text": encoded})
    monkeypatch.setattr("shared.llm_manager.hosted_glm_provider._HTTP_SESSION.post", post)
    def narrate(context):
        request = ProviderRequest(model="hosted", messages=[{"role": "user", "content": "Report"}],
                                  max_tokens=100, temperature=0)
        answer = HostedGLMProvider().complete(request, api_key="test-token",
                                             base_url="https://keyservice.test")
        return json.loads(answer.text)
    monkeypatch.setattr(reports, "_narrate", narrate)
    await reports.ReportScheduler(store, clock=lambda: NOW)._retry_narrative(row["id"], "db", report)
    assert report["narrative_error"] is None
    assert len(sent) == 1


def test_v23_cursor_migration_preserves_ids_indexes_and_maximum(tmp_path):
    path = tmp_path / "monitor.db"
    store = MonitorStore(path)
    store.upsert_item("db", "stuck_query", KEY, 1, "Stuck", {}, NOW)
    store.record_pulse("db", NOW, "collect", True, "ok")
    before = {}
    with sqlite3.connect(path) as conn:
        for table in ("inbox_event", "monitor_pulse"):
            conn.execute(f"UPDATE {table} SET id=id+40")
            before[table] = conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
            sql = conn.execute("SELECT sql FROM sqlite_master WHERE name=?", (table,)).fetchone()[0]
            indexes = conn.execute("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? "
                                   "AND sql IS NOT NULL", (table,)).fetchall()
            conn.execute(f"ALTER TABLE {table} RENAME TO old_cursor")
            conn.execute(sql.replace(" AUTOINCREMENT", ""))
            conn.execute(f"INSERT INTO {table} SELECT * FROM old_cursor")
            conn.execute("DROP TABLE old_cursor")
            for index in indexes:
                conn.execute(index[0])
        conn.execute("ALTER TABLE analyze_job DROP COLUMN owner")
        conn.execute("ALTER TABLE analyze_job DROP COLUMN attempt")
        conn.execute("PRAGMA user_version=23")
    migrated = MonitorStore(path)
    migrated.ensure_open()
    with sqlite3.connect(path) as conn:
        for table, rows in before.items():
            assert conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall() == rows
            assert conn.execute("SELECT seq FROM sqlite_sequence WHERE name=?", (table,)).fetchone()[0] == rows[-1][0]
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='monitor_pulse_target'").fetchone()
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='inbox_event_ts'").fetchone()
    migrated.record_pulse("db", NOW + 1, "collect", True, "ok")
    assert migrated.pulses("db")[0]["id"] == before["monitor_pulse"][-1][0] + 1


@pytest.mark.asyncio
async def test_stale_worker_does_not_start_expensive_stage(tmp_path):
    from features.monitor.analyze_queue import AnalyzeQueue
    store = MonitorStore(tmp_path / "monitor.db")
    store.enqueue_analysis("db", KEY, "user", 1, NOW)
    old = store.claim_analysis("db", NOW)
    store.recover_analysis(NOW + 601, 600)
    store.claim_analysis("db", NOW + 602)
    async def unexpected(*args):
        pytest.fail("A stale worker started paid work")
    queue = AnalyzeQueue(store, service=SimpleNamespace(suggest_parameter_values=unexpected))
    await queue._process(old)
    assert store.read("SELECT state FROM analyze_job")[0]["state"] == "running"


@pytest.mark.asyncio
@pytest.mark.parametrize("code,handler", [("MONITOR_DATABASE_LIMIT", "file_refusal"),
                                          ("HOSTED_CAP_REACHED", "file_allowance")])
async def test_retry_records_admission_refusals(tmp_path, monkeypatch, code, handler):
    store = MonitorStore(tmp_path / "monitor.db")
    row, _ = store.claim_report("db", "nightly", NOW)
    report = {"computed": {}, "provisional_query_keys": [], "narrative_error": "temporary"}
    monkeypatch.setattr(reports.database_limit, "paused", lambda *args: False)
    monkeypatch.setattr(reports.database_limit, "target_fingerprint", lambda target: "fingerprint")
    calls = []
    monkeypatch.setattr(reports.database_limit, handler, lambda *args: calls.append(args))
    def refused(context):
        raise RuntimeError(code)
    monkeypatch.setattr(reports, "_narrate", refused)
    await reports.ReportScheduler(store, clock=lambda: NOW)._retry_narrative(row["id"], "db", report)
    assert calls
    assert report["narrative_retry_count"] == 1
