"""Run collector and attention loop directly for three minutes on local MySQL."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from load import connect, run, run_stuck, seed  # noqa: E402
from features.monitor.api import routes  # noqa: E402
from features.monitor.loop import MonitorLoop  # noqa: E402
from features.query_registry.discovery import QueryDiscoveryCollector  # noqa: E402
from features.analyze.parameter_suggestions import suggest_parameter_values  # noqa: E402
from shared.config.targets import TargetsConfig  # noqa: E402
from shared.monitor.monitor_store import MonitorStore  # noqa: E402
from shared.query_registry.observation_store import ObservationStore  # noqa: E402


TARGET = "mysql-soak"
INDEX = "idx_monitor_soak_total"


async def verify() -> dict:
    if "RDST_DATA_DIR" not in os.environ or "RDST_MYSQL_SOAK_PASSWORD" not in os.environ:
        raise RuntimeError("Set RDST_DATA_DIR and RDST_MYSQL_SOAK_PASSWORD")
    seed()
    config = TargetsConfig()
    config.load()
    config.upsert(TARGET, {"engine": "mysql", "host": "127.0.0.1", "port": 13306,
                           "database": "shopdb", "user": "root",
                           "password_env": "RDST_MYSQL_SOAK_PASSWORD"})
    config.save()
    store = MonitorStore()
    store.set_settings(TARGET, {"enabled": True})
    collector = QueryDiscoveryCollector(TARGET, two_phase=True, store=ObservationStore())
    loop = MonitorLoop(store, judge=lambda *_: None)
    stop = threading.Event()
    workers = run(stop)
    stuck = threading.Thread(target=run_stuck, daemon=True)
    stuck.start()
    start = time.monotonic()
    events = []
    added = False
    removed = False
    try:
        while time.monotonic() - start < 180:
            elapsed = time.monotonic() - start
            if elapsed >= 35 and not added:
                with connect() as conn, conn.cursor() as cursor:
                    cursor.execute(f"CREATE INDEX {INDEX} ON orders (total)")
                added = True
            if elapsed >= 110 and not removed:
                with connect() as conn, conn.cursor() as cursor:
                    cursor.execute(f"DROP INDEX {INDEX} ON orders")
                removed = True
            event = await collector.collect_now()
            events.append(event.event)
            loop.cycle(TARGET)
            await asyncio.sleep(5)
    finally:
        stop.set()
        for worker in workers:
            worker.join(timeout=5)
        stuck.join(timeout=1)
        if added and not removed:
            with connect() as conn, conn.cursor() as cursor:
                cursor.execute(f"DROP INDEX {INDEX} ON orders")
    queries = store.read("SELECT query_key,text FROM query_text WHERE target=?", (TARGET,))
    sampled = None
    for query in queries:
        if "orders" in query["text"].lower() and "?" in query["text"]:
            sampled = suggest_parameter_values(query["text"], TARGET, config.get(TARGET),
                                               query["query_key"])
            if sampled.get("sample"):
                break
    result = {
        "elapsed_s": round(time.monotonic() - start, 1),
        "collector_events": {name: events.count(name) for name in set(events)},
        "stmt_rows": store.read("SELECT COUNT(*) AS n FROM stmt_series WHERE target=?", (TARGET,))[0]["n"],
        "db_rows": store.read("SELECT COUNT(*) AS n FROM db_series WHERE target=?", (TARGET,))[0]["n"],
        "latest_db": store.read("SELECT * FROM db_series WHERE target=? ORDER BY bucket_start DESC LIMIT 1", (TARGET,)),
        "health_tiles": routes.health(TARGET)["tiles"],
        "index_changes": store.read("SELECT kind,index_name FROM index_change WHERE target=?", (TARGET,)),
        "stuck_samples": store.read("SELECT pid,duration_s FROM stuck_sample WHERE target=?", (TARGET,)),
        "parameter_sample": sampled.get("sample") if sampled else None,
    }
    return result


if __name__ == "__main__":
    print(json.dumps(asyncio.run(verify()), indent=2, default=str))
