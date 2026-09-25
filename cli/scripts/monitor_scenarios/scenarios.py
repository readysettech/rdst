"""Drive the localdev Postgres through named monitor scenarios with real load.

Each scenario prints what the Database page and Inbox should show and when, then
(with --check) polls the dev backend until it does. Scenarios use their own schema,
monitor_scenarios, so they never touch the tables or the index the soak drives, and
they can run while the soak runs. Every step is idempotent; SIGTERM or Ctrl-C drops the
scenario index and stops the load. `cleanup` drops the schema and the scenario mute rules.

    spike            steady load, then 4x traffic: a traffic_spike Inbox item, no push
    regression       an indexed lookup loses its index: a latency notification, then a
                     green "Back to normal" once the index is back
    index_added      a slow lookup gets an index: a neutral chart marker, no Inbox item
    muted            a muted query spikes: filed under Muted, no notification

Run from rdst/ against the dev backend (docs/plans/continuous_analysis/run_dev_server.sh):

    RDST_DATA_DIR=~/.rdst-monitor-dev uv run python scripts/monitor_scenarios/scenarios.py \\
        run spike --check

Index changes are timeline facts. Items open and close on measured latency only, so the
regression scenario checks the item, never an index verdict.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "monitor_soak"))
from common import connection_args  # noqa: E402  (shared soak connection settings)

LOG = logging.getLogger("monitor_scenarios")
SCHEMA = "monitor_scenarios"
TABLE = f"{SCHEMA}.scn_events"
ROWS = 400_000
REF_INDEX = "idx_scn_events_ref"
NOTE = "monitor scenario"
MINUTE = 60


class StopRequested(BaseException):
    """Unwind sleeps so the finally block restores the database."""


def request_stop(_signum, _frame) -> None:
    raise StopRequested


@dataclass(frozen=True)
class Query:
    name: str
    sql: str
    params: Callable[[int], tuple]
    marker: str  # text that identifies the query in the monitor's normalized SQL


LOOKUP = Query("lookup", f"SELECT id, amount FROM {TABLE} WHERE ref = %s",
               lambda n: (f"r{(n * 7919) % ROWS}",), "scn_events WHERE ref")
TRAFFIC = Query("traffic", f"SELECT amount FROM {TABLE} WHERE id = %s",
                lambda n: ((n * 104729) % ROWS + 1,), "scn_events WHERE id")


@dataclass(frozen=True)
class Step:
    at_minute: int
    action: str
    detail: str
    expect: str = ""


@dataclass
class Scenario:
    name: str
    summary: str
    steps: tuple[Step, ...]
    queries: dict[str, float]  # query name -> calls per second at rate 1
    check: str


def scenarios(judge_minutes: int) -> dict[str, Scenario]:
    """Timelines leave a full baseline hour and one judge interval plus a five-minute
    series window for each change to reach the attention check."""
    notice = judge_minutes + 6
    return {
        "spike": Scenario(
            "spike", "Steady traffic for an hour, then four times the rate.",
            (Step(0, "load", "Steady traffic on the scenario lookup by id"),
             Step(66, "rate:4", "Traffic x4",
                  f"Within {notice} min: Inbox 'Query traffic increased' for the id "
                  "lookup (watch band, no push), an inbox marker on its chart"),
             Step(66 + notice + 4, "rate:1", "Traffic back to normal",
                  "The item closes itself after three calm checks and shows green in "
                  "Recovered")),
            {"traffic": 4.0}, "spike"),
        "regression": Scenario(
            "regression", "An indexed lookup loses its index for a while.",
            (Step(0, f"create:{REF_INDEX}:ref", "Lookup by ref with its index",
                  "A fast lookup builds an hour of baseline"),
             Step(66, f"drop:{REF_INDEX}", f"DROP INDEX {REF_INDEX}",
                  f"Within {notice} min: Inbox 'Query latency increased' (a push), "
                  "an 'idx_scn_events_ref dropped' marker with no claim about the cause"),
             Step(66 + notice + 4, f"create:{REF_INDEX}:ref", f"CREATE INDEX {REF_INDEX}",
                  "Three checks back in band close the item: green 'Back to normal'")),
            {"lookup": 2.0}, "regression"),
        "index_added": Scenario(
            "index_added", "A slow lookup gets an index.",
            (Step(0, "load", "Slow lookup by ref (sequential scan on 400k rows)",
                  "The lookup leads database time on the Database page"),
             Step(10, f"create:{REF_INDEX}:ref", f"CREATE INDEX {REF_INDEX}",
                  "Marker 'idx_scn_events_ref added' on the chart. Getting faster is "
                  "digest-only: no Inbox item, no verdict")),
            {"lookup": 2.0}, "index_added"),
        "muted": Scenario(
            "muted", "The spike scenario on a query muted at query scope.",
            (Step(0, "load", "Steady traffic on the scenario lookup by id"),
             Step(1, "mute", "Mute rule for the lookup (query scope, this target)",
                  "Inbox > Muted lists the rule"),
             Step(66, "rate:4", "Traffic x4",
                  f"Within {notice} min: the item is filed under Muted with muted_by set; "
                  "no notification, the unread badge does not change"),
             Step(66 + notice + 4, "rate:1", "Traffic back to normal")),
            {"traffic": 4.0}, "muted"),
    }


# Database setup, owned entirely by the scenario schema.

def ddl(*statements: str) -> None:
    with closing(psycopg2.connect(**connection_args(superuser=True))) as connection:
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute("SET lock_timeout = '5s'")
            for statement in statements:
                cursor.execute(statement)


def prepare() -> None:
    ddl(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}",
        f"CREATE TABLE IF NOT EXISTS {TABLE} (id integer PRIMARY KEY, ref text NOT NULL, "
        "kind text NOT NULL, amount numeric(10,2) NOT NULL)",
        f"INSERT INTO {TABLE} SELECT g, 'r' || g, (ARRAY['a','b','c'])[1 + g % 3], "
        f"(g % 1000) / 10.0 FROM generate_series(1, {ROWS}) g "
        f"WHERE NOT EXISTS (SELECT 1 FROM {TABLE} LIMIT 1)",
        f"ANALYZE {TABLE}",
        f"GRANT USAGE ON SCHEMA {SCHEMA} TO rdst",
        f"GRANT SELECT ON {TABLE} TO rdst")


def drop_indexes() -> None:
    ddl(f"DROP INDEX IF EXISTS {SCHEMA}.{REF_INDEX}")


# Load

@dataclass
class Load:
    rates: dict[str, float]
    multiplier: float = 1.0
    stop: threading.Event = field(default_factory=threading.Event)
    threads: list[threading.Thread] = field(default_factory=list)

    def start(self) -> None:
        for name, rate in self.rates.items():
            query = {q.name: q for q in (LOOKUP, TRAFFIC)}[name]
            thread = threading.Thread(target=self._run, args=(query, rate), daemon=True)
            thread.start()
            self.threads.append(thread)

    def _run(self, query: Query, rate: float) -> None:
        connection, count = None, 0
        while not self.stop.is_set():
            try:
                if connection is None or connection.closed:
                    connection = psycopg2.connect(**{**connection_args(),
                                                     "application_name": "rdst-monitor-scenario"})
                    connection.autocommit = True
                with connection.cursor() as cursor:
                    cursor.execute(query.sql, query.params(count))
                    cursor.fetchall()
                count += 1
            except psycopg2.Error as exc:
                LOG.warning("%s: %s", query.name, exc)
                if connection is not None:
                    connection.close()
                connection = None
                self.stop.wait(3)
            self.stop.wait(1 / max(0.05, rate * self.multiplier))
        if connection is not None:
            connection.close()

    def close(self) -> None:
        self.stop.set()
        for thread in self.threads:
            thread.join(timeout=10)


# Backend

class Api:
    def __init__(self, base: str, target: str):
        self.base, self.target = base.rstrip("/"), target

    def call(self, method: str, path: str, body: Any = None) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(f"{self.base}/api/monitor{path}", data=data,
                                         method=method,
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read() or b"null")

    def target_path(self, rest: str) -> str:
        return f"/targets/{urllib.parse.quote(self.target)}{rest}"

    def query_key(self, query: Query, timeout_s: int = 600) -> str:
        """The monitor key for a scenario query, once a collector pass has seen it."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            page = self.call("GET", self.target_path("/queries?range=1h&limit=500"))
            for row in page["queries"]:
                if query.marker in " ".join(row["text"].split()):
                    return row["key"]
            time.sleep(15)
        raise TimeoutError(f"the monitor never listed the {query.name} query")


LATENCY_TYPES = {"latency_regression", "plan_change", "table_slowdown", "many_queries_changed"}


def expectation_met(kind: str, api: Api, key: str, started: int) -> bool:
    if kind in ("spike", "muted"):
        state = "muted" if kind == "muted" else "open"
        items = api.call("GET", f"/inbox?state={state}&target={urllib.parse.quote(api.target)}")
        return any(item["type"] == "traffic_spike" and key in item["query_keys"]
                   and item["created_at"] >= started
                   and (kind != "muted" or item["muted_by"] is not None)
                   for item in items["items"])
    if kind == "regression":
        # Filed on measured latency, then closed on measured latency (green in Open).
        items = api.call("GET", f"/inbox?state=open&target={urllib.parse.quote(api.target)}")
        return any(item["type"] in LATENCY_TYPES and key in item["query_keys"]
                   and item["created_at"] >= started
                   and item["severity_band"] == "recovered"
                   for item in items["items"])
    series = api.call("GET", api.target_path(f"/queries/{key}/series?range=1h"))
    items = api.call("GET", f"/inbox?state=open&target={urllib.parse.quote(api.target)}")
    marker = any(marker["kind"] == "index_added" and marker["label"] == f"{REF_INDEX} added"
                 for marker in series["markers"])
    return (marker and series["fix"] is None and
            not any(key in item["query_keys"] and item["created_at"] >= started
                    for item in items["items"]))


# Runner

def run(scenario: Scenario, api: Api, *, check: bool, speed: float) -> bool:
    started = int(time.time())
    prepare()
    drop_indexes()
    load = Load(scenario.queries)
    key: str | None = None
    origin = time.monotonic()
    try:
        load.start()
        print(f"{scenario.name}: {scenario.summary}")
        for step in scenario.steps:
            time.sleep(max(0.0, origin + step.at_minute * MINUTE / speed - time.monotonic()))
            action, _, argument = step.action.partition(":")
            stamp = time.strftime("%H:%M")
            if action == "mute" and key is None:
                key = api.query_key(TRAFFIC)
            if action == "rate":
                load.multiplier = float(argument)
            elif action == "create":
                name, _, column = argument.partition(":")
                ddl(f"CREATE INDEX IF NOT EXISTS {name} ON {TABLE} ({column})")
            elif action == "drop":
                ddl(f"DROP INDEX IF EXISTS {SCHEMA}.{argument}")
            elif action == "mute":
                api.call("POST", "/mute-rules", {"target": api.target, "query_key": key,
                                                 "type": None, "until": None, "note": NOTE})
            print(f"[{stamp}] {step.detail}")
            if step.expect:
                print(f"         expect: {step.expect}")
        if key is None:
            key = api.query_key(TRAFFIC if scenario.check in ("spike", "muted") else LOOKUP)
        if not check or key is None:
            return True
        deadline = time.monotonic() + 30 * MINUTE / speed
        while time.monotonic() < deadline:
            if expectation_met(scenario.check, api, key, started):
                print(f"[{time.strftime('%H:%M')}] PASS {scenario.name}")
                return True
            time.sleep(30)
        print(f"[{time.strftime('%H:%M')}] FAIL {scenario.name}: expectation not observed")
        return False
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        load.close()
        drop_indexes()


def cleanup(api: Api) -> None:
    drop_indexes()
    ddl(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
    try:
        for rule in api.call("GET", "/mute-rules")["rules"]:
            if rule["note"] == NOTE:
                api.call("DELETE", f"/mute-rules/{rule['id']}")
    except urllib.error.URLError as exc:
        print(f"Mute rules not cleaned (backend unreachable): {exc}")
    print("Scenario schema and mute rules removed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api", default="http://127.0.0.1:8797")
    parser.add_argument("--target", default="localdev")
    parser.add_argument("--judge-minutes", type=int, default=max(1, int(os.environ.get(
        "RDST_MONITOR_JUDGE_INTERVAL_S", "600")) // 60))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="print every scenario's timeline")
    run_parser = commands.add_parser("run", help="run one scenario")
    run_parser.add_argument("scenario", choices=sorted(scenarios(10)))
    run_parser.add_argument("--check", action="store_true",
                            help="poll the backend until the expected state appears")
    run_parser.add_argument("--speed", type=float, default=1.0, help=argparse.SUPPRESS)
    commands.add_parser("cleanup", help="drop the scenario schema and mute rules")
    args = parser.parse_args()
    catalog = scenarios(args.judge_minutes)
    api = Api(args.api, args.target)
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
    if args.command == "list":
        for scenario in catalog.values():
            print(f"{scenario.name}: {scenario.summary}")
            for step in scenario.steps:
                print(f"  +{step.at_minute:>3} min  {step.detail}")
                if step.expect:
                    print(f"            expect: {step.expect}")
        return 0
    if args.command == "cleanup":
        cleanup(api)
        return 0
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        return 0 if run(catalog[args.scenario], api, check=args.check,
                        speed=args.speed) else 1
    except StopRequested:
        print("Stopped; scenario indexes dropped")
        return 130


if __name__ == "__main__":
    sys.exit(main())
