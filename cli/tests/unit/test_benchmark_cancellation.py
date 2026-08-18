"""T10 — cancelling a load test must never hang the stream.

Workers register their connections with the benchmark controller, so a cancel
aborts a statement blocked server-side; if a driver ignores the close, the
post-cancel waits are bounded by BENCHMARK_CANCEL_GRACE_SECONDS. These tests
run the sync worker on a real executor thread so a blocking execute exercises
the same code path as a stuck driver call.
"""

from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import patch

import pytest

from features.query_registry.service import QueryService


class _FakeTargetsConfig:
    def load(self) -> None:
        pass

    def get_default(self) -> str:
        return "demo"

    def get(self, name: str) -> dict:
        return {"engine": "postgresql", "host": "127.0.0.1"}


class _BlockingCursor:
    """Cursor whose execute blocks like a long server-side statement until the
    connection is closed."""

    def __init__(self, conn: "_BlockingConnection") -> None:
        self._conn = conn

    def execute(self, sql: str) -> None:
        if sql.strip().lower().startswith("set "):
            return
        self._conn.execute_entered.set()
        if not self._conn.unblock.wait(timeout=10):
            raise TimeoutError("test cursor was never unblocked")
        raise RuntimeError("connection closed while executing")

    def fetchall(self):
        return []

    def close(self) -> None:
        pass


class _BlockingConnection:
    def __init__(self, close_unblocks: bool = True) -> None:
        self.execute_entered = threading.Event()
        self.unblock = threading.Event()
        self.close_calls = 0
        self._close_unblocks = close_unblocks

    def cursor(self) -> _BlockingCursor:
        return _BlockingCursor(self)

    def close(self) -> None:
        self.close_calls += 1
        if self._close_unblocks:
            self.unblock.set()


def _consume(service: QueryService, connection: _BlockingConnection):
    async def run() -> None:
        with (
            patch(
                "shared.db_connection.create_direct_connection",
                return_value=connection,
            ),
            patch(
                "shared.config.targets.create_targets_config",
                return_value=_FakeTargetsConfig(),
            ),
        ):
            async for _event in service.stream_benchmark(
                queries=[{"identifier": "q", "sql": "SELECT 1"}],
                target="demo",
                mode="interval",
                interval_ms=0,
                concurrency=1,
                duration_seconds=5,
                max_count=None,
            ):
                pass

    return run()


async def _wait_for(event: threading.Event, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not event.is_set():
        assert time.monotonic() < deadline, "worker never reached execute"
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_cancel_closes_registered_connection_and_returns_promptly():
    """A worker blocked in execute is unblocked by the cancel closing its
    registered connection; the stream returns well within the grace bound."""
    connection = _BlockingConnection(close_unblocks=True)
    task = asyncio.create_task(_consume(QueryService(), connection))
    await _wait_for(connection.execute_entered)

    started = time.monotonic()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert time.monotonic() - started < 5.0
    assert connection.close_calls >= 1


@pytest.mark.asyncio
async def test_cancel_with_unkillable_worker_returns_at_grace_deadline(
    monkeypatch,
):
    """If closing the connection does not unblock the driver call, the stream
    still returns once BENCHMARK_CANCEL_GRACE_SECONDS elapses."""
    monkeypatch.setattr(
        "features.query_registry.service.BENCHMARK_CANCEL_GRACE_SECONDS", 0.5
    )
    connection = _BlockingConnection(close_unblocks=False)
    task = asyncio.create_task(_consume(QueryService(), connection))
    await _wait_for(connection.execute_entered)

    started = time.monotonic()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert time.monotonic() - started < 5.0
    assert connection.close_calls >= 1
    # Let the stuck daemon worker unblock so it cannot outlive the test run.
    connection.unblock.set()
