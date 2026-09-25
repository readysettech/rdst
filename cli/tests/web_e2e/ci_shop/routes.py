"""Test hooks for the live Continuous Monitoring E2E: the ci-shop database, its load, the proxy
RDST connects through, the backdated history and on-demand monitor passes.

The server runs one engine, set by RDST_CIM_ENGINE (postgresql or mysql). The database admin
account comes from RDST_CIM_DB_HOST/PORT/ADMIN_USER/ADMIN_PASSWORD; RDST itself connects as
RDST_CIM_DB_USER with the password in RDST_CIM_DB_PASSWORD, through the proxy.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import sqlite3
import threading
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from shared.monitor.monitor_store import MonitorStore
from tests.web_e2e.ci_shop import catalogue, history, schema
from tests.web_e2e.ci_shop.load import LoadDriver
from tests.web_e2e.ci_shop.proxy import CuttableProxy

router = APIRouter(prefix="/__test/ci-shop/{engine}")

DATABASE = "ci_shop"
PASSWORD_ENV = "RDST_CIM_DB_PASSWORD"
_state: dict[str, Any] = {}
logger = logging.getLogger(__name__)


def _engine(engine: str) -> str:
    configured = os.environ.get("RDST_CIM_ENGINE", "postgresql")
    if engine != configured:
        raise HTTPException(404, f"This server runs the {configured} ci-shop")
    return engine


def _settings() -> dict[str, Any]:
    engine = os.environ.get("RDST_CIM_ENGINE", "postgresql")
    mysql = engine == "mysql"
    return {
        "engine": engine,
        "host": os.environ.get("RDST_CIM_DB_HOST", "127.0.0.1"),
        "port": int(os.environ.get("RDST_CIM_DB_PORT", "13306" if mysql else "15432")),
        "admin_user": os.environ.get("RDST_CIM_ADMIN_USER", "root" if mysql else "rdst_test"),
        "admin_password": os.environ.get("RDST_CIM_ADMIN_PASSWORD", "rdst_e2e_password"),
        "user": os.environ.get("RDST_CIM_DB_USER", "rdst_cim" if mysql else "rdst_test"),
        "password": os.environ.get(PASSWORD_ENV, "rdst_e2e_password"),
    }


def _connect(database: str | None = None, *, admin: bool = True) -> Any:
    settings = _settings()
    user = settings["admin_user"] if admin else settings["user"]
    password = settings["admin_password"] if admin else settings["password"]
    if settings["engine"] == "mysql":
        import pymysql

        return pymysql.connect(host=settings["host"], port=settings["port"], user=user,
                               password=password, database=database, autocommit=True,
                               connect_timeout=10)
    import psycopg2

    connection = psycopg2.connect(host=settings["host"], port=settings["port"], user=user,
                                  password=password, dbname=database or "postgres",
                                  connect_timeout=10)
    connection.autocommit = True
    return connection


def _execute(connection: Any, *statements: str) -> None:
    with connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)
            if cursor.description:
                cursor.fetchall()


def _admin_when_ready(timeout_s: float = 90) -> Any:
    """The admin connection, waiting out a server that is still initializing."""
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            return _connect()
        except Exception:
            if time.monotonic() > deadline:
                raise
            time.sleep(1)


def _create_database() -> int:
    settings = _settings()
    admin = _admin_when_ready()
    try:
        if settings["engine"] == "mysql":
            user, password = settings["user"], settings["password"]
            _execute(admin,
                     f"DROP DATABASE IF EXISTS {DATABASE}",
                     f"CREATE DATABASE {DATABASE}",
                     f"CREATE USER IF NOT EXISTS '{user}'@'%' IDENTIFIED BY '{password}'",
                     f"GRANT ALL ON {DATABASE}.* TO '{user}'@'%'",
                     f"GRANT SELECT ON performance_schema.* TO '{user}'@'%'",
                     f"GRANT PROCESS ON *.* TO '{user}'@'%'",
                     # The statement history consumers carry real parameter values.
                     "UPDATE performance_schema.setup_consumers SET ENABLED='YES' "
                     "WHERE NAME LIKE 'events_statements_%' OR NAME IN "
                     "('global_instrumentation','thread_instrumentation','statements_digest')",
                     "TRUNCATE TABLE performance_schema.events_statements_summary_by_digest")
        else:
            _execute(admin, f"DROP DATABASE IF EXISTS {DATABASE} WITH (FORCE)",
                     f"CREATE DATABASE {DATABASE}")
    finally:
        admin.close()
    connection = _connect(DATABASE)
    try:
        if settings["engine"] == "postgresql":
            _execute(connection, "CREATE EXTENSION IF NOT EXISTS pg_stat_statements")
        rows = schema.seed(connection, settings["engine"])
        history.MEASURED_MS.clear()
        history.MEASURED_MS.update(_measure_unseen(connection, settings["engine"]))
        if settings["engine"] == "postgresql":
            _execute(connection, "SELECT pg_stat_statements_reset()")
        else:
            history.DIGEST_TEXT.clear()
            history.DIGEST_TEXT.update(_digest_texts(connection))
            _execute(connection,
                     "TRUNCATE TABLE performance_schema.events_statements_summary_by_digest")
    finally:
        connection.close()
    return rows


def _measure_unseen(connection: Any, engine: str, runs: int = 10) -> dict[str, float]:
    """The burst-only query's mean latency, read from the engine's statistics before they are
    reset, so its backdated history has this engine's cost as the live shapes' history does."""
    shape = catalogue.BURST_ONLY
    with connection.cursor() as cursor:
        for _ in range(runs):
            cursor.execute(shape.sql)
            cursor.fetchall()
        if engine == "mysql":
            cursor.execute("SELECT AVG_TIMER_WAIT / 1e9 FROM performance_schema."
                           "events_statements_summary_by_digest WHERE SCHEMA_NAME = %s "
                           "AND DIGEST = STATEMENT_DIGEST(%s)", (DATABASE, shape.sql))
        else:
            cursor.execute("SELECT mean_exec_time FROM pg_stat_statements s JOIN pg_database d "
                           "ON d.oid = s.dbid WHERE d.datname = current_database() "
                           "AND s.query = %s", (shape.sql,))
        row = cursor.fetchone()
    return {shape.name: float(row[0])} if row and row[0] else {}


def _digest_texts(connection: Any) -> dict[str, str]:
    """Each shape's MySQL DIGEST_TEXT, computed by the server without running the statement."""
    rng = random.Random(7)
    found = {}
    with connection.cursor() as cursor:
        for shape in catalogue.ALL:
            cursor.execute("SELECT STATEMENT_DIGEST_TEXT(%s)", (catalogue.render(shape, rng),))
            found[shape.name] = cursor.fetchone()[0]
    return found


def _teardown() -> None:
    for name in ("locks", "spill"):
        sessions = _state.pop(name, None)
        if sessions is not None:
            sessions.close()
    driver, proxy = _state.pop("driver", None), _state.pop("proxy", None)
    if driver is not None:
        driver.stop()
    if proxy is not None:
        proxy.close()


@router.post("/setup")
def setup(engine: str):
    """A fresh seeded ci_shop database, steady load with cheap-query noise, and the proxy."""
    _engine(engine)
    _teardown()
    started = time.monotonic()
    rows = _create_database()
    seeded_s = round(time.monotonic() - started, 2)
    settings = _settings()
    proxy = CuttableProxy(settings["host"], settings["port"])
    proxy.start()
    driver = LoadDriver(lambda: _connect(DATABASE))
    driver.set_phase("noise", True)
    driver.start()
    _state.update(driver=driver, proxy=proxy, engine=engine)
    return {"engine": engine, "host": proxy.listen_host, "port": proxy.port,
            "database": DATABASE, "user": settings["user"], "password_env": PASSWORD_ENV,
            "rows": rows, "seed_s": seeded_s, "shapes": len(catalogue.LIVE)}


def _driver() -> LoadDriver:
    if "driver" not in _state:
        raise HTTPException(409, "ci-shop is not set up")
    return _state["driver"]


def _plan_nodes(engine: str) -> list[str]:
    connection = _connect(DATABASE)
    try:
        with connection.cursor() as cursor:
            shape = catalogue.BY_NAME[catalogue.REGRESSION]
            if engine == "mysql":
                cursor.execute("EXPLAIN FORMAT=JSON " + shape.sql, (1,))
                return [json.dumps(json.loads(cursor.fetchone()[0]))]
            cursor.execute("EXPLAIN (FORMAT JSON) " + shape.sql, (1,))
            plan = cursor.fetchone()[0][0]["Plan"]

            def nodes(node: dict[str, Any]) -> list[str]:
                return [node["Node Type"], *[name for child in node.get("Plans", [])
                                             for name in nodes(child)]]
            return nodes(plan)
    finally:
        connection.close()


@router.post("/phase/{name}")
def phase(engine: str, name: str, body: dict):
    """Switch a load phase: burst, noise, new_heavy, or regression (drops the index)."""
    _engine(engine)
    on = bool(body.get("on"))
    if name == "regression":
        index, table, column = schema.REGRESSION_INDEX
        connection = _connect(DATABASE)
        try:
            if on:
                _execute(connection, schema.drop_index_sql(engine, index, table))
            else:
                _execute(connection, schema.create_index_sql(index, table, column),
                         f"ANALYZE TABLE {table}" if engine == "mysql" else f"ANALYZE {table}")
        finally:
            connection.close()
        return {"regression": on, "plan": _plan_nodes(engine)}
    try:
        return {"phases": _driver().set_phase(name, on)}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/proxy/{state}")
def proxy(engine: str, state: str):
    """Cut or restore RDST's route to the database."""
    _engine(engine)
    current = _state.get("proxy")
    if current is None:
        raise HTTPException(409, "ci-shop is not set up")
    if state == "cut":
        current.cut()
    elif state == "open":
        current.start()
    else:
        raise HTTPException(422, "state is cut or open")
    return {"open": current.open, "cuts": current.cut_count, "at": int(time.time())}


@router.post("/history/{target}")
async def inject_history(engine: str, target: str, body: dict, request: Request):
    """Two days of backdated history, the replayed past bursts and yesterday's nightly report."""
    _engine(engine)
    written = await history.inject(target, analysis_cap=int(body.get("analysis_cap", 10)))
    # Shortlist rows the running loop held for its next AI check were measured before the
    # history existed; the next check measures again.
    loop = getattr(request.app.state, "monitor_loop", None)
    if loop is not None:
        loop._pending.pop(target, None)
    return written


@router.post("/catalogue/{target}")
def shapes(engine: str, target: str):
    _engine(engine)
    return {"shapes": list(history.catalogue_keys(MonitorStore(), target).values())}


async def _read(store: MonitorStore, sql: str, params: tuple) -> list[dict[str, Any]]:
    """A store read that waits out a writer holding the file lock."""
    for attempt in range(20):
        try:
            return await asyncio.to_thread(store.read, sql, params)
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc) or attempt == 19:
                raise
            await asyncio.sleep(0.5)
    return []


@router.post("/judge/{target}")
async def judge(engine: str, target: str, request: Request):
    """Make the running monitor loop's next pass an AI check and return that pass."""
    _engine(engine)
    loop = getattr(request.app.state, "monitor_loop", None)
    if loop is None:
        raise HTTPException(409, "The monitor loop isn't running")
    store = MonitorStore()
    before = (await _read(store, "SELECT COALESCE(MAX(id),0) id FROM judge_run WHERE target=?",
                          (target,)))[0]["id"]
    loop._last_judged.pop(target, None)
    deadline = time.time() + 120
    while time.time() < deadline:
        rows = await _read(store, "SELECT id,ts,outcome,request_json,response_json FROM "
                           "judge_run WHERE target=? AND id>? ORDER BY id LIMIT 1",
                           (target, before))
        if rows:
            row = rows[0]
            response = json.loads(row["response_json"] or "{}")
            request_body = json.loads(row["request_json"] or "{}")
            return {"id": row["id"], "ts": row["ts"], "outcome": row["outcome"],
                    "gate": request_body.get("gate") or response.get("gate"),
                    "shortlist": [query.get("query_key") for query in
                                  request_body.get("queries", [])],
                    "event_scope": response.get("event_scope"),
                    "queries": request_body.get("queries", []),
                    "verdict": [{key: answer.get(key) for key in
                                 ("query_key", "kind", "attention", "severity")}
                                for answer in response.get("queries", [])]}
        await asyncio.sleep(0.5)
    raise HTTPException(504, "No monitor pass within two minutes")


@router.post("/item-events/{item_id}")
def item_events(engine: str, item_id: int):
    """An inbox item's event log, notifications included."""
    _engine(engine)
    return {"events": MonitorStore().read(
        "SELECT ts,kind,detail_json FROM inbox_event WHERE item_id=? ORDER BY id", (item_id,))}


@router.post("/stats")
def stats(engine: str):
    _engine(engine)
    proxy_state = _state.get("proxy")
    return {"load": _driver().stats(),
            "proxy": {"open": proxy_state.open if proxy_state else None,
                      "port": proxy_state.port if proxy_state else None}}


@router.delete("")
def teardown(engine: str):
    _engine(engine)
    _teardown()
    return {"stopped": True}


class _Sessions:
    """Background demo sessions owned by the test server's lifespan."""

    def __init__(self, engine: str):
        self.engine = engine
        self.stop = threading.Event()
        self.threads: list[threading.Thread] = []
        self.ids: list[int] = []
        self.lock = threading.Lock()
        self.errors: list[str] = []

    def start(self, work) -> None:
        def run():
            connection = None
            session_id = None
            try:
                connection = _connect(DATABASE)
                with connection.cursor() as cursor:
                    cursor.execute("SELECT CONNECTION_ID()" if self.engine == "mysql" else
                                   "SELECT pg_backend_pid()")
                    session_id = int(cursor.fetchone()[0])
                with self.lock:
                    self.ids.append(session_id)
                if not self.stop.is_set():
                    work(connection)
            except Exception as exc:
                if not self.stop.is_set():
                    with self.lock:
                        self.errors.append(str(exc))
                    logger.exception("ci-shop demo session failed")
            finally:
                if connection is not None:
                    connection.close()
                with self.lock:
                    if session_id in self.ids:
                        self.ids.remove(session_id)

        thread = threading.Thread(target=run, daemon=True)
        self.threads.append(thread)
        thread.start()

    def close(self) -> None:
        self.stop.set()
        for thread in self.threads:
            thread.join(timeout=1)
        with self.lock:
            ids = list(self.ids)
        if ids:
            admin = _connect(DATABASE)
            try:
                for session_id in ids:
                    try:
                        _execute(admin, f"KILL CONNECTION {session_id}" if self.engine == "mysql"
                                 else f"SELECT pg_terminate_backend({session_id})")
                    except Exception:
                        logger.debug("Demo session already closed", exc_info=True)
            finally:
                admin.close()
        for thread in self.threads:
            thread.join(timeout=5)
        if any(thread.is_alive() for thread in self.threads):
            raise RuntimeError("ci-shop demo sessions didn't stop")


@router.post("/locks/{target}")
def locks(engine: str, target: str, body: dict):
    _engine(engine)
    release_locks(engine, target)
    waiters = max(1, min(int(body.get("waiters", 4)), 16))
    connection = _connect(DATABASE)
    try:
        _execute(connection, "CREATE TABLE IF NOT EXISTS cim_lock_demo (id INTEGER PRIMARY KEY)",
                 "INSERT IGNORE INTO cim_lock_demo VALUES (1)" if engine == "mysql" else
                 "INSERT INTO cim_lock_demo VALUES (1) ON CONFLICT DO NOTHING")
    finally:
        connection.close()
    sessions = _Sessions(engine)
    _state["locks"] = sessions
    acquired = threading.Event()

    def holder(connection):
        _execute(connection, "BEGIN", "SELECT * FROM cim_lock_demo WHERE id=1 FOR UPDATE"
                 if engine == "mysql" else "LOCK TABLE cim_lock_demo IN ACCESS EXCLUSIVE MODE")
        acquired.set()
        sessions.stop.wait()
        connection.rollback()

    def waiter(connection):
        if engine == "mysql":
            _execute(connection, "SET SESSION innodb_lock_wait_timeout=600", "BEGIN",
                     "SELECT * FROM cim_lock_demo WHERE id=1 FOR UPDATE")
        else:
            _execute(connection, "BEGIN", "SELECT * FROM cim_lock_demo")
        connection.rollback()

    sessions.start(holder)
    if not acquired.wait(timeout=15):
        sessions.close()
        raise HTTPException(500, f"Lock holder failed: {sessions.errors}")
    for _ in range(waiters):
        sessions.start(waiter)
    return {"waiters": waiters}


@router.delete("/locks/{target}")
def release_locks(engine: str, target: str):
    _engine(engine)
    sessions = _state.pop("locks", None)
    if sessions is not None:
        sessions.close()
    return {"released": True}


@router.post("/spill/{target}")
def spill(engine: str, target: str, body: dict):
    _engine(engine)
    previous = _state.pop("spill", None)
    if previous is not None:
        previous.close()
    seconds = max(1, min(int(body.get("seconds", 150)), 300))
    sessions = _Sessions(engine)
    _state["spill"] = sessions
    sql = ("SELECT payload, COUNT(*) FROM cim_spill_demo GROUP BY payload ORDER BY payload"
           if engine == "mysql" else "SELECT payload FROM cim_spill_demo ORDER BY payload")

    def run(connection):
        _execute(connection, "DROP TABLE IF EXISTS cim_spill_demo",
                 "CREATE TABLE cim_spill_demo (id INTEGER PRIMARY KEY, payload TEXT)")
        with connection.cursor() as cursor:
            if engine == "mysql":
                cursor.executemany("INSERT INTO cim_spill_demo VALUES (%s,%s)",
                                   [(i, f"{i % 997:04d}" + "x" * 500) for i in range(40000)])
                _execute(connection, "SET SESSION tmp_table_size=16384",
                         "SET SESSION max_heap_table_size=16384",
                         "SET SESSION internal_tmp_mem_storage_engine=MEMORY")
            else:
                _execute(connection, "INSERT INTO cim_spill_demo SELECT i, md5(i::text) || "
                         "repeat('x',500) FROM generate_series(1,40000) i",
                         "SET work_mem='64kB'", "SET max_parallel_workers_per_gather=0")
        deadline = time.monotonic() + seconds
        while not sessions.stop.is_set() and time.monotonic() < deadline:
            _execute(connection, sql)
            sessions.stop.wait(0.05)

    sessions.start(run)
    return {"sql": sql, "seconds": seconds}


@router.post("/bloat/{target}")
def bloat(engine: str, target: str):
    _engine(engine)
    if engine != "postgresql":
        raise HTTPException(422, "Bloat demo requires PostgreSQL")
    connection = _connect(DATABASE)
    try:
        _execute(connection, "DROP TABLE IF EXISTS cim_bloat_demo",
                 "CREATE TABLE cim_bloat_demo (id INTEGER, payload TEXT) "
                 "WITH (autovacuum_enabled=false)",
                 "INSERT INTO cim_bloat_demo SELECT i, repeat('x',500) "
                 "FROM generate_series(1,100000) i",
                 "DELETE FROM cim_bloat_demo WHERE id > 1000", "ANALYZE cim_bloat_demo",
                 "SELECT pg_stat_force_next_flush()")
    finally:
        connection.close()
    return {"table": "cim_bloat_demo"}


@router.post("/report/nightly/{target}")
async def nightly(engine: str, target: str):
    from features.monitor.report import run_report
    from shared.monitor.monitor_store import local_day_bounds

    _engine(engine)
    store = MonitorStore()
    start, end = local_day_bounds(int(time.time()))
    await asyncio.to_thread(store._write, lambda conn: conn.execute(
        "DELETE FROM report_run WHERE target=? AND ts>=? AND ts<?", (target, start, end)))
    report = await run_report(target, "nightly", store=store)
    return {"id": report.get("id"), "state": report["state"]}
