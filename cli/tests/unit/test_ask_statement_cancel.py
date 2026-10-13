"""Ask stops its database statement when the request goes away or stalls.

These tests run Ask's blocking work in real threads: cancellation must reach a
statement whose worker thread outlives the awaiting task.
"""

import asyncio
import socket
import struct
import threading
import time

import pytest

from features.ask.engine.ask3.context import Ask3Context
from features.ask.engine.ask3.phases import execute as execute_phase
from features.ask.engine.ask3.types import DbType, StatementCancel
from features.ask.events import AskErrorEvent
from features.ask.models import AskPhase
from features.ask.service import AskService
from shared import db_connection
from tests.unit.test_frozen_database import _listen, _pg_params, _serve_frozen_postgres

_CANCEL_REQUEST_CODE = 80877102


class _Conn:
    def __init__(self):
        self.cancel_requested = threading.Event()
        self.closed = False

    def cancel(self):
        raise AssertionError("psycopg2 cancel holds the GIL while the server is silent")

    def thread_id(self):
        return 42

    def close(self):
        self.closed = True


@pytest.fixture
def side_cancels(monkeypatch):
    """Server-side cancels and client-side aborts, recorded and released on the fake."""
    sent = []

    class Request:
        def __init__(self, conn):
            self.conn = conn

        def send(self):
            sent.append(("postgres", self.conn))
            return True

    monkeypatch.setattr(
        db_connection.PostgresCancelRequest, "for_connection", staticmethod(Request)
    )
    monkeypatch.setattr(
        db_connection,
        "abort_connection",
        lambda conn: sent.append(("abort", conn)) or conn.cancel_requested.set(),
    )
    return sent


def test_cancel_sends_postgres_a_protocol_cancel(side_cancels):
    handle = StatementCancel()
    conn = _Conn()

    assert handle.register(conn, DbType.POSTGRESQL, "db", {})
    assert handle.cancel() == 1
    assert side_cancels == [("postgres", conn)]
    assert not handle.register(_Conn(), DbType.POSTGRESQL, "db", {})


def test_cancel_kills_a_mysql_query_from_a_side_connection(monkeypatch):
    kills = []
    monkeypatch.setattr(
        "shared.db_connection.cancel_mysql_by_thread_id",
        lambda config, thread_id, target=None: kills.append((config, thread_id, target))
        or True,
    )
    handle = StatementCancel()
    conn = _Conn()
    handle.register(conn, DbType.MYSQL, "shop", {"engine": "mysql"})

    assert handle.cancel() == 1
    assert kills == [({"engine": "mysql"}, 42, "shop")]


def test_finished_statements_are_neither_cancelled_nor_aborted(side_cancels):
    handle = StatementCancel()
    conn = _Conn()
    handle.register(conn, DbType.POSTGRESQL, "db", {})
    handle.unregister(conn)

    assert handle.cancel() == 0
    handle.abort()
    assert side_cancels == []


def test_abort_cuts_live_connections_and_refuses_later_statements(side_cancels):
    handle = StatementCancel()
    conn = _Conn()
    handle.register(conn, DbType.POSTGRESQL, "db", {})

    handle.abort()

    assert side_cancels == [("abort", conn)]
    assert not handle.register(_Conn(), DbType.POSTGRESQL, "db", {})


def test_a_cancelled_request_never_starts_its_statement(monkeypatch):
    import psycopg2

    conn = _Conn()
    monkeypatch.setattr(psycopg2, "connect", lambda **_kwargs: conn)
    monkeypatch.setattr(
        "shared.db_connection.resolve_connection_params",
        lambda **_kwargs: {"host": "h", "user": "u", "database": "d"},
    )
    monkeypatch.setattr(
        "shared.db_connection.postgres_connection_kwargs", lambda params, **kw: {}
    )
    handle = StatementCancel()
    handle.abort()

    result = execute_phase._execute_postgres(
        "SELECT 1", {}, 30, "db", statement_cancel=handle
    )

    assert result["error_kind"] == "cancelled"
    assert conn.closed


def _blocking_execute(started: threading.Event, conn: _Conn):
    def execute(ctx, _presenter, _executor=None):
        ctx.statement_cancel.register(conn, DbType.POSTGRESQL, ctx.target, {})
        started.set()
        conn.cancel_requested.wait(5)
        ctx.statement_cancel.unregister(conn)
        return ctx

    return execute


def _context(timeout_seconds):
    return Ask3Context(
        question="Show user ids",
        target="warehouse",
        target_config={"engine": "postgresql"},
        timeout_seconds=timeout_seconds,
    )


def _patch_until_execute(monkeypatch, execute):
    def generate(ctx, _presenter, _manager):
        ctx.sql = "SELECT id FROM users"
        return ctx

    monkeypatch.setattr("features.ask.service.generate_sql", generate)
    monkeypatch.setattr("features.ask.service.validate_sql", lambda ctx, _p: ctx)
    monkeypatch.setattr("features.ask.service.execute_query", execute)


@pytest.mark.asyncio
async def test_stalled_execution_is_aborted_at_the_client_deadline(monkeypatch, side_cancels):
    started = threading.Event()
    conn = _Conn()
    _patch_until_execute(monkeypatch, _blocking_execute(started, conn))
    monkeypatch.setattr("features.ask.service._EXECUTION_GRACE_SECONDS", 0)
    service = AskService(persist_queries=False)

    events = [event async for event in service._run_from_generate(_context(0.2))]

    error = events[-1]
    assert isinstance(error, AskErrorEvent)
    assert error.message == "warehouse didn't answer within 0.2 s."
    assert (error.code, error.phase) == ("query_timeout", AskPhase.EXECUTE)
    assert side_cancels == [("abort", conn)]


@pytest.mark.asyncio
async def test_disconnect_cancels_the_running_statement(monkeypatch, side_cancels):
    started = threading.Event()
    conn = _Conn()
    _patch_until_execute(monkeypatch, _blocking_execute(started, conn))
    service = AskService(persist_queries=False)

    async def consume():
        async for _event in service._run_from_generate(_context(30)):
            pass

    task = asyncio.create_task(consume())
    assert await asyncio.to_thread(started.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert await asyncio.to_thread(conn.cancel_requested.wait, 5)
    assert side_cancels == [("postgres", conn), ("abort", conn)]


def _serve_paused_postmaster(conn: socket.socket) -> None:
    """A postmaster paused after its session started: cancel requests get no answer either."""
    header = conn.recv(8, socket.MSG_PEEK)
    if len(header) == 8 and struct.unpack("!I", header[4:])[0] == _CANCEL_REQUEST_CODE:
        return
    _serve_frozen_postgres(conn)


@pytest.fixture
def paused_postgres(monkeypatch):
    listener = _listen(_serve_paused_postmaster)
    params = _pg_params(listener.getsockname()[1])
    monkeypatch.setattr(db_connection, "resolve_connection_params", lambda **_kw: params)
    monkeypatch.setattr("features.ask.service._EXECUTION_GRACE_SECONDS", 0)
    yield
    listener.close()


def _real_execute(finished: threading.Event):
    def execute(ctx, presenter, executor=None):
        try:
            return execute_phase.execute_query(ctx, presenter, executor)
        finally:
            finished.set()

    return execute


@pytest.mark.asyncio
async def test_a_server_paused_mid_query_ends_ask_at_the_deadline(monkeypatch, paused_postgres):
    finished = threading.Event()
    _patch_until_execute(monkeypatch, _real_execute(finished))
    service = AskService(persist_queries=False)
    started = time.monotonic()

    events = await asyncio.wait_for(
        _collect(service._run_from_generate(_context(1))), timeout=10
    )

    assert time.monotonic() - started < 4
    error = events[-1]
    assert isinstance(error, AskErrorEvent)
    assert (error.message, error.code) == ("warehouse didn't answer within 1 s.", "query_timeout")
    # The aborted socket also releases the worker thread.
    assert await asyncio.to_thread(finished.wait, 5)


@pytest.mark.asyncio
async def test_disconnect_from_a_paused_server_releases_the_worker(monkeypatch, paused_postgres):
    monkeypatch.setattr("features.ask.service._CANCEL_WAIT_SECONDS", 0.3)
    monkeypatch.setattr(db_connection, "POSTGRES_CANCEL_SECONDS", 1)
    finished = threading.Event()
    _patch_until_execute(monkeypatch, _real_execute(finished))
    service = AskService(persist_queries=False)
    task = asyncio.create_task(_collect(service._run_from_generate(_context(30))))
    await asyncio.sleep(0.5)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert await asyncio.to_thread(finished.wait, 5)


async def _collect(events):
    return [event async for event in events]
