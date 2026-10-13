"""A database that accepts connections but stops answering fails every RDST read in bounded
time: monitor collection, the Settings connection test, fleet checks and introspection."""

from __future__ import annotations

import asyncio
import socket
import struct
import threading
import time
from types import SimpleNamespace

import psycopg2
import pytest

from features.configure import service as configure_module
from features.configure.service import ConfigureService
from features.monitor.connection import connection_problem, reason_text
from features.query_registry import scheduler as scheduler_module
from features.query_registry.discovery import QueryDiscoveryCollector
from features.query_registry.scheduler import ObservationScheduler
from shared import db_connection
from shared.db_connection import (
    IoDeadline,
    ServerNotAnsweringError,
    create_mysql_connection_from_params,
)
from shared.query_registry.query_registry import QueryRegistry


def _message(kind: bytes, payload: bytes) -> bytes:
    return kind + struct.pack("!I", len(payload) + 4) + payload


def _serve_frozen_postgres(conn: socket.socket, backend_key: tuple = (1, 2)) -> None:
    """Complete the startup exchange, then read every query and never answer. Cancel requests
    get no answer either, like a paused postmaster's."""

    def read_exact(size: int) -> bytes:
        data = b""
        while len(data) < size:
            chunk = conn.recv(size - len(data))
            if not chunk:
                raise EOFError
            data += chunk
        return data

    try:
        while True:
            length = struct.unpack("!I", read_exact(4))[0]
            code = struct.unpack("!I", read_exact(length - 4)[:4])[0]
            if code == 80877102:  # CancelRequest
                while conn.recv(65536):
                    pass
                return
            if code not in (80877103, 80877104):  # SSLRequest, GSSENCRequest
                break
            conn.sendall(b"N")
        reply = _message(b"R", struct.pack("!I", 0))
        for key, value in (("client_encoding", "UTF8"), ("DateStyle", "ISO, MDY"),
                           ("server_version", "16.0"), ("integer_datetimes", "on"),
                           ("standard_conforming_strings", "on")):
            reply += _message(b"S", f"{key}\0{value}\0".encode())
        conn.sendall(reply + _message(b"K", struct.pack("!II", *backend_key))
                     + _message(b"Z", b"I"))
        while conn.recv(65536):
            pass
    except (EOFError, OSError):
        pass
    finally:
        conn.close()


def _listen(handler):
    listener = socket.create_server(("127.0.0.1", 0))

    def serve() -> None:
        while True:
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            threading.Thread(target=handler, args=(conn,), daemon=True).start()

    threading.Thread(target=serve, daemon=True).start()
    return listener


@pytest.fixture
def frozen_postgres():
    listener = _listen(_serve_frozen_postgres)
    yield listener.getsockname()[1]
    listener.close()


@pytest.fixture
def silent_server():
    """Accepts TCP connections and never sends a byte, like a paused MySQL server."""
    accepted = []
    listener = _listen(accepted.append)
    yield listener.getsockname()[1]
    listener.close()
    for conn in accepted:
        conn.close()


def _pg_params(port: int) -> dict:
    return {"engine": "postgresql", "host": "127.0.0.1", "port": port, "user": "app",
            "password": "secret", "database": "app", "sslmode": "disable",
            "application_name": "rdst/test"}


def _connect_frozen(port: int):
    return psycopg2.connect(**db_connection.postgres_connection_kwargs(_pg_params(port)))


def test_io_deadline_aborts_a_blocked_postgres_query(frozen_postgres):
    connection = _connect_frozen(frozen_postgres)
    started = time.monotonic()
    with pytest.raises(ServerNotAnsweringError, match="didn't answer within 0.2 s"):
        with IoDeadline(connection, 0.2), connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    assert time.monotonic() - started < 5
    connection.close()


def test_io_deadline_renew_extends_and_exit_disarms(frozen_postgres):
    connection = _connect_frozen(frozen_postgres)
    with IoDeadline(connection, 0.2) as deadline:
        time.sleep(0.15)
        deadline.renew()
        time.sleep(0.15)
    time.sleep(0.3)
    assert not deadline.expired
    assert connection.fileno() >= 0
    connection.close()


def test_io_deadline_exit_waits_for_an_abort_in_progress(monkeypatch):
    aborting, release, exited = threading.Event(), threading.Event(), threading.Event()

    def slow_abort(connection):
        aborting.set()
        release.wait(5)

    monkeypatch.setattr(db_connection, "abort_connection", slow_abort)
    deadline = IoDeadline(object(), 0.01)
    deadline.__enter__()
    assert aborting.wait(5)

    def leave():
        with pytest.raises(ServerNotAnsweringError):
            deadline.__exit__(None, None, None)
        exited.set()

    leaver = threading.Thread(target=leave)
    leaver.start()
    # The caller closes the connection once the block exits, so exit waits for the abort.
    assert not exited.wait(0.2)
    release.set()
    leaver.join(5)
    assert exited.is_set()


def test_mysql_connections_take_read_and_write_bounds_from_the_caller(monkeypatch):
    import pymysql

    calls = []
    monkeypatch.setattr(pymysql, "connect", lambda **kwargs: calls.append(kwargs) or object())
    params = {"host": "db", "port": 3306, "user": "app", "password": "", "database": "app"}
    create_mysql_connection_from_params(params)
    create_mysql_connection_from_params(params, read_timeout=5, write_timeout=6)
    # Long workloads (rdst query run, benchmarks) keep running; bounded callers pass theirs.
    assert "read_timeout" not in calls[0] and "write_timeout" not in calls[0]
    assert (calls[1]["read_timeout"], calls[1]["write_timeout"]) == (5, 6)


def test_mysql_greeting_from_a_silent_server_times_out(silent_server):
    params = {"host": "127.0.0.1", "port": silent_server, "user": "app", "password": "",
              "database": "app"}
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="timed out"):
        create_mysql_connection_from_params(params, read_timeout=0.2)
    assert time.monotonic() - started < 5


def test_no_answer_has_its_own_connection_problem():
    problem = connection_problem("The database didn't answer within 120 s.")
    assert problem["reason"] == "The database stopped answering."
    assert reason_text("The database didn't answer within 120 s.") == problem["reason"]


@pytest.mark.asyncio
async def test_scheduler_aborts_a_collection_the_database_never_answers(
    tmp_path, frozen_postgres, monkeypatch
):
    monkeypatch.setattr(scheduler_module, "COLLECTION_DEADLINE_SECONDS", 0.3)

    def forbidden_service():
        raise AssertionError("legacy path must stay unused")

    collector = QueryDiscoveryCollector(
        "demo", service_factory=forbidden_service,
        registry_factory=lambda: QueryRegistry(registry_path=str(tmp_path / "queries.toml")),
        two_phase=True,
        connection_factory=lambda target: (_connect_frozen(frozen_postgres), "postgresql"),
    )
    coordinator = SimpleNamespace(collector_for=lambda name: collector,
                                  has_subscribers=lambda name: False)
    scheduler = ObservationScheduler(coordinator, clock=lambda: 100.0,
                                     targets_provider=lambda: ["demo"],
                                     monitored_provider=lambda: set())
    outcomes = []
    scheduler.set_outcome_listener(lambda *outcome: outcomes.append(outcome))

    result = await asyncio.wait_for(scheduler.check_now("demo"), 10)

    assert result == {"ok": False, "detail": "The database didn't answer within 0.3 s."}
    assert outcomes == [("demo", False, 100, "The database didn't answer within 0.3 s.")]
    assert collector._connection is None
    assert scheduler._schedules["demo"].task is None
    assert scheduler._schedules["demo"].consecutive_failures == 1


@pytest.mark.asyncio
async def test_unanswered_read_is_reported_before_the_collection_deadline(
    tmp_path, frozen_postgres, monkeypatch
):
    monkeypatch.setattr(scheduler_module, "COLLECTION_DEADLINE_SECONDS", 3)
    monkeypatch.setattr(scheduler_module, "NOT_ANSWERING_SECONDS", 0.3)
    monkeypatch.setattr(scheduler_module, "TICK_SECONDS", 0.05)
    # The liveness probe opens its own connection to the frozen server and gets no answer.
    monkeypatch.setattr(scheduler_module, "PROBE_SECONDS", 0.2)
    collector = QueryDiscoveryCollector(
        "demo", service_factory=lambda: None,
        registry_factory=lambda: QueryRegistry(registry_path=str(tmp_path / "queries.toml")),
        two_phase=True,
        connection_factory=lambda target: (_connect_frozen(frozen_postgres), "postgresql"),
    )
    coordinator = SimpleNamespace(collector_for=lambda name: collector,
                                  has_subscribers=lambda name: False)
    scheduler = ObservationScheduler(coordinator, targets_provider=lambda: ["demo"],
                                     monitored_provider=lambda: set())
    outcomes = []
    scheduler.set_outcome_listener(lambda *outcome: outcomes.append(outcome))

    started = time.time()
    result = await asyncio.wait_for(scheduler.check_now("demo"), 10)

    assert result == {"ok": False, "detail": "The database didn't answer within 3 s."}
    assert [(ok, error) for _, ok, _, error in outcomes] == [
        (False, "The database didn't answer within 0.3 s."),
        (False, "The database didn't answer within 3 s.")]
    # The early report is dated from when the read went unanswered.
    assert started - 1 <= outcomes[0][2] <= outcomes[1][2]
    assert collector.unanswered_for() is None
    assert scheduler._schedules["demo"].consecutive_failures == 1


def _serve_answering_postgres(conn: socket.socket) -> None:
    """Complete the startup exchange, then answer every query: SELECTs with one row."""
    try:
        with conn.makefile("rb") as stream:
            while True:
                length = struct.unpack("!I", stream.read(4))[0]
                if struct.unpack("!I", stream.read(length - 4)[:4])[0] != 80877103:  # SSLRequest
                    break
                conn.sendall(b"N")
            conn.sendall(_message(b"R", struct.pack("!I", 0)) +
                         _message(b"S", b"server_version\x0016.0\x00") +
                         _message(b"S", b"client_encoding\x00UTF8\x00") +
                         _message(b"K", struct.pack("!II", 1, 2)) + _message(b"Z", b"I"))
            while (kind := stream.read(1)) == b"Q":
                query = stream.read(struct.unpack("!I", stream.read(4))[0] - 4)
                if query.lstrip().upper().startswith(b"SELECT"):
                    field = b"?column?\x00" + struct.pack("!IhIhih", 0, 0, 23, 4, -1, 0)
                    reply = (_message(b"T", struct.pack("!h", 1) + field) +
                             _message(b"D", struct.pack("!hi", 1, 1) + b"1") +
                             _message(b"C", b"SELECT 1\x00"))
                else:
                    reply = _message(b"C", query.split()[0].rstrip(b"\x00;") + b"\x00")
                conn.sendall(reply + _message(b"Z", b"T"))
    except (struct.error, OSError):
        pass
    finally:
        conn.close()


@pytest.fixture
def answering_postgres():
    listener = _listen(_serve_answering_postgres)
    yield listener.getsockname()[1]
    listener.close()


@pytest.mark.asyncio
async def test_slow_read_on_a_database_that_answers_a_probe_is_not_reported(
    tmp_path, frozen_postgres, answering_postgres, monkeypatch
):
    monkeypatch.setattr(scheduler_module, "COLLECTION_DEADLINE_SECONDS", 1.5)
    monkeypatch.setattr(scheduler_module, "NOT_ANSWERING_SECONDS", 0.2)
    monkeypatch.setattr(scheduler_module, "TICK_SECONDS", 0.05)
    monkeypatch.setattr(scheduler_module, "PROBE_SECONDS", 0.5)
    monkeypatch.setattr(scheduler_module, "PROBE_INTERVAL_SECONDS", 0.4)
    opened = []

    def connect(target):
        # The collection's read stays unanswered; every probe opens a connection that answers.
        port = answering_postgres if opened else frozen_postgres
        opened.append(_connect_frozen(port))
        return opened[-1], "postgresql"

    collector = QueryDiscoveryCollector(
        "demo", service_factory=lambda: None,
        registry_factory=lambda: QueryRegistry(registry_path=str(tmp_path / "queries.toml")),
        two_phase=True, connection_factory=connect,
    )
    coordinator = SimpleNamespace(collector_for=lambda name: collector,
                                  has_subscribers=lambda name: False)
    scheduler = ObservationScheduler(coordinator, targets_provider=lambda: ["demo"],
                                     monitored_provider=lambda: set())
    outcomes = []
    scheduler.set_outcome_listener(lambda *outcome: outcomes.append(outcome))

    result = await asyncio.wait_for(scheduler.check_now("demo"), 10)

    # Only the collection's own deadline reports; each probe found the server answering.
    assert result == {"ok": False, "detail": "The database didn't answer within 1.5 s."}
    assert [(ok, error) for _, ok, _, error in outcomes] == [
        (False, "The database didn't answer within 1.5 s.")]
    assert len(opened) >= 3
    assert all(connection.closed for connection in opened[1:])


@pytest.mark.asyncio
async def test_failed_reconnect_reports_the_driver_error(tmp_path):
    def connect(target):
        raise psycopg2.OperationalError('connection to server at "db" failed: timeout expired')

    collector = QueryDiscoveryCollector(
        "demo", service_factory=lambda: None,
        registry_factory=lambda: QueryRegistry(registry_path=str(tmp_path / "queries.toml")),
        two_phase=True, connection_factory=connect,
    )
    coordinator = SimpleNamespace(collector_for=lambda name: collector,
                                  has_subscribers=lambda name: False)
    scheduler = ObservationScheduler(coordinator, targets_provider=lambda: ["demo"],
                                     monitored_provider=lambda: set())
    outcomes = []
    scheduler.set_outcome_listener(lambda *outcome: outcomes.append(outcome))
    await asyncio.wait_for(scheduler.check_now("demo"), 10)
    assert connection_problem(outcomes[0][3])["reason"] == "The connection timed out."


@pytest.mark.asyncio
async def test_check_now_returns_when_aborting_cannot_unblock_the_collection(monkeypatch):
    monkeypatch.setattr(scheduler_module, "COLLECTION_DEADLINE_SECONDS", 0.05)
    monkeypatch.setattr(scheduler_module, "ABORT_GRACE_SECONDS", 0.05)
    release = threading.Event()
    aborted = []
    collector = SimpleNamespace(
        collect_now=lambda: asyncio.to_thread(release.wait, 5),
        abort_connection=lambda: aborted.append(True),
    )
    coordinator = SimpleNamespace(collector_for=lambda name: collector,
                                  has_subscribers=lambda name: False)
    scheduler = ObservationScheduler(coordinator, clock=lambda: 100.0,
                                     targets_provider=lambda: ["demo"],
                                     monitored_provider=lambda: set())
    try:
        result = await asyncio.wait_for(scheduler.check_now("demo"), 5)
        assert result["ok"] is False and "didn't answer" in result["detail"]
        assert aborted == [True]
        assert scheduler._schedules["demo"].task is None
    finally:
        release.set()


def test_parameter_sampling_gives_up_on_a_frozen_database_and_reconnects(
    tmp_path, frozen_postgres, monkeypatch
):
    from features.monitor import param_sampler
    from features.query_registry import discovery
    from shared.monitor.monitor_store import MonitorStore

    monkeypatch.setattr(param_sampler, "READ_DEADLINE_S", 0.3)
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: 1000)
    store.set_settings("demo", {"enabled": True})
    sampler = param_sampler.ParamSampler(store, clock=lambda: 1000, interval_s=3)
    monkeypatch.setattr(sampler, "wanted_keys", lambda target: {"k"})
    monkeypatch.setattr(discovery.query_discovery, "collector_for", lambda target: SimpleNamespace(
        _alias_map={"10:20:111:t": "k"}, _engine="postgresql"))
    opened = []

    def connect(target):
        opened.append(_connect_frozen(frozen_postgres))
        return opened[-1]

    monkeypatch.setattr(sampler, "_connection",
                        lambda target: sampler._connections.get(target) or
                        sampler._connections.setdefault(target, connect(target)))

    started = time.monotonic()
    assert sampler.tick() is False
    assert time.monotonic() - started < 5
    assert "didn't answer within 0.3 s" in sampler._last_error["demo"]
    assert "demo" not in sampler._connections and opened[0].closed
    sampler._next_poll.clear()
    sampler.backoff.forget("demo")
    sampler.tick()
    assert len(opened) == 2
    sampler._close_all()


@pytest.mark.asyncio
async def test_connection_test_runs_off_the_event_loop(monkeypatch):
    seen = []
    monkeypatch.setattr(ConfigureService, "_connection_test",
                        lambda self, config, *, force_fresh_tunnel=False:
                        seen.append(threading.get_ident()) or {"success": True})
    assert await ConfigureService().perform_connection_test({}) == {"success": True}
    assert seen and seen[0] != threading.get_ident()


def test_connection_test_coroutine_advanced_without_a_loop_runs_inline(monkeypatch):
    monkeypatch.setattr(ConfigureService, "_connection_test",
                        lambda self, config, *, force_fresh_tunnel=False: {"success": True})
    test = ConfigureService().perform_connection_test({})
    with pytest.raises(StopIteration) as complete:
        test.send(None)
    assert complete.value.value == {"success": True}


@pytest.mark.asyncio
async def test_postgres_connection_test_fails_when_the_database_never_answers(
    frozen_postgres, monkeypatch
):
    monkeypatch.setattr(configure_module, "CONNECTION_TEST_TIMEOUT_SECONDS", 0.3)
    params = _pg_params(frozen_postgres)
    monkeypatch.setattr(configure_module, "resolve_connection_params",
                        lambda **kwargs: dict(params))
    result = await asyncio.wait_for(ConfigureService().perform_connection_test(params), 10)
    assert result["success"] is False
    assert "accepted the connection but didn't answer within 0.3 s" in result["message"]


@pytest.mark.asyncio
async def test_mysql_connection_test_fails_when_the_database_never_answers(
    silent_server, monkeypatch
):
    monkeypatch.setattr(configure_module, "CONNECTION_TEST_TIMEOUT_SECONDS", 0.3)
    params = {"engine": "mysql", "host": "127.0.0.1", "port": silent_server, "user": "app",
              "password": "secret", "database": "app"}
    monkeypatch.setattr(configure_module, "resolve_connection_params",
                        lambda **kwargs: dict(params))
    result = await asyncio.wait_for(ConfigureService().perform_connection_test(params), 10)
    assert result["success"] is False
    assert "accepted the connection but didn't answer within 0.3 s" in result["message"]


def test_fleet_connectivity_check_fails_when_the_database_never_answers(
    frozen_postgres, monkeypatch
):
    from features.fleet import service as fleet_module

    monkeypatch.setattr(configure_module, "CONNECTION_TEST_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(fleet_module, "create_direct_connection",
                        lambda *args, **kwargs: _connect_frozen(frozen_postgres))
    started = time.monotonic()
    with pytest.raises(ServerNotAnsweringError):
        fleet_module.FleetService(config=SimpleNamespace())._check_connection(
            "demo", {"engine": "postgresql"})
    assert time.monotonic() - started < 5


def test_monitor_index_scan_fails_when_the_database_never_answers(frozen_postgres, monkeypatch):
    from features.monitor import loop

    monkeypatch.setattr(loop, "READ_DEADLINE_S", 0.3)
    monkeypatch.setattr(loop, "_default_connection_factory",
                        lambda target: (_connect_frozen(frozen_postgres), "postgresql"))
    started = time.monotonic()
    with pytest.raises(Exception):
        loop._scan_indexes("demo")
    assert time.monotonic() - started < 5


def test_mysql_introspection_bounds_socket_reads(monkeypatch):
    from features.schema.semantic_layer import introspector

    seen = {}

    def connect(params, **kwargs):
        seen.update(kwargs)
        raise RuntimeError("stop")

    monkeypatch.setattr(introspector, "create_mysql_connection_from_params", connect)
    instance = introspector.SchemaIntrospector({"engine": "mysql"})
    monkeypatch.setattr(instance, "_get_connection_params", lambda target: {
        "host": "db", "user": "app", "database": "app", "password": "secret"})
    with pytest.raises(RuntimeError, match="stop"):
        instance._introspect_mysql("demo", 10, False)
    assert seen == {"read_timeout": introspector.IO_TIMEOUT_SECONDS,
                    "write_timeout": introspector.IO_TIMEOUT_SECONDS}


def test_postgres_introspection_fails_when_the_database_never_answers(
    frozen_postgres, monkeypatch
):
    from features.schema.semantic_layer import introspector

    monkeypatch.setattr(introspector, "IO_TIMEOUT_SECONDS", 0.3)
    instance = introspector.SchemaIntrospector({"engine": "postgresql"})
    monkeypatch.setattr(instance, "_get_connection_params",
                        lambda target: _pg_params(frozen_postgres))
    started = time.monotonic()
    with pytest.raises(ServerNotAnsweringError):
        instance._introspect_postgres("demo", 10, False)
    assert time.monotonic() - started < 5


_CANCEL_CHILD = """
import json, sys, threading, time
import psycopg2
from shared import db_connection

params = {"engine": "postgresql", "host": "127.0.0.1", "port": int(sys.argv[1]), "user": "app",
          "password": "secret", "database": "app", "sslmode": "disable",
          "application_name": "rdst/test"}
db_connection.POSTGRES_CANCEL_SECONDS = 0.3
conn = psycopg2.connect(**db_connection.postgres_connection_kwargs(params))
statement_ended = threading.Event()

def run_statement():
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT pg_sleep(60)")
    except Exception:
        pass
    statement_ended.set()

threading.Thread(target=run_statement, daemon=True).start()
time.sleep(0.2)
beats, done = [0], threading.Event()

def heartbeat():
    while not done.is_set():
        beats[0] += 1
        time.sleep(0.01)

threading.Thread(target=heartbeat, daemon=True).start()
started = time.monotonic()
sent = db_connection.cancel_query(conn, "postgresql")
elapsed = time.monotonic() - started
done.set()
print(json.dumps({"libpq": db_connection._libpq() is not None, "sent": sent,
                  "elapsed": elapsed, "beats": beats[0],
                  "statement_ended": statement_ended.wait(5)}))
"""


def test_postgres_cancel_against_a_paused_server_is_bounded_and_keeps_the_process_live(
    frozen_postgres
):
    """The fake postmaster ignores cancel requests, so psycopg2's conn.cancel() would block
    forever while holding the GIL; the child runs in its own process so a freeze fails the
    test by timeout instead of hanging the suite."""
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-c", _CANCEL_CHILD, str(frozen_postgres)],
        cwd=root, env={**os.environ, "PYTHONPATH": str(root)}, capture_output=True,
        text=True, stdin=subprocess.DEVNULL, timeout=30)
    assert result.returncode == 0, result.stderr
    outcome = json.loads(result.stdout.strip().splitlines()[-1])
    # The protocol cancel itself waited on the server, not a fallback.
    assert outcome["libpq"] is True
    assert outcome["sent"] is False
    assert outcome["elapsed"] < 5
    # Other threads kept running while the cancel waited on the server.
    assert outcome["beats"] >= 5
    # The unanswered cancel fell back to aborting the connection, so the statement stopped too.
    assert outcome["statement_ended"] is True


def test_postgres_cancel_sends_the_connections_backend_key(monkeypatch):
    """A postmaster that takes cancel requests receives libpq's CancelRequest packet carrying
    the key from the connection's own startup, and the running statement isn't aborted."""
    received = []
    backend_key = (4242, 0x5EC12E7)

    def serve(conn: socket.socket) -> None:
        header = conn.recv(8, socket.MSG_PEEK)
        if len(header) == 8 and struct.unpack("!I", header[4:])[0] == 80877102:
            packet = b""
            while len(packet) < 16 and (chunk := conn.recv(16 - len(packet))):
                packet += chunk
            received.append(struct.unpack("!IIII", packet))
            conn.close()
            return
        _serve_frozen_postgres(conn, backend_key)

    listener = _listen(serve)
    aborted = []
    real_abort = db_connection.abort_connection
    monkeypatch.setattr(db_connection, "abort_connection", aborted.append)
    connection = _connect_frozen(listener.getsockname()[1])
    statement_ended = threading.Event()

    def run_statement() -> None:
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_sleep(60)")
        except Exception:
            pass
        statement_ended.set()

    try:
        assert connection.get_backend_pid() == backend_key[0]
        threading.Thread(target=run_statement, daemon=True).start()
        time.sleep(0.2)
        assert db_connection.cancel_postgres_connection(connection, seconds=5) is True
        assert received == [(16, 80877102, *backend_key)]
        assert aborted == []
        assert not statement_ended.is_set()
    finally:
        real_abort(connection)
        assert statement_ended.wait(5)
        connection.close()
        listener.close()


class _FakeLibpq:
    """Stands in for libpq's cancel calls; PQcancel returns ``result`` once ``release`` is set."""

    def __init__(self, result: int) -> None:
        self.result = result
        self.release = threading.Event()
        self.release.set()
        self.freed = []

    def PQgetCancel(self, pointer):
        return pointer + 1

    def PQcancel(self, handle, errbuf, size):
        self.release.wait(5)
        return self.result

    def PQfreeCancel(self, handle):
        self.freed.append(handle)


class _FakePgConn:
    closed = 0
    pgconn_ptr = 100


@pytest.fixture
def aborts(monkeypatch):
    aborted = []
    monkeypatch.setattr(db_connection, "abort_connection", aborted.append)
    return aborted


def test_postgres_cancel_aborts_when_libpq_is_unavailable(monkeypatch, aborts):
    monkeypatch.setattr(db_connection, "_libpq", lambda: None)
    conn = _FakePgConn()
    assert db_connection.cancel_postgres_connection(conn) is False
    assert aborts == [conn]


def test_postgres_cancel_aborts_when_the_server_refuses_the_request(monkeypatch, aborts):
    lib = _FakeLibpq(result=0)
    monkeypatch.setattr(db_connection, "_libpq", lambda: lib)
    conn = _FakePgConn()
    assert db_connection.cancel_postgres_connection(conn) is False
    assert aborts == [conn]
    assert lib.freed == [101]


def test_postgres_cancel_taken_by_the_server_keeps_the_connection(monkeypatch, aborts):
    lib = _FakeLibpq(result=1)
    monkeypatch.setattr(db_connection, "_libpq", lambda: lib)
    assert db_connection.cancel_postgres_connection(_FakePgConn()) is True
    assert aborts == []
    assert lib.freed == [101]


def test_postgres_cancel_timeout_aborts_and_frees_only_after_libpq_returns(monkeypatch, aborts):
    lib = _FakeLibpq(result=1)
    lib.release.clear()
    monkeypatch.setattr(db_connection, "_libpq", lambda: lib)
    conn = _FakePgConn()
    started = time.monotonic()
    assert db_connection.cancel_postgres_connection(conn, seconds=0.1) is False
    assert time.monotonic() - started < 2
    assert aborts == [conn]
    # PQcancel is still running, so its PGcancel stays allocated.
    assert lib.freed == []
    lib.release.set()
    deadline = time.monotonic() + 5
    while not lib.freed and time.monotonic() < deadline:
        time.sleep(0.01)
    assert lib.freed == [101]


def test_postgres_cancel_request_needs_an_open_connection(monkeypatch):
    monkeypatch.setattr(db_connection, "_libpq", lambda: _FakeLibpq(result=1))
    closed = _FakePgConn()
    closed.closed = 1
    assert db_connection.PostgresCancelRequest.for_connection(closed) is None
    assert db_connection.PostgresCancelRequest.for_connection(object()) is None


def test_registered_cancel_builds_under_the_lock_and_aborts_only_listed_connections(
        monkeypatch, aborts):
    lock = threading.Lock()
    lib = _FakeLibpq(result=1)
    lib.release.clear()
    built_under_lock, sending = [], threading.Event()
    get_cancel, cancel = lib.PQgetCancel, lib.PQcancel
    lib.PQgetCancel = lambda pointer: built_under_lock.append(lock.locked()) or get_cancel(pointer)
    lib.PQcancel = lambda *args: sending.set() or cancel(*args)
    monkeypatch.setattr(db_connection, "_libpq", lambda: lib)
    closed, listed = _FakePgConn(), _FakePgConn()
    registered = [(closed, "postgresql", None), (listed, "postgresql", None)]

    def owner_closes_while_the_cancel_is_unanswered():
        sending.wait(5)
        with lock:
            registered.pop(0)
        closed.closed = 1

    owner = threading.Thread(target=owner_closes_while_the_cancel_is_unanswered)
    owner.start()
    try:
        assert db_connection.cancel_registered(lock, lambda: registered, seconds=1) == 0
        owner.join(5)
        assert built_under_lock == [True, True]
        assert aborts == [listed]
    finally:
        lib.release.set()


def test_libpq_matches_the_version_psycopg2_runs():
    from psycopg2 import extensions

    lib = db_connection._libpq()
    assert lib is not None
    assert lib.PQlibVersion() == extensions.libpq_version()
