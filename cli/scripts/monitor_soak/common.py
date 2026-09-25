"""Shared local-only state and query identifiers for the monitor soak."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = ROOT / "docs/plans/continuous_analysis/logs/soak"
CONTROL = LOG_DIR / "control.json"
LEDGER = LOG_DIR / "incidents.jsonl"
BASELINE = LOG_DIR / "baseline.json"
INDEX = "idx_monitor_soak_orders_total"

# Stable SQL shapes make it possible to match API query keys without knowing the
# registry's hash algorithm. The new-heavy shape is unique to each run and cycle.
HOT_SQL = "SELECT id, total FROM orders WHERE total >= %s AND total < %s ORDER BY total LIMIT 20"
HOT_COUNT_SQL = "SELECT count(*) FROM orders WHERE total >= %s AND total < %s"
STEADY_SQL = "SELECT count(*) FROM customers WHERE country = %s"
STUCK_SQL = "SELECT pg_sleep(180) FROM orders WHERE id = 1"

DEFAULT_CONTROL = {"rate_multiplier": 1.0, "heavy_enabled": False,
                   "steady_enabled": True}


def heavy_sql(cycle: int, run_id: str) -> str:
    """Give each phase a distinct queryid without changing the expensive join."""
    if cycle < 1 or len(run_id) != 32 or any(char not in "0123456789abcdef" for char in run_id):
        raise ValueError("expected a positive cycle and hex run ID")
    # min/max are different parse nodes. Unlike parameters or aliases, these
    # columns remain distinct when pg_stat_statements normalizes the query.
    run_columns = [f"{'max' if int(char, 16) & bit else 'min'}(o.total)"
                   for char in run_id for bit in (8, 4, 2, 1)]
    cycle_columns = [f"{'max' if bit == '1' else 'min'}(o.total)"
                     for bit in f"{cycle:b}"]
    columns = ["c.country", "count(*)", "sum(o.total)", *run_columns, *cycle_columns]
    return (f"SELECT {', '.join(columns)} FROM customers c "
            "JOIN orders o ON o.customer_id = c.id WHERE o.total > %s "
            "GROUP BY c.country ORDER BY sum(o.total) DESC")


def connection_args(superuser: bool = False) -> dict:
    return {"host": "127.0.0.1", "port": 15433, "dbname": "shopdb",
            "user": "postgres" if superuser else "rdst",
            "password": os.environ.get("LOCALDEV_ADMIN_PASSWORD", "localdev") if superuser
            else os.environ.get("LOCALDEV_PASSWORD", "rdst-localdev"),
            "connect_timeout": 5,
            "application_name": "rdst-monitor-soak-admin" if superuser else "rdst-monitor-soak-load"}


def write_control(**changes: object) -> dict:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    state = {**DEFAULT_CONTROL, **read_control(), **changes}
    temp = CONTROL.with_suffix(f".{os.getpid()}.tmp")
    temp.write_text(json.dumps(state, sort_keys=True) + "\n", encoding="ascii")
    os.replace(temp, CONTROL)
    return state


def read_control() -> dict:
    try:
        state = json.loads(CONTROL.read_text(encoding="ascii"))
        return state if isinstance(state, dict) else DEFAULT_CONTROL.copy()
    except (OSError, ValueError):
        return DEFAULT_CONTROL.copy()


def ledger_event(cycle: int, kind: str, phase: str, **details: object) -> dict:
    event = {"ts": int(time.time()), "cycle": cycle, "kind": kind,
             "phase": phase, **details}
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="ascii") as out:
        out.write(json.dumps(event, sort_keys=True) + "\n")
        out.flush()
        os.fsync(out.fileno())
    return event
