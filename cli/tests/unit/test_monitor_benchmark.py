"""Opt-in local cost measurement for retained minute history."""

import os
import time

import pytest

from shared.monitor.monitor_store import MonitorStore


@pytest.mark.slow
@pytest.mark.skipif(os.getenv("RDST_RUN_MONITOR_SLOW") != "1",
                    reason="set RDST_RUN_MONITOR_SLOW=1 to build 14.4 million rows")
def test_rollup_cost(tmp_path):
    now = 48 * 3600 + 3600
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: now,
                         budget_bytes=10**12)
    store.ensure_open()
    with store._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.executemany("INSERT INTO query_text VALUES (?,?,?,?,?)",
                         [("db", f"q{i:04d}", "SELECT * FROM t WHERE id = 1", 0, now)
                          for i in range(5000)])
        conn.execute(
            "WITH RECURSIVE minutes(m) AS (SELECT 0 UNION ALL SELECT m+1 FROM minutes WHERE m<2879), "
            "keys(k) AS (SELECT 0 UNION ALL SELECT k+1 FROM keys WHERE k<4999) "
            "INSERT INTO stmt_series SELECT 'db',printf('q%04d',k),60,m*60,1,1.0,0,0,0 "
            "FROM minutes CROSS JOIN keys")
        conn.execute("COMMIT")
    # Build the existing rollups before timing one normal five-minute write.
    store.rollup("db", now)
    with store._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "WITH RECURSIVE minutes(m) AS (SELECT 0 UNION ALL SELECT m+1 FROM minutes WHERE m<4), "
            "keys(k) AS (SELECT 0 UNION ALL SELECT k+1 FROM keys WHERE k<4999) "
            "INSERT INTO stmt_series SELECT 'db',printf('q%04d',k),60,?+m*60,1,1.0,0,0,0 "
            "FROM minutes CROSS JOIN keys", (now,))
        conn.execute("COMMIT")
    start = time.perf_counter()
    store.rollup("db", now + 300)
    rollup_s = time.perf_counter() - start
    print(f"monitor benchmark: rollup={rollup_s:.3f}s")
