"""An existing install's data directory meets this build (docs/.../UPGRADE_NOTES.md).

The fixture is the shape of a long-running install: a config.toml with targets and a
retired trial section, the legacy queries.toml the SQLite library imported long ago,
library.db at the schema main ships with its older migration backups beside it, a
cache.db with observation history, and monitor.db at the oldest schema its ladder
handles. Startup runs the app lifespan and the first reads the UI makes.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
import toml

from shared.monitor import monitor_store
from shared.query_registry import library_store, observation_store
from shared.query_registry.query_registry import (
    QueryEntry,
    QueryRegistry,
    _sql_digest,
    hash_sql,
    normalize_sql,
)
from shared.query_registry.sql_normalizer import canonicalize_placeholder_style

# The library schema main ships; this build migrates from it.
MAIN_LIBRARY_SCHEMA = 15
NOW = 1_790_000_000

CONFIG = {
    "default": "prod",
    "init": {"completed": True, "version": "0.9.0"},
    "llm": {"provider": "readyset"},
    "account": {"user_id": "user-1", "email": "owner@example.com"},
    # Retired by an earlier release: the token goes, the address stays.
    "trial": {"email": "owner@example.com", "status": "active"},
    "targets": {
        "prod": {"engine": "postgresql", "host": "db.internal", "port": 5432,
                 "user": "rdst", "database": "shop", "password_env": "RDST_PROD_PASSWORD",
                 "tls": True, "tags": ["primary"]},
        "reports": {"engine": "mysql", "host": "10.0.0.7", "port": 3306, "user": "ro",
                    "database": "reports", "password_env": "RDST_REPORTS_PASSWORD"},
    },
}

# (text, targets). The first two share one identity once value lists of any length do.
QUERIES = [
    ("SELECT * FROM orders WHERE id IN (1, 2)", ["prod"]),
    ("SELECT * FROM orders WHERE id IN (1, 2, 3)", ["reports"]),
    ("SELECT count(*) FROM customers WHERE region = 'EU'", ["prod", "reports"]),
]


def _main_hash(text: str) -> str:
    """The identity hash main's library derived for this text."""
    return _sql_digest(canonicalize_placeholder_style(normalize_sql(text)))


def _entry(text: str, targets: list[str], index: int) -> dict:
    stamp = f"2026-08-0{index + 1}T10:00:00Z"
    return QueryEntry.from_dict({
        "sql": text, "original_sql": text, "hash": _main_hash(text), "tag": f"q{index}",
        "first_analyzed": stamp, "last_analyzed": stamp, "frequency": 5 + index,
        "source": "top", "last_target": targets[0],
        "target_lifecycle": {target: {"first_observed_at": stamp, "last_observed_at": stamp,
                                      "reviewed_at": stamp, "saved_at": stamp,
                                      "last_analyzed_at": stamp, "analysis_count": 1,
                                      "last_compared_at": "", "comparison_count": 0,
                                      "sources": ["top"]} for target in targets},
    }).to_dict()


def _dump(path: Path) -> list[str]:
    conn = sqlite3.connect(path)
    try:
        return list(conn.iterdump())
    finally:
        conn.close()


def _version(path: Path) -> int:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_old_install(data: Path, monkeypatch) -> dict:
    """Write every file an install from before this build holds."""
    data.mkdir(parents=True, exist_ok=True)
    (data / "config.toml").write_text(toml.dumps(CONFIG), encoding="utf-8")
    (data / "queries.toml").write_text(toml.dumps({"queries": {
        _main_hash(text): _entry(text, targets, index)
        for index, (text, targets) in enumerate(QUERIES)}}), encoding="utf-8")

    # library.db exactly as main's ladder leaves it, created before queries.toml existed
    # here so nothing is imported twice, then filled through the store's own writers.
    with monkeypatch.context() as patch:
        patch.setattr(library_store, "SCHEMA_VERSION", MAIN_LIBRARY_SCHEMA)
        store = library_store.LibraryStore(data / "library.db", data / "absent.toml")
        store.ensure_open()
        with store._write() as conn:
            for index, (text, targets) in enumerate(QUERIES):
                store._insert_entry(conn, _main_hash(text), _entry(text, targets, index))
        for index, (text, targets) in enumerate(QUERIES):
            store.record_analysis(query_hash=_main_hash(text), analysis_id=f"analysis-{index}",
                                  created_at=f"2026-08-0{index + 1}T10:05:00Z",
                                  target=targets[0], payload={"summary": f"finding {index}"})
    assert _version(data / "library.db") == MAIN_LIBRARY_SCHEMA
    # Backups earlier upgrades left behind are the user's; nothing may touch them.
    for old in ("pre-v8", "pre-v13"):
        (data / f"library.db.{old}.bak").write_bytes(f"backup {old}".encode())

    cache = observation_store.ObservationStore(data / "cache.db")
    cache.record_recent_observations("prod", [
        {"normalized_hash": _main_hash(text), "window_start": NOW - 60 * (minute + 1),
         "window_end": NOW - 60 * minute, "calls_delta": 10 * (index + 1),
         "exec_time_delta": 2.5, "approximate_qps": 0.2, "completeness": "complete"}
        for index, (text, _) in enumerate(QUERIES) for minute in range(3)])
    cache.close()

    with sqlite3.connect(data / "monitor.db") as conn:
        for statement in monitor_store._SCHEMA.split(";"):
            if statement.strip():
                conn.execute(statement)
        conn.execute("INSERT INTO monitor_setting VALUES ('prod','enabled','1')")
        conn.execute("INSERT INTO monitor_setting VALUES ('prod','execute','0')")
        conn.execute("INSERT INTO query_text VALUES ('prod','k1','SELECT 1',?,?)", (NOW - 600, NOW))
        conn.executemany("INSERT INTO stmt_series (target,query_key,res,bucket_start,calls,total_ms,rows,blks_hit,blks_read) VALUES ('prod','k1',60,?,?,?,1,0,0)",
                         [(NOW - 60 * m, 5, 12.5) for m in range(5)])
        conn.execute("INSERT INTO inbox_item (target,type,subject,state,severity,title,body_json,"
                     "created_at,updated_at) VALUES ('prod','stuck_query','k1','new',0.8,"
                     "'Stuck','{}',?,?)", (NOW, NOW))
        # History under main's identity keys: both value-list spellings in the same minutes.
        first, second = (_main_hash(text) for text, _ in QUERIES[:2])
        for key, (text, _) in zip((first, second), QUERIES[:2]):
            conn.execute("INSERT INTO query_text VALUES ('prod',?,?,?,?)", (key, text, NOW - 900, NOW))
            conn.executemany("INSERT INTO stmt_series (target,query_key,res,bucket_start,calls,total_ms,rows,blks_hit,blks_read) VALUES ('prod',?,60,?,4,20.0,4,0,0)",
                             [(key, NOW - 60 * m) for m in range(3)])
        conn.execute("INSERT INTO inbox_item (target,type,subject,state,severity,title,body_json,"
                     "created_at,updated_at) VALUES ('prod','latency_regression',?,'new',0.7,"
                     "'Slower',?,?,?)", (first, f'{{"query_keys": ["{first}", "{second}"]}}',
                                         NOW, NOW))
        conn.execute("INSERT INTO analyze_job (target,query_key,reason,state,priority,"
                     "enqueued_at,analysis_ref) VALUES ('prod',?,'new','done',1,?,?)",
                     (second, NOW, f"/api/query-registry/{second}/analysis/analysis-1"))
        conn.execute("INSERT INTO attention_episode (target,query_key,kind,baseline_mean_ms,"
                     "settled_at) VALUES ('prod',?,'latency_regression',5,?)",
                     (first, NOW))
        conn.execute("PRAGMA user_version=1")
    conn.close()
    return {"queries_toml": _digest(data / "queries.toml"),
            "library": _dump(data / "library.db"),
            "monitor": _dump(data / "monitor.db"),
            "backups": {name: _digest(data / name)
                        for name in ("library.db.pre-v8.bak", "library.db.pre-v13.bak")}}


class _Stub:
    async def start(self, *args, **kwargs):
        return self

    async def stop(self, *args, **kwargs):
        return None


def start_app(monkeypatch) -> dict:
    """The app's lifespan, then the first reads the UI makes, against the data dir."""
    from shared.agent_activity.store import AgentActivityStore
    from shared.api import app as api

    for target in ("shared.deploy.sandbox_manager.sandbox_manager",
                   "features.query_registry.assessment.query_assessment_worker"):
        monkeypatch.setattr(target, _Stub())
    monkeypatch.setattr("features.query_registry.scheduler.ObservationScheduler.start",
                        _Stub.start)
    monkeypatch.setattr("features.query_registry.scheduler.ObservationScheduler.stop",
                        _Stub.stop)
    monitoring: list[bool] = []

    def start_monitor_loop(**kwargs):
        monitoring.append(True)
        return _Stub().start()

    monkeypatch.setattr("features.monitor.loop.start_monitor_loop", start_monitor_loop)
    monkeypatch.setattr("features.monitor.analyze_queue.start_analyze_queue",
                        lambda: _Stub().start())
    monkeypatch.setattr("features.monitor.report.ReportScheduler", _Stub)

    async def run() -> None:
        async with api.lifespan(None):
            pass

    asyncio.run(run())
    registry = QueryRegistry()
    registry.load()
    observation_store.ObservationStore().close()
    monitor = monitor_store.MonitorStore()
    AgentActivityStore().ensure_open()
    return {"registry": registry, "monitor": monitor, "monitoring_started": bool(monitoring)}


@pytest.fixture
def data(tmp_path, monkeypatch):
    path = tmp_path / ".rdst"
    monkeypatch.setenv("RDST_DATA_DIR", str(path))
    # Each store remembers files it already opened in this process.
    monkeypatch.setattr(monitor_store, "_OPENED", set())
    return path


def test_upgrade_keeps_everything_backs_up_first_and_is_idempotent(data, monkeypatch):
    before = build_old_install(data, monkeypatch)
    started = start_app(monkeypatch)

    from shared.config.targets import TargetsConfig

    config = TargetsConfig()
    config.load()
    assert {name: config.get(name) for name in config.list_targets()} == CONFIG["targets"]
    assert config.get_default() == "prod"
    assert config.get_account_config()["user_id"] == "user-1"
    assert config.get_email() == "owner@example.com"
    assert not config.get_trial_config()

    # The legacy TOML was imported long ago: it is neither re-read nor rewritten.
    assert _digest(data / "queries.toml") == before["queries_toml"]
    assert not list(data.glob("queries.toml.pre-sqlite-*.bak"))

    # library.db: backed up before its ladder ran, older backups untouched.
    assert _version(data / "library.db") == library_store.SCHEMA_VERSION
    backup = data / f"library.db.pre-v{MAIN_LIBRARY_SCHEMA}.bak"
    assert backup.exists() and _dump(backup) == before["library"]
    for name, digest in before["backups"].items():
        assert _digest(data / name) == digest

    registry: QueryRegistry = started["registry"]
    by_text = {entry.original_sql: entry for entry in registry.list_queries()}
    for text, targets in QUERIES:
        identity = hash_sql(text)
        entry = registry.get_query(identity)
        assert entry is not None, text
        assert set(targets) <= set(entry.target_lifecycle), text
    assert len({hash_sql(text) for text, _ in QUERIES}) <= len(by_text) <= len(QUERIES)

    # Every analysis is still reachable from its query.
    store = registry.library_store
    reachable = {payload["summary"] for text, _ in QUERIES
                 for payload in store.analysis_payloads(hash_sql(text))}
    assert reachable == {f"finding {index}" for index in range(len(QUERIES))}

    # cache.db history follows the identities, with no calls lost.
    with sqlite3.connect(data / "cache.db") as conn:
        rows = conn.execute("SELECT normalized_hash, SUM(calls_delta) FROM recent_observation "
                            "GROUP BY normalized_hash").fetchall()
    conn.close()
    assert sum(calls for _, calls in rows) == 3 * (10 + 20 + 30)
    assert {hashed for hashed, _ in rows} == {hash_sql(text) for text, _ in QUERIES}

    # monitor.db: backed up, migrated, nothing dropped, monitoring still on.
    monitor = started["monitor"]
    assert _version(data / "monitor.db") == monitor_store.SCHEMA_VERSION
    assert _dump(data / "monitor.db.pre-v1.bak") == before["monitor"]
    assert monitor.monitored_targets() == {"prod"}
    assert monitor.levers("prod")["execute"] is False
    assert "k1" in [item["subject"] for item in monitor.inbox(target="prod")]
    assert monitor.read("SELECT SUM(calls) AS n FROM stmt_series WHERE res=60")[0]["n"] == 25 + 24
    # Its query keys follow the library's re-key; the two spellings' minutes add up.
    merged = hash_sql(QUERIES[0][0])
    assert monitor.read("SELECT query_key, SUM(calls) AS n FROM stmt_series WHERE res=60 "
                        "AND query_key!='k1' GROUP BY query_key") == [{"query_key": merged, "n": 24}]
    assert [row["query_key"] for row in monitor.read(
        "SELECT query_key FROM query_text WHERE query_key!='k1'")] == [merged]
    alert = next(item for item in monitor.inbox(target="prod") if item["type"] == "latency_regression")
    assert alert["subject"] == merged
    assert json.loads(alert["body_json"])["query_keys"] == [merged, merged]
    (job,) = monitor.read("SELECT query_key, analysis_ref FROM analyze_job")
    assert job["query_key"] == merged
    assert store.analysis_payload(merged, job["analysis_ref"].rsplit("/", 1)[1])["summary"] == "finding 1"
    assert monitor.read("SELECT query_key FROM attention_episode") == [{"query_key": merged}]
    assert started["monitoring_started"]

    # A second startup changes nothing.
    files = sorted(path.name for path in data.iterdir())
    dumps = {name: _dump(data / name) for name in ("library.db", "monitor.db", "cache.db")}
    config_text = (data / "config.toml").read_text()
    monkeypatch.setattr(monitor_store, "_OPENED", set())
    start_app(monkeypatch)
    assert sorted(path.name for path in data.iterdir()) == files
    assert {name: _dump(data / name) for name in dumps} == dumps
    assert (data / "config.toml").read_text() == config_text


def test_fresh_legacy_toml_install_imports_with_a_backup(data, monkeypatch):
    data.mkdir(parents=True)
    (data / "config.toml").write_text(toml.dumps(CONFIG), encoding="utf-8")
    (data / "queries.toml").write_text(toml.dumps({"queries": {
        _main_hash(text): _entry(text, targets, index)
        for index, (text, targets) in enumerate(QUERIES)}}), encoding="utf-8")
    original = (data / "queries.toml").read_bytes()
    registry = start_app(monkeypatch)["registry"]
    assert (data / "queries.toml").read_bytes() == original
    assert (data / f"queries.toml.pre-sqlite-{library_store.SCHEMA_VERSION}.bak").read_bytes() == original
    for text, targets in QUERIES:
        assert set(targets) <= set(registry.get_query(hash_sql(text)).target_lifecycle)
    # No monitoring was on, so none starts and monitor.db isn't created.
    assert not (data / "monitor.db").exists()


def test_older_build_meeting_newer_files_refuses_without_damage(data, monkeypatch):
    build_old_install(data, monkeypatch)
    start_app(monkeypatch)
    for name, bump in (("library.db", library_store.SCHEMA_VERSION + 1),
                       ("monitor.db", monitor_store.SCHEMA_VERSION + 1)):
        with sqlite3.connect(data / name) as conn:
            conn.execute(f"PRAGMA user_version={bump}")
        conn.close()
    library_before, monitor_before = _dump(data / "library.db"), _dump(data / "monitor.db")

    # library.db: reads still work, writes refuse with the reason, the file is unchanged.
    registry = QueryRegistry()
    registry.load()
    assert registry.get_query(hash_sql(QUERIES[2][0])) is not None
    with pytest.raises(RuntimeError, match="newer"):
        registry.add_query("SELECT 2", source="manual")
    assert _dump(data / "library.db") == library_before

    # monitor.db: refused outright, never migrated or rebuilt.
    monkeypatch.setattr(monitor_store, "_OPENED", set())
    with pytest.raises(RuntimeError, match="newer than this build"):
        monitor_store.MonitorStore().monitored_targets()
    assert _dump(data / "monitor.db") == monitor_before
    # The app still starts for Ask and Analyze; monitoring stays off until RDST is updated.
    assert start_app(monkeypatch)["monitoring_started"] is False
    assert _dump(data / "monitor.db") == monitor_before

    # cache.db is rebuildable: a newer file is set aside whole, never edited in place.
    with sqlite3.connect(data / "cache.db") as conn:
        conn.execute(f"PRAGMA user_version={observation_store.SCHEMA_VERSION + 1}")
    conn.close()
    newer = _dump(data / "cache.db")
    observation_store.ObservationStore().close()
    (aside,) = data.glob("cache.db.corrupt-*")
    assert _dump(aside) == newer

    # config.toml keeps keys this build doesn't know.
    text = (data / "config.toml").read_text() + '\n[future]\nflag = "kept"\n'
    (data / "config.toml").write_text(text)
    from shared.config.targets import TargetsConfig

    config = TargetsConfig()
    config.load()
    config.set_default("reports")
    config.save()
    assert toml.loads((data / "config.toml").read_text())["future"] == {"flag": "kept"}
