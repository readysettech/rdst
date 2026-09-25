"""Small repeatable MySQL workload for direct monitor parity checks."""

from __future__ import annotations

import argparse
import os
import random
import signal
import threading
import time
from pathlib import Path

import pymysql


def connect():
    return pymysql.connect(host="127.0.0.1", port=13306, user="root",
                           password=os.environ["RDST_MYSQL_SOAK_PASSWORD"], database="shopdb",
                           autocommit=True, connect_timeout=5, read_timeout=90)


def seed() -> None:
    with connect() as conn, conn.cursor() as cursor:
        for statement in Path(__file__).with_name("schema.sql").read_text(encoding="ascii").split(";"):
            if statement.strip():
                cursor.execute(statement)
        cursor.execute("SELECT COUNT(*) FROM customers")
        if cursor.fetchone()[0] == 0:
            cursor.executemany("INSERT INTO customers (country) VALUES (%s)",
                               [(country,) for country in ("US", "CA", "GB") for _ in range(100)])
        cursor.execute("SELECT COUNT(*) FROM orders")
        if cursor.fetchone()[0] == 0:
            cursor.executemany("INSERT INTO orders (customer_id,total,status) VALUES (%s,%s,%s)",
                               [(i % 300 + 1, i % 1000, ("paid", "pending")[i % 2])
                                for i in range(6000)])


def run(stop: threading.Event, *, workers: int = 3) -> list[threading.Thread]:
    def worker(number: int) -> None:
        rng = random.Random(number)
        with connect() as conn, conn.cursor() as cursor:
            while not stop.is_set():
                low = rng.randrange(100, 900, 10)
                cursor.execute("SELECT id,total FROM orders WHERE total >= %s ORDER BY total LIMIT 10",
                               (low,))
                cursor.fetchall()
                cursor.execute("SELECT count(*) FROM orders WHERE status = %s",
                               (("paid", "pending")[number % 2],))
                cursor.fetchone()
                stop.wait(0.15)

    threads = [threading.Thread(target=worker, args=(number,), daemon=True)
               for number in range(workers)]
    for thread in threads:
        thread.start()
    return threads


def run_stuck() -> None:
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute("SELECT SLEEP(75)")
        cursor.fetchone()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=float, default=180)
    args = parser.parse_args()
    if args.duration <= 0:
        parser.error("duration must be positive")
    seed()
    stop = threading.Event()
    threads = run(stop)

    def request_stop(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        time.sleep(args.duration)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=5)


if __name__ == "__main__":
    main()
