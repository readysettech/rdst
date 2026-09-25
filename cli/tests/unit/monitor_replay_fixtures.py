"""Small deterministic incident series for the attention replay."""

from __future__ import annotations

from shared.monitor.monitor_store import MonitorStore

NOW = 2_000_040


def series(store: MonitorStore, key: str, *, now: int = NOW, before_calls: int = 60,
           before_ms: float = 10, after_calls: int = 60, after_ms: float = 10,
           sql: str = "SELECT * FROM orders") -> None:
    """Write a full prior hour and the current five minutes, in minute buckets."""
    rows = [("db", key, 60, ts // 60 * 60, calls, calls * mean, 0, 0, 0)
            for start, count, calls, mean in ((now - 3900, 60, before_calls, before_ms),
                                               (now - 300, 5, after_calls, after_ms))
            for ts in (start + index * 60 for index in range(count))]
    def write(conn):
        conn.execute("INSERT OR REPLACE INTO query_text VALUES (?,?,?,?,?)",
                     ("db", key, sql, now - 3900, now))
        conn.executemany("INSERT OR REPLACE INTO stmt_series (target,query_key,res,bucket_start,calls,total_ms,rows,blks_hit,blks_read) VALUES (?,?,?,?,?,?,?,?,?)", rows)
    store._write(write)
    store.record("db", now - 60, [], {}, [], {"calls": after_calls,
                 "total_ms": after_calls * after_ms, "active_conns": 2,
                 "max_conns": 20, "cache_hit_ratio": 0.99})


def planted(store: MonitorStore, *, now: int = NOW) -> None:
    series(store, "heavy_regression", now=now, before_ms=100, after_ms=250,
           before_calls=100, after_calls=100)
    series(store, "traffic_spike", now=now, before_calls=30, after_calls=90, before_ms=100,
           after_ms=100)
    series(store, "always_slow", now=now, before_calls=20, after_calls=20,
           before_ms=2000, after_ms=2000)
    series(store, "index_improved", now=now, before_calls=20, after_calls=20,
           before_ms=1000, after_ms=400)
    # New query: current five minutes only.
    def write(conn):
        conn.execute("INSERT OR REPLACE INTO query_text VALUES (?,?,?,?,?)",
                     ("db", "new_heavy", "SELECT * FROM new_orders", now - 300, now))
        conn.executemany("INSERT OR REPLACE INTO stmt_series (target,query_key,res,bucket_start,calls,total_ms,rows,blks_hit,blks_read) VALUES (?,?,?,?,?,?,?,?,?)",
                         [("db", "new_heavy", 60, (now - 300 + i * 60) // 60 * 60,
                           100, 100_000, 0, 0, 0) for i in range(5)])
    store._write(write)
    store.rank_queries("db", now)


def many_slowdowns(store: MonitorStore, *, now: int = NOW) -> None:
    for index in range(6):
        series(store, f"slow_{index}", now=now, before_calls=100,
               after_calls=100, before_ms=10, after_ms=20)
