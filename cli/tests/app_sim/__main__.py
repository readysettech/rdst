"""CLI: python -m tests.app_sim up|seed|run|down|status|report [--engine pg|mysql|both]."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import time
from typing import Any

from tests.app_sim import docker, report, schema, traffic


def _engines(name: str) -> list[docker.Engine]:
    return [docker.PG, docker.MYSQL] if name == "both" else [docker.ENGINES[name]]


def _parallel(engines: list[docker.Engine], fn: Any) -> None:
    errors: list[str] = []

    def body(engine: docker.Engine) -> None:
        try:
            fn(engine)
        except Exception as exc:
            errors.append(f"{engine.container}: {exc}")

    threads = [threading.Thread(target=body, args=(engine,)) for engine in engines]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    if errors:
        raise SystemExit("\n".join(errors))


def cmd_up(args: argparse.Namespace) -> None:
    def up(engine: docker.Engine) -> None:
        docker.start(engine)
        docker.wait_ready(engine)
        docker.provision(engine)
        print(f"{engine.container} ready on 127.0.0.1:{engine.port}")

    _parallel(_engines(args.engine), up)


def cmd_seed(args: argparse.Namespace) -> None:
    free = docker.free_gb()
    if free < docker.MIN_FREE_GB:
        raise SystemExit(f"Only {free:.1f} GB free; refusing to seed (need {docker.MIN_FREE_GB}).")

    def seed(engine: docker.Engine) -> None:
        started = time.monotonic()
        conn = docker.connect(engine, autocommit=True)
        total = schema.seed(conn, engine.name, scale=args.scale, seed_value=args.seed)
        conn.close()
        docker.provision(engine)
        docker.reset_stats(engine)
        print(f"{engine.container}: {total} rows in {time.monotonic() - started:.0f}s, "
              f"{docker.db_size_mb(engine):.0f} MB")

    _parallel(_engines(args.engine), seed)
    print(f"Free disk: {docker.free_gb():.1f} GB")


def cmd_run(args: argparse.Namespace) -> None:
    drivers = []
    for engine in _engines(args.engine):
        if args.reset:
            docker.reset_stats(engine)
        drivers.append(traffic.Driver(
            engine.name, lambda engine=engine: docker.connect(engine), rps=args.rps,
            threads=args.threads, seed=args.seed, scale=args.scale, jobs=not args.no_jobs))
    summaries = traffic.run(drivers, args.duration)
    print(json.dumps(summaries, indent=2, default=str))


def cmd_down(args: argparse.Namespace) -> None:
    for engine in _engines(args.engine):
        docker.remove(engine)
        print(f"Removed {engine.container} and {engine.volume}")


def cmd_status(args: argparse.Namespace) -> None:
    for engine in _engines(args.engine):
        info = docker.connection_info(engine)
        info["state"] = docker.state(engine)
        if info["state"] == "running":
            try:
                info["size_mb"] = round(docker.db_size_mb(engine), 1)
            except Exception as exc:
                info["size_mb"] = f"unavailable: {exc}"
        print(json.dumps(info, indent=2))
    print(f"Free disk: {docker.free_gb():.1f} GB")


def cmd_report(args: argparse.Namespace) -> None:
    for engine in _engines(args.engine):
        print(report.render(engine, report.collect(engine, args.top)))


def cmd_score(args: argparse.Namespace) -> None:
    """Score a monitoring rdst's plan and actions (RDST_DATA_DIR) against the expected set."""
    from shared.monitor.monitor_store import MonitorStore
    from tests.app_sim.score import score

    store = MonitorStore()
    failed = 0
    for engine in (item.name for item in _engines(args.engine)):
        target = args.pg_target if engine == "postgresql" else args.mysql_target
        plan = store.index_plan(target)
        if plan is None:
            print(f"{target}: no index plan yet")
            failed += 1
            continue
        lines = score(engine, plan, store.actions(target, ("open",)))
        print(f"{target}: {sum(ok for _, ok, _ in lines)}/{len(lines)}")
        for plant, ok, detail in lines:
            print(f"  {'PASS' if ok else 'FAIL'} {plant}: {detail}")
        failed += sum(not ok for _, ok, _ in lines)
    sys.exit(1 if failed else 0)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m tests.app_sim")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("up", "seed", "run", "down", "status", "report", "score"):
        p = sub.add_parser(name)
        p.add_argument("--engine", choices=("pg", "postgresql", "mysql", "both"), default="both")
        if name in ("seed", "run"):
            p.add_argument("--scale", type=float, default=1.0)
            p.add_argument("--seed", type=int, default=7 if name == "seed" else 20261007)
        if name == "run":
            p.add_argument("--duration", type=float, default=300.0, help="seconds")
            p.add_argument("--rps", type=float, default=12.0, help="requests/s per engine")
            p.add_argument("--threads", type=int, default=6)
            p.add_argument("--no-jobs", action="store_true")
            p.add_argument("--reset", action="store_true", help="zero stats counters first")
        if name == "report":
            p.add_argument("--top", type=int, default=15)
        if name == "score":
            p.add_argument("--pg-target", default="appsim-pg")
            p.add_argument("--mysql-target", default="appsim-mysql")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    {"up": cmd_up, "seed": cmd_seed, "run": cmd_run, "down": cmd_down,
     "status": cmd_status, "report": cmd_report, "score": cmd_score}[args.command](args)


if __name__ == "__main__":
    main(sys.argv[1:])
