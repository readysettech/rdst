import json
import sqlite3

from shared.monitor.monitor_store import MonitorStore, SCHEMA_VERSION, _SCHEMA


def test_v1_store_migrates_without_losing_inbox(tmp_path):
    path = tmp_path / "monitor.db"
    with sqlite3.connect(path) as conn:
        for statement in _SCHEMA.split(";"):
            if statement.strip():
                conn.execute(statement)
        conn.execute("INSERT INTO inbox_item (target,type,subject,state,severity,title,body_json,"
                     "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                     ("db", "stuck_query", "q", "new", 0.8, "Stuck", "{}", 1, 1))
        conn.execute("PRAGMA user_version=1")
    store = MonitorStore(path)
    assert store.inbox()[0]["subject"] == "q"
    assert "snoozed_until" in store.inbox()[0]
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert path.with_name("monitor.db.pre-v1.bak").exists()


def test_v2_report_migration_keeps_one_active_run(tmp_path):
    path = tmp_path / "monitor.db"
    with sqlite3.connect(path) as conn:
        for statement in _SCHEMA.split(";"):
            if statement.strip():
                conn.execute(statement)
        conn.execute("ALTER TABLE inbox_item ADD COLUMN snoozed_until INTEGER")
        conn.executemany("INSERT INTO report_run (target,ts,trigger,state) VALUES (?,?,?,?)",
                         [("db", 1, "user", "running"), ("db", 2, "nightly", "running")])
        conn.execute("PRAGMA user_version=2")
    store = MonitorStore(path)
    rows = store.read("SELECT id,state FROM report_run ORDER BY id")
    assert [row["state"] for row in rows] == ["failed", "running"]
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert "heartbeat_at" in {row[1] for row in conn.execute("PRAGMA table_info(report_run)")}


def test_v5_database_series_migration_keeps_history(tmp_path):
    path = tmp_path / "monitor.db"
    store = MonitorStore(path)
    store.record("db", 60, [], {}, [], {
        "calls": 1, "total_ms": 2, "cache_hit_ratio": 0.5})
    with sqlite3.connect(path) as conn:
        conn.execute("ALTER TABLE db_series DROP COLUMN blks_hit")
        conn.execute("ALTER TABLE db_series DROP COLUMN blks_read")
        conn.execute("DROP INDEX judge_run_target_ts")
        conn.execute("DROP INDEX inbox_event_ts")
        conn.execute("PRAGMA user_version=5")
    migrated = MonitorStore(path)
    row = migrated.read("SELECT calls,cache_hit_ratio,blks_hit,blks_read FROM db_series")[0]
    assert row == {"calls": 1, "cache_hit_ratio": 0.5,
                   "blks_hit": None, "blks_read": None}


def test_v8_migration_preserves_used_dismissal_return(tmp_path):
    path = tmp_path / "monitor.db"
    store = MonitorStore(path)
    item = store.upsert_item("db", "regression", "q", 0.2, "Changed", {}, 100)
    store.action_item(item["id"], "dismiss", 101)
    store.upsert_item("db", "regression", "q", 0.5, "Changed", {}, 102, materially_worse=True)
    store.action_item(item["id"], "dismiss", 103)
    with sqlite3.connect(path) as conn:
        conn.execute("ALTER TABLE inbox_item DROP COLUMN dismissed_returned")
        conn.execute("DROP TABLE dirty_rollup")
        conn.execute("PRAGMA user_version=8")
    migrated = MonitorStore(path)
    row = migrated.inbox("dismissed", "db")[0]
    assert row["dismissed_returned"] == 1
    assert migrated.upsert_item("db", "regression", "q", 0.8, "Changed", {},
                                104)["state"] == "dismissed"


def test_v10_migration_adds_billable_counts_without_losing_runs(tmp_path):
    path = tmp_path / "monitor.db"
    store = MonitorStore(path)
    store.record_judge_run("db", 1, [], outcome="empty")
    store.claim_report("db", "user", 1)
    store.enqueue_analysis("db", "q", "user", 1, 1)
    with sqlite3.connect(path) as conn:
        for table in ("judge_run", "analyze_job", "report_run"):
            conn.execute(f"ALTER TABLE {table} DROP COLUMN billable_calls")
        conn.execute("PRAGMA user_version=10")
    migrated = MonitorStore(path)
    for table in ("judge_run", "analyze_job", "report_run"):
        assert migrated.read(f"SELECT billable_calls FROM {table}") == [{"billable_calls": None}]
    migrated.record_judge_run("new", 2, [])
    migrated.claim_report("new", "user", 2)
    migrated.enqueue_analysis("new", "q", "user", 1, 2)
    for table in ("judge_run", "analyze_job", "report_run"):
        assert migrated.read(f"SELECT billable_calls FROM {table} ORDER BY id DESC LIMIT 1") == [
            {"billable_calls": 0}]


def test_v11_migration_adds_attention_episodes_without_losing_inbox(tmp_path):
    path = tmp_path / "monitor.db"
    store = MonitorStore(path)
    item = store.upsert_item("db", "latency_regression", "q", .8,
                             "Query got slower", {"query_key": "q"}, 10)
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE attention_episode")
        conn.execute("PRAGMA user_version=11")
    migrated = MonitorStore(path)
    assert migrated.inbox("new")[0]["id"] == item["id"]
    assert migrated.read("SELECT * FROM attention_episode") == []


def test_v12_migration_recovers_judged_qps_from_series(tmp_path):
    path = tmp_path / "monitor.db"
    store = MonitorStore(path)
    store.record_attention_episodes([("db", "q", "query_improved", 100, 600, 1)])
    store._write(lambda conn: conn.execute(
        "INSERT INTO stmt_series (target,query_key,res,bucket_start,calls,total_ms,rows,blks_hit,blks_read) VALUES (?,?,?,?,?,?,?,?,?)",
        ("db", "q", 60, 540, 60, 2400, 0, 0, 0)))
    with sqlite3.connect(path) as conn:
        conn.execute("ALTER TABLE attention_episode DROP COLUMN judged_qps")
        conn.execute("PRAGMA user_version=12")
    migrated = MonitorStore(path)
    assert migrated.read("SELECT judged_qps FROM attention_episode") == [{"judged_qps": .2}]


def test_v18_rekey_follows_library_identities_and_keeps_colliding_rows(tmp_path):
    from shared.query_registry.query_registry import _sql_digest, hash_sql, normalize_sql
    from shared.query_registry.sql_normalizer import canonicalize_placeholder_style

    two, three = "SELECT * FROM t WHERE id IN (1, 2)", "SELECT * FROM t WHERE id IN (1, 2, 3)"
    old_two, old_three = (_sql_digest(canonicalize_placeholder_style(normalize_sql(text)))
                          for text in (two, three))
    new = hash_sql(two)
    assert new == hash_sql(three) and new not in (old_two, old_three)
    path = tmp_path / "monitor.db"
    store = MonitorStore(path)
    store.ensure_open()
    with sqlite3.connect(path) as conn:
        for key, text in ((old_two, two), (old_three, three), ("other", "SELECT 1")):
            conn.execute("INSERT INTO query_text VALUES ('db',?,?,1,2)", (key, text))
            conn.execute("INSERT INTO stmt_series (target,query_key,res,bucket_start,calls,total_ms,rows,blks_hit,blks_read) VALUES ('db',?,60,60,3,6.0,3,0,0)", (key,))
        for key in (old_two, old_three):
            conn.execute("INSERT INTO inbox_item (target,type,subject,state,severity,title,"
                         "body_json,created_at,updated_at) VALUES ('db','latency_regression',"
                         "?,'new',0.5,'Slower',?,1,1)", (key, f'{{"query_key": "{key}"}}'))
            conn.execute("INSERT INTO attention_episode (target,query_key,kind,baseline_mean_ms,"
                         "settled_at) VALUES ('db',?,'latency_regression',1,1)", (key,))
        conn.execute("PRAGMA user_version=18")
    conn.close()
    migrated = MonitorStore(path)
    rows = migrated.read("SELECT query_key, calls FROM stmt_series ORDER BY query_key")
    assert sorted((row["query_key"], row["calls"]) for row in rows) == sorted(
        [(new, 6), ("other", 3)])
    subjects = sorted(row["subject"] for row in migrated.read("SELECT subject FROM inbox_item"))
    assert new in subjects and len(subjects) == 1
    [merged] = migrated.read("SELECT body_json FROM inbox_item")
    assert len(json.loads(merged["body_json"])["occurrences"]) == 2
    assert migrated.read("SELECT query_key FROM attention_episode") == [{"query_key": new}]
    assert all(json.loads(row["body_json"])["query_key"] in (new, row["subject"])
               for row in migrated.read("SELECT subject, body_json FROM inbox_item"))
    assert path.with_name("monitor.db.pre-v18.bak").exists()
