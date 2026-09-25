"""Steady, adjustable Postgres traffic for the local monitor target."""

from __future__ import annotations

import argparse
import logging
import random
import signal
import threading
import time

import psycopg2

from common import (HOT_COUNT_SQL, HOT_SQL, STEADY_SQL, connection_args,
                    heavy_sql, read_control)

STOP = threading.Event()
LOG = logging.getLogger(__name__)


class StopRequested(BaseException):
    pass


def request_stop(_signum, _frame) -> None:
    raise StopRequested


def execute_loop(name: str, interval: float, select, enabled=lambda state: True) -> None:
    connection = None
    while not STOP.is_set():
        state = read_control()
        if not enabled(state):
            STOP.wait(0.5)
            continue
        try:
            if connection is None or connection.closed:
                connection = psycopg2.connect(**connection_args())
                connection.autocommit = True
                with connection.cursor() as cursor:
                    cursor.execute("SET statement_timeout = '20s'")
            sql, params = select(state)
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                cursor.fetchall()
        except psycopg2.Error as exc:
            LOG.warning("%s: %s", name, exc)
            if connection is not None:
                connection.close()
            connection = None
            STOP.wait(3)
        multiplier = max(0.1, min(5.0, float(state.get("rate_multiplier", 1))))
        STOP.wait(interval / multiplier)
    if connection is not None:
        connection.close()


def mixed_query():
    choice = random.choices(("hot", "items", "status", "product"), (40, 25, 20, 15))[0]
    if choice == "hot":
        low = random.randrange(50, 950, 10)
        return HOT_SQL, (low, low + 10)
    if choice == "items":
        return "SELECT product_id, quantity FROM order_items WHERE order_id = %s", (random.randint(1, 60000),)
    if choice == "status":
        return "SELECT count(*) FROM orders WHERE status = %s", (random.choice(("paid", "shipped", "pending")),)
    return "SELECT id, name FROM products WHERE category = %s LIMIT 25", (random.choice(("books", "home", "toys")),)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--interval", type=float, default=0.15)
    args = parser.parse_args()
    if args.workers < 1 or args.interval <= 0:
        parser.error("workers and interval must be positive")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    threads = [threading.Thread(target=execute_loop,
                                args=(f"mixed-{n}", args.interval, lambda state: mixed_query()), daemon=True)
               for n in range(args.workers)]
    threads += [threading.Thread(target=execute_loop,
                                 args=("hot-count", 0.15, lambda state: (
                                     HOT_COUNT_SQL, (500, 510))), daemon=True),
                threading.Thread(target=execute_loop,
                                 args=("steady", 0.5, lambda state: (STEADY_SQL, ("US",))),
                                 kwargs={"enabled": lambda state: state.get("steady_enabled", True)}, daemon=True),
                threading.Thread(target=execute_loop,
                                 args=("heavy", 2.0, lambda state: (
                                     heavy_sql(state["heavy_cycle"], state["heavy_run_id"]), (100,))),
                                 kwargs={"enabled": lambda state: state.get("heavy_enabled", False)}, daemon=True)]
    try:
        for thread in threads:
            thread.start()
        while not STOP.wait(1):
            pass
    except StopRequested:
        LOG.info("Stop requested")
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        STOP.set()
        deadline = time.monotonic() + 10
        for thread in threads:
            if thread.is_alive():
                thread.join(timeout=max(0, deadline - time.monotonic()))


if __name__ == "__main__":
    main()
