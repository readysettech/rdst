"""CLI: python -m tests.app_sim.scenarios <scenario> setup|run|status|drop [options]."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
from datetime import datetime
from typing import Any

from tests.app_sim import docker
from tests.app_sim.scenarios import SCENARIOS, base


def _engines(name: str) -> list[docker.Engine]:
    return [docker.PG, docker.MYSQL] if name == "both" else [docker.ENGINES[name]]


def _app(args: argparse.Namespace, engine: docker.Engine, anchor: datetime | None = None) -> Any:
    return SCENARIOS[args.scenario].App(scale=args.scale, anchor=anchor, engine=engine.name)


def cmd_setup(args: argparse.Namespace) -> None:
    free = docker.free_gb()
    if free < docker.MIN_FREE_GB:
        raise SystemExit(f"Only {free:.1f} GB free on /mnt/c; refusing (need "
                         f"{docker.MIN_FREE_GB}).")
    module = SCENARIOS[args.scenario]
    anchor = datetime.now().replace(second=0, microsecond=0) if module.App.live_anchor else None
    errors: list[str] = []

    def body(engine: docker.Engine) -> None:
        try:
            result = base.setup(_app(args, engine, anchor), engine, seed=args.seed)
            print(json.dumps(result), flush=True)
        except Exception as exc:
            errors.append(f"{engine.name}: {exc}")

    threads = [threading.Thread(target=body, args=(e,)) for e in _engines(args.engine)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    print(f"Free disk: {docker.free_gb():.1f} GB")
    if errors:
        raise SystemExit("\n".join(errors))


def cmd_run(args: argparse.Namespace) -> None:
    module = SCENARIOS[args.scenario]
    drivers = []
    for engine in _engines(args.engine):
        app = _app(args, engine)
        if not base.exists(app, engine):
            raise SystemExit(f"{engine.name}: database {app.database} is missing; run setup")
        drivers.append(base.Driver(
            app, engine, rps=args.rps or module.App.default_rps,
            threads=args.threads or module.App.default_threads, seed=args.seed,
            jobs=not args.no_jobs))
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {args.scenario}: "
          f"{', '.join(d.engine.name for d in drivers)} at {drivers[0].rps} req/s each for "
          f"{args.duration:.0f}s", flush=True)
    summaries = base.run(drivers, args.duration, report_every=args.report_every,
                         log=lambda line: print(line, flush=True))
    print(json.dumps(summaries, indent=2, default=str), flush=True)


def render_status(info: dict[str, Any]) -> str:
    lines = [f"== {info['database']} on {info['engine']}: "
             f"{info.get('size_mb', info.get('state'))} MB"]
    if "tables" not in info:
        return lines[0]
    pg = info["engine"] == "postgresql"
    header = ("live seq_scan idx_scan ins upd del" if pg else "fetch ins upd del")
    lines.append(f"  tables ({header}):")
    lines += [f"    {row[0]:<20} " + " ".join(str(v) for v in row[1:]) for row in info["tables"]]
    lines.append("  index scans: " + ", ".join(f"{k}={v}" for k, v in info["index_scans"].items()))
    lines.append("  top statements (calls, total ms, rows examined/returned, text):")
    lines += [f"    {calls:>8} {ms:>10.1f} {rows:>10}  {text}"
              for calls, ms, rows, text in info["top_statements"]]
    return "\n".join(lines)


def cmd_status(args: argparse.Namespace) -> None:
    for engine in _engines(args.engine):
        print(render_status(base.status(_app(args, engine), engine, args.top)))
    print(f"Free disk: {docker.free_gb():.1f} GB")


def cmd_drop(args: argparse.Namespace) -> None:
    for engine in _engines(args.engine):
        app = _app(args, engine)
        base.drop(app, engine)
        print(f"Dropped {app.database} on {engine.container}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m tests.app_sim.scenarios")
    parser.add_argument("scenario", choices=sorted(SCENARIOS))
    parser.add_argument("command", choices=("setup", "run", "status", "drop"))
    parser.add_argument("--engine", choices=("pg", "postgresql", "mysql", "both"),
                        default="both")
    parser.add_argument("--scale", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=None,
                        help="seed RNG (setup default 7, run default 20261007)")
    parser.add_argument("--duration", type=float, default=300.0, help="run seconds")
    parser.add_argument("--rps", type=float, default=None,
                        help="requests/s per engine (default per scenario)")
    parser.add_argument("--threads", type=int, default=None)
    parser.add_argument("--no-jobs", action="store_true")
    parser.add_argument("--report-every", type=float, default=60.0)
    parser.add_argument("--top", type=int, default=15)
    args = parser.parse_args(argv)
    if args.seed is None:
        args.seed = 7 if args.command == "setup" else 20261007
    logging.basicConfig(level=logging.WARNING)
    {"setup": cmd_setup, "run": cmd_run, "status": cmd_status, "drop": cmd_drop}[args.command](
        args)


if __name__ == "__main__":
    main(sys.argv[1:])
