"""Report capacity and immediate recovery of abandoned analysis claims."""

import os
import subprocess
import sys

import psutil
import pytest

from features.monitor.analyze_queue import enqueue_for_report
from shared.monitor.monitor_store import MonitorStore, OWNER_HOST

NOW = 1_800_000_000


def _store(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: NOW)
    store.set_settings("db", {"enabled": True, "deep_analysis": True,
                              "execute": True, "analysis_daily_cap": 10,
                              "newcomer_daily_cap": 0})
    return store


def test_report_skipped_writes_leave_capacity_for_ten_reads(tmp_path):
    store = _store(tmp_path)
    keys = ["write"] + [f"read{i}" for i in range(11)]
    texts = {key: "INSERT INTO t VALUES (1)" if key == "write" else
             f"SELECT * FROM t WHERE id = {index}" for index, key in enumerate(keys)}
    store.record("db", NOW, [{"query_key": key, "calls": 10, "total_ms": 100 - index}
                            for index, key in enumerate(keys)], texts, [], None)
    jobs = enqueue_for_report("db", keys, store=store, now=NOW)
    assert [job["query_key"] for job in jobs] == keys[1:11]
    assert store.latest_analysis_jobs("db")["write"]["state"] == "skipped"
    for index in range(10):
        job = store.claim_analysis("db", NOW + index, cap=10, newcomer_cap=0)
        assert job["query_key"] == keys[index + 1]
        assert store.finish_analysis(job["id"], "done", NOW + index,
                                     owner=job["owner"], attempt=job["attempt"])
    assert store.claim_analysis("db", NOW + 10, cap=10, newcomer_cap=0) is None


@pytest.mark.parametrize("recover", [False, True])
def test_dead_local_owner_reclaimed_before_lease_expiry(tmp_path, recover):
    store = _store(tmp_path)
    store.enqueue_analysis("db", "q", "user", 1, NOW)
    old = store.claim_analysis("db", NOW)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                             stdin=subprocess.DEVNULL)
    try:
        start = psutil.Process(child.pid).create_time()
        store._write(lambda conn: conn.execute(
            "UPDATE analyze_job SET owner_pid=?,owner_start=? WHERE id=?",
            (child.pid, start, old["id"])))
        assert store.claim_analysis("db", NOW + 1) is None
    finally:
        child.terminate()
        child.wait(timeout=5)
    if recover:
        store.recover_analysis(NOW + 2, 600)
    fresh = store.claim_analysis("db", NOW + 2)
    assert fresh["id"] == old["id"]
    assert fresh["attempt"] > old["attempt"]
    assert fresh["owner_host"] == OWNER_HOST
    assert fresh["owner_pid"] == os.getpid()
    assert fresh["owner_start"] == psutil.Process().create_time()
    assert not store.analysis_owned(old)
    assert not store.retry_analysis(old)
    assert not store.finish_analysis(old["id"], "done", NOW + 3,
                                     owner=old["owner"], attempt=old["attempt"])
    assert store.analysis_owned(fresh)


@pytest.mark.parametrize("owner", ["live", "remote", "legacy", "reused", "denied"])
def test_owner_identity_controls_early_recovery(tmp_path, monkeypatch, owner):
    from shared.monitor import monitor_store

    store = _store(tmp_path)
    store.enqueue_analysis("db", "q", "user", 1, NOW)
    old = store.claim_analysis("db", NOW)
    if owner == "remote":
        store._write(lambda conn: conn.execute("UPDATE analyze_job SET owner_host='another-host'"))
    elif owner == "legacy":
        store._write(lambda conn: conn.execute("UPDATE analyze_job SET owner_start=NULL"))
    elif owner == "reused":
        store._write(lambda conn: conn.execute("UPDATE analyze_job SET owner_start=owner_start-1"))
    elif owner == "denied":
        def denied(pid):
            raise psutil.AccessDenied(pid)
        monkeypatch.setattr(monitor_store, "_process_start", denied)
    store.recover_analysis(NOW + 1, 600)
    fresh = store.claim_analysis("db", NOW + 2)
    if owner == "reused":
        assert fresh["attempt"] > old["attempt"]
    else:
        assert fresh is None
        assert store.analysis_owned(old)


def test_upgrade_preserves_legacy_running_claim(tmp_path):
    store = _store(tmp_path)
    store.enqueue_analysis("db", "q", "user", 1, NOW)
    old = store.claim_analysis("db", NOW)
    def downgrade(conn):
        for name in ("owner_host", "owner_pid", "owner_start"):
            conn.execute(f"ALTER TABLE analyze_job DROP COLUMN {name}")
        conn.execute("PRAGMA user_version=24")
    store._write(downgrade)
    upgraded = MonitorStore(tmp_path / "monitor.db", clock=lambda: NOW)
    assert upgraded.analysis_owned(old)
    assert upgraded.claim_analysis("db", NOW + 1) is None
    upgraded.recover_analysis(NOW + 600, 600)
    fresh = upgraded.claim_analysis("db", NOW + 601)
    assert fresh["attempt"] > old["attempt"]
    assert fresh["owner_pid"] == os.getpid()
