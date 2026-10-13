"""Cancellation and serialization for unattended analysis statements."""

from __future__ import annotations

import threading
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from typing import Any, Callable


PLAN_ANALYSIS_CONCURRENCY = 3


class AnalysisRun:
    def __init__(self, target: str, execute: bool = True) -> None:
        self.target = target
        self.execute = execute
        self.stopped = threading.Event()
        self.connections: list[tuple[Any, str, dict[str, Any] | None]] = []
        self.lock = threading.Lock()


class _Cursor:
    def __init__(self, cursor: Any, run: AnalysisRun) -> None:
        self._cursor = cursor
        self._run = run

    def execute(self, *args: Any) -> Any:
        return execute(self._run, lambda: self._cursor.execute(*args))

    def executemany(self, *args: Any) -> Any:
        return execute(self._run, lambda: self._cursor.executemany(*args))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)


class StatementConnection:
    def __init__(self, conn: Any, run: AnalysisRun | None) -> None:
        self._conn = conn
        self._run = run

    def cursor(self, *args: Any, **kwargs: Any) -> Any:
        if self._run is not None and self._run.stopped.is_set():
            raise RuntimeError("Analysis stopped")
        cursor = self._conn.cursor(*args, **kwargs)
        return _Cursor(cursor, self._run) if self._run is not None else cursor

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


_runs: dict[str, list[AnalysisRun]] = {}
_current: ContextVar[AnalysisRun | None] = ContextVar("analysis_run", default=None)
_connection_budgets: dict[str, threading.BoundedSemaphore] = {}
_statement_locks: dict[str, threading.Lock] = {}
_registry_lock = threading.Lock()


def begin(target: str, *, execute: bool = True) -> AnalysisRun:
    with _registry_lock:
        run = AnalysisRun(target, execute)
        _runs.setdefault(target, []).append(run)
        _current.set(run)
        _connection_budgets.setdefault(target, threading.BoundedSemaphore(PLAN_ANALYSIS_CONCURRENCY))
        _statement_locks.setdefault(target, threading.Lock())
        return run


def active(target: str) -> AnalysisRun | None:
    with _registry_lock:
        run = _current.get()
        if run is not None and run.target == target:
            return run
        runs = _runs.get(target, [])
        return runs[0] if len(runs) == 1 else None


def register(target: str, conn: Any, engine: str,
             config: dict[str, Any] | None = None) -> AnalysisRun | None:
    run = active(target)
    if run is not None:
        with run.lock:
            if run.stopped.is_set():
                from shared.db_connection import cancel_query

                cancel_query(conn, engine, config)
            else:
                run.connections.append((conn, engine, config))
    return run


def unregister(run: AnalysisRun | None, conn: Any) -> None:
    if run is not None:
        with run.lock:
            run.connections = [row for row in run.connections if row[0] is not conn]


def execute(run: AnalysisRun | None, action: Callable[[], Any]) -> Any:
    if run is None:
        return action()
    with _statement_locks[run.target] if run.execute else nullcontext():
        if run.stopped.is_set():
            raise RuntimeError("Analysis stopped")
        return action()


def stop(run: AnalysisRun) -> None:
    run.stopped.set()
    if _current.get() is run:
        _current.set(None)
    from shared.db_connection import cancel_registered

    cancel_registered(run.lock, lambda: run.connections)
    with _registry_lock:
        runs = _runs.get(run.target, [])
        if run in runs:
            runs.remove(run)
        if not runs:
            _runs.pop(run.target, None)


def finish(run: AnalysisRun) -> None:
    if _current.get() is run:
        _current.set(None)
    with _registry_lock:
        runs = _runs.get(run.target, [])
        if run in runs:
            runs.remove(run)
        if not runs:
            _runs.pop(run.target, None)


@contextmanager
def connection_budget():
    """Reserve a connection before opening it, releasing the slot after closing it.

    Sampling and plans share three slots per database. The registry lock is released
    before waiting for a slot or taking a run lock.
    """
    run = _current.get()
    if run is None:
        yield
        return
    budget = _connection_budgets[run.target]
    while not budget.acquire(timeout=0.1):
        if run.stopped.is_set():
            raise RuntimeError("Analysis stopped")
    try:
        if run.stopped.is_set():
            raise RuntimeError("Analysis stopped")
        yield
    finally:
        budget.release()
