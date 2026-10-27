"""The shop on databases started elsewhere: seed it, then drive it until stopped.

    python -m tests.app_sim.attached seed --postgres URL --mysql URL [--scale 0.1]
    python -m tests.app_sim.attached run --postgres URL --mysql URL [--rps 4]

URLs are postgresql://user:password@host:port/database and mysql://... The shop's tables go into
the named database next to whatever is already there, and the given user owns them. Traffic runs
until the process is stopped, and rides out a database that stops answering for a while.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import time
from typing import Any, Callable
from urllib.parse import unquote, urlsplit

from tests.app_sim import schema, traffic

CONNECT_TIMEOUT_S = 5


def connector(url: str) -> tuple[str, Callable[[], Any]]:
    parts = urlsplit(url)
    params = {"host": parts.hostname, "port": parts.port, "user": unquote(parts.username or ""),
              "password": unquote(parts.password or ""), "database": parts.path.lstrip("/")}
    if parts.scheme in ("postgres", "postgresql"):
        import psycopg2

        def connect_pg() -> Any:
            return psycopg2.connect(host=params["host"], port=params["port"] or 5432,
                                    user=params["user"], password=params["password"],
                                    dbname=params["database"], application_name="appsim-shop",
                                    connect_timeout=CONNECT_TIMEOUT_S)

        return "postgresql", connect_pg
    if parts.scheme == "mysql":
        import pymysql

        def connect_mysql() -> Any:
            return pymysql.connect(host=params["host"], port=params["port"] or 3306,
                                   user=params["user"], password=params["password"],
                                   database=params["database"], program_name="appsim-shop",
                                   connect_timeout=CONNECT_TIMEOUT_S)

        return "mysql", connect_mysql
    raise SystemExit(f"unsupported database URL scheme: {parts.scheme}")


def seed(engine: str, connect: Callable[[], Any], scale: float, seed_value: int) -> int:
    conn = connect()
    conn.autocommit = True
    try:
        return schema.seed(conn, engine, scale=scale, seed_value=seed_value,
                           log=lambda line: None)
    finally:
        conn.close()


def cmd_seed(targets: list[tuple[str, Callable[[], Any]]], args: argparse.Namespace) -> None:
    errors: list[str] = []

    def body(engine: str, connect: Callable[[], Any]) -> None:
        started = time.monotonic()
        try:
            total = seed(engine, connect, args.scale, args.seed)
            print(f"{engine}: {total} rows in {time.monotonic() - started:.0f}s", flush=True)
        except Exception as exc:
            errors.append(f"{engine}: {exc}")

    threads = [threading.Thread(target=body, args=target) for target in targets]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    if errors:
        raise SystemExit("\n".join(errors))


def cmd_run(targets: list[tuple[str, Callable[[], Any]]], args: argparse.Namespace) -> None:
    drivers = [traffic.Driver(engine, connect, rps=args.rps, threads=args.threads,
                              scale=args.scale) for engine, connect in targets]
    summaries = traffic.run(drivers, float("inf"), report_every=60.0,
                            log=lambda line: print(line, flush=True))
    print(json.dumps(summaries, indent=2, default=str), flush=True)


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="python -m tests.app_sim.attached")
    parser.add_argument("command", choices=("seed", "run"))
    parser.add_argument("--postgres", metavar="URL")
    parser.add_argument("--mysql", metavar="URL")
    parser.add_argument("--scale", type=float, default=0.1)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--rps", type=float, default=4.0, help="requests/s per engine")
    parser.add_argument("--threads", type=int, default=3)
    args = parser.parse_args(argv)
    urls = [url for url in (args.postgres, args.mysql) if url]
    if not urls:
        parser.error("name at least one database")
    logging.basicConfig(level=logging.WARNING)
    targets = [connector(url) for url in urls]
    {"seed": cmd_seed, "run": cmd_run}[args.command](targets, args)


if __name__ == "__main__":
    main(sys.argv[1:])
