"""Repeatable three-hour incident schedule; SIGTERM restores local DB state."""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import signal
import threading
import time
import uuid
from contextlib import closing
from dataclasses import dataclass

import psycopg2

from shared.db_connection import cancel_postgres_connection

from common import BASELINE, INDEX, STUCK_SQL, connection_args, ledger_event, write_control

LOG = logging.getLogger(__name__)

class StopRequested(BaseException):
    """Unwind a sleeping or busy runner so its finally block restores state."""


def request_stop(_signum, _frame) -> None:
    raise StopRequested


@dataclass(frozen=True)
class Step:
    minute: int
    kind: str
    phase: str


def table_slowdown_minutes(judge_interval_s: int) -> int:
    """Leave a full collected five-minute window for any judge phase."""
    if judge_interval_s < 1:
        raise ValueError("judge interval must be positive")
    return (judge_interval_s + 300 + 60 + 59) // 60


def schedule(cycle_minutes: int = 180, judge_interval_s: int = 300) -> tuple[Step, ...]:
    """Fixed order preserves a baseline and leaves time for each recovery."""
    table_end = 160 + table_slowdown_minutes(judge_interval_s)
    if cycle_minutes < 180 or table_end >= cycle_minutes:
        raise ValueError("cycle is too short for the judge interval")
    return tuple(Step(minute, kind, phase) for minute, kind, phase in (
        (25, "index_regression", "start"), (45, "index_regression", "end"),
        (50, "new_heavy_query", "start"), (70, "new_heavy_query", "end"),
        (75, "traffic_spike", "start"), (95, "traffic_spike", "end"),
        (100, "query_stopped_running", "start"), (145, "query_stopped_running", "end"),
        (150, "stuck_query", "start"), (153, "stuck_query", "end"),
        (160, "table_slowdown", "start"), (table_end, "table_slowdown", "end")))


PHASE_MINUTES = {"index_regression": 20, "new_heavy_query": 20, "traffic_spike": 20,
                 "query_stopped_running": 45, "stuck_query": 3}
WARMUP_MINUTES = 25
MIN_GAP_MINUTES = 5


def jittered_schedule(seed: str, judge_interval_s: int = 300) -> tuple[tuple[Step, ...], int]:
    """One cycle with the incidents in a random order and random gaps, and its length.

    RDST learns bursts that repeat on a steady period or at the same time of day as routine;
    a soak on a fixed 3-hour clock would teach it its own incidents. The order, the gaps
    (5 to 45 minutes) and a 0 to 90 minute rest before the next cycle come from `seed`, so a
    run is reproducible from its run ID and cycle.
    """
    rng = random.Random(seed)
    phases = {**PHASE_MINUTES, "table_slowdown": table_slowdown_minutes(judge_interval_s)}
    order = list(phases)
    rng.shuffle(order)
    steps = []
    minute = WARMUP_MINUTES + rng.randint(0, 30)
    for kind in order:
        steps += [Step(minute, kind, "start"), Step(minute + phases[kind], kind, "end")]
        minute += phases[kind] + MIN_GAP_MINUTES + rng.randint(0, 40)
    return tuple(steps), minute + rng.randint(0, 90)


class Runner:
    def __init__(self, cycle_minutes: int = 180, judge_interval_s: int = 300):
        self.cycle_minutes = cycle_minutes
        self.judge_interval_s = judge_interval_s
        self.active: set[str] = set()
        self.stuck_connection = None
        self.stuck_thread = None
        self.index_existed: bool | None = None
        self.run_id = uuid.uuid4().hex

    def ddl(self, sql: str) -> None:
        with closing(psycopg2.connect(**connection_args(superuser=True))) as connection:
            connection.autocommit = True
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '5s'")
                cursor.execute("SET statement_timeout = '10s'")
                cursor.execute(sql)

    def prepare(self) -> None:
        if BASELINE.exists():
            self.index_existed = bool(json.loads(BASELINE.read_text(encoding="ascii"))["index_existed"])
        else:
            with closing(psycopg2.connect(**connection_args(superuser=True))) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1 FROM pg_indexes WHERE schemaname = 'public' AND indexname = %s", (INDEX,))
                    self.index_existed = cursor.fetchone() is not None
            BASELINE.parent.mkdir(parents=True, exist_ok=True)
            temp = BASELINE.with_suffix(f".{os.getpid()}.tmp")
            temp.write_text(json.dumps({"index_existed": self.index_existed}) + "\n", encoding="ascii")
            os.replace(temp, BASELINE)
        self.ddl(f"CREATE INDEX IF NOT EXISTS {INDEX} ON public.orders (total)")
        write_control(rate_multiplier=1.0, heavy_enabled=False,
                      steady_enabled=True)

    def run_stuck(self) -> None:
        try:
            connection = psycopg2.connect(**connection_args())
            self.stuck_connection = connection
            with connection.cursor() as cursor:
                cursor.execute(STUCK_SQL)
                cursor.fetchall()
        except psycopg2.Error as exc:
            LOG.warning("stuck query ended: %s", exc)
        finally:
            if self.stuck_connection is not None:
                self.stuck_connection.close()
                self.stuck_connection = None

    def apply(self, step: Step, cycle: int) -> None:
        kind, phase = step.kind, step.phase
        if kind in ("index_regression", "table_slowdown"):
            self.ddl((f"DROP INDEX IF EXISTS public.{INDEX}" if phase == "start" else
                      f"CREATE INDEX IF NOT EXISTS {INDEX} ON public.orders (total)"))
        elif kind == "stuck_query":
            if phase == "start":
                self.stuck_thread = threading.Thread(target=self.run_stuck, daemon=True)
                self.stuck_thread.start()
            elif self.stuck_connection is not None:
                cancel_postgres_connection(self.stuck_connection)
        else:
            controls = {
                "new_heavy_query": {"heavy_enabled": phase == "start",
                                    "heavy_cycle": cycle, "heavy_run_id": self.run_id},
                "traffic_spike": {"rate_multiplier": 4.0 if phase == "start" else 1.0},
                "query_stopped_running": {"steady_enabled": phase == "end"},
            }
            write_control(**controls[kind])
        if phase == "start":
            self.active.add(kind)
        else:
            self.active.discard(kind)
        event = ledger_event(cycle, kind, phase, run_id=self.run_id,
                             index_name=INDEX if kind == "index_regression" else None)
        LOG.info("%s", event)

    def restore(self, cycle: int) -> None:
        for kind in list(self.active):
            try:
                self.apply(Step(0, kind, "end"), cycle)
            except Exception:
                LOG.exception("Failed to end %s", kind)
        write_control(rate_multiplier=1.0, heavy_enabled=False,
                      steady_enabled=True)
        try:
            original = self.index_existed
            if original is None and BASELINE.exists():
                original = bool(json.loads(BASELINE.read_text(encoding="ascii"))["index_existed"])
            if original is not None:
                self.ddl(f"CREATE INDEX IF NOT EXISTS {INDEX} ON public.orders (total)")
                if not original:
                    self.ddl(f"DROP INDEX IF EXISTS public.{INDEX}")
                BASELINE.unlink(missing_ok=True)
        except Exception:
            LOG.exception("Failed to restore original index state")
        if self.stuck_thread is not None:
            self.stuck_thread.join(timeout=5)

    def run(self) -> None:
        cycle = 0
        try:
            self.prepare()
            while True:
                cycle += 1
                started = time.monotonic()
                steps, length = jittered_schedule(f"{self.run_id}:{cycle}",
                                                  self.judge_interval_s)
                for step in steps:
                    time.sleep(max(0, started + step.minute * 60 - time.monotonic()))
                    self.apply(step, cycle)
                time.sleep(max(0, started + max(length, self.cycle_minutes) * 60 -
                               time.monotonic()))
        except StopRequested:
            LOG.info("Stop requested; restoring database baseline")
        finally:
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            self.restore(cycle)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycle-minutes", type=int, default=180)
    parser.add_argument("--judge-interval-s", type=int, default=int(
        os.environ.get("RDST_MONITOR_JUDGE_INTERVAL_S", "300")))
    args = parser.parse_args()
    try:
        schedule(args.cycle_minutes, args.judge_interval_s)
    except ValueError as exc:
        parser.error(str(exc))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    Runner(args.cycle_minutes, args.judge_interval_s).run()


if __name__ == "__main__":
    main()
