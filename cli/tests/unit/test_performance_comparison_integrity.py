from __future__ import annotations

import threading
import time

import pytest

from features.cache import performance_comparison


class _Connection:
    def close(self) -> None:
        pass


def test_execute_start_callback_is_immediately_before_cursor_execute():
    order = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, _query):
            order.append("execute")

        def fetchall(self):
            return []

    class Connection:
        def cursor(self):
            order.append("cursor")
            return Cursor()

    result = performance_comparison._execute_on_connection(
        Connection(),
        "SELECT 1",
        "postgresql",
        on_execute=lambda: order.append("start"),
    )

    assert result["success"] is True
    assert order == ["cursor", "start", "execute"]


def test_cursor_creation_failure_does_not_emit_execute_start():
    started = []

    class Connection:
        def cursor(self):
            raise RuntimeError("cursor unavailable")

    result = performance_comparison._execute_on_connection(
        Connection(),
        "SELECT 1",
        "postgresql",
        on_execute=lambda: started.append(True),
    )

    assert result["success"] is False
    assert started == []


def test_partial_measured_failure_rejects_the_comparison(monkeypatch):
    connections = iter([(_Connection(), "postgresql"), (_Connection(), "postgresql")])
    monkeypatch.setattr(
        performance_comparison,
        "_open_persistent_connection",
        lambda _config: next(connections),
    )
    outcomes = iter(
        [
            {"success": True, "execution_time_ms": 10.0},
            {"success": False, "error": "statement timeout"},
            {"success": True, "execution_time_ms": 3.0},
            {"success": True, "execution_time_ms": 2.0},
        ]
    )
    def execute(*_args, **kwargs):
        token = kwargs["on_execute"]() if kwargs.get("on_execute") else None
        result = next(outcomes)
        if result["success"] and kwargs.get("on_complete") is not None:
            kwargs["on_complete"](token)
        return result

    monkeypatch.setattr(performance_comparison, "_execute_on_connection", execute)

    result = performance_comparison.run_comparison(
        query="SELECT 1",
        original_db_config={"engine": "postgresql"},
        readyset_db_config={"engine": "postgresql"},
        iterations=2,
        warmup_iterations=0,
    )

    assert result["success"] is False
    assert "original" in result["error"].lower()
    assert "iteration 2" in result["error"].lower()


def test_warmup_failure_rejects_before_measurement(monkeypatch):
    connections = iter([(_Connection(), "postgresql"), (_Connection(), "postgresql")])
    monkeypatch.setattr(
        performance_comparison,
        "_open_persistent_connection",
        lambda _config: next(connections),
    )
    monkeypatch.setattr(
        performance_comparison,
        "_execute_on_connection",
        lambda *_args, **_kwargs: {
            "success": False,
            "error": "connection reset",
        },
    )

    result = performance_comparison.run_comparison(
        query="SELECT 1",
        original_db_config={"engine": "postgresql"},
        readyset_db_config={"engine": "postgresql"},
        iterations=2,
        warmup_iterations=1,
    )

    assert result["success"] is False
    assert "warmup" in result["error"].lower()


def test_concurrent_comparison_uses_bounded_parallel_connections(monkeypatch):
    active = 0
    max_active = 0
    active_lock = threading.Lock()
    opened: list[_Connection] = []
    progress: list[tuple[str, int, int]] = []

    def open_connection(_config):
        connection = _Connection()
        opened.append(connection)
        return connection, "postgresql"

    def execute(*_args, **_kwargs):
        nonlocal active, max_active
        with active_lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.01)
        with active_lock:
            active -= 1
        return {"success": True, "execution_time_ms": 1.0}

    monkeypatch.setattr(
        performance_comparison, "_open_persistent_connection", open_connection
    )
    monkeypatch.setattr(performance_comparison, "_execute_on_connection", execute)

    result = performance_comparison.run_comparison(
        query="SELECT 1",
        original_db_config={"engine": "postgresql"},
        readyset_db_config={"engine": "postgresql"},
        iterations=6,
        warmup_iterations=0,
        concurrency=3,
        on_progress=lambda *values: progress.append(values),
    )

    assert result["success"] is True
    assert result["iterations"] == 6
    assert result["original"]["iterations"] == 6
    assert result["readyset"]["iterations"] == 6
    assert len(opened) == 6
    assert max_active == 3
    assert [current for stage, current, _ in progress if stage == "origin"] == list(
        range(1, 7)
    )
    assert [current for stage, current, _ in progress if stage == "cache"] == list(
        range(1, 7)
    )


def test_concurrent_cancellation_settles_workers_and_connections(monkeypatch):
    class Connection:
        def __init__(self):
            self.cancelled = threading.Event()
            self.closed = False

        def cancel(self):
            self.cancelled.set()

        def close(self):
            self.closed = True

    connections: list[Connection] = []
    workers_started = threading.Event()
    active = 0
    active_lock = threading.Lock()

    def open_connection(_config):
        connection = Connection()
        connections.append(connection)
        return connection, "postgresql"

    def execute(connection, _query, _engine, controller, **_kwargs):
        nonlocal active
        with active_lock:
            active += 1
            if active == 3:
                workers_started.set()
        connection.cancelled.wait(2)
        controller.raise_if_cancelled()
        return {"success": True, "execution_time_ms": 1.0}

    monkeypatch.setattr(
        performance_comparison, "_open_persistent_connection", open_connection
    )
    monkeypatch.setattr(performance_comparison, "_execute_on_connection", execute)
    controller = performance_comparison.ComparisonController()
    result: dict = {}

    def compare():
        result.update(
            performance_comparison.run_comparison(
                query="SELECT 1",
                original_db_config={"engine": "postgresql"},
                readyset_db_config={"engine": "postgresql"},
                iterations=100,
                warmup_iterations=0,
                concurrency=3,
                controller=controller,
            )
        )

    thread = threading.Thread(target=compare)
    thread.start()
    assert workers_started.wait(1)
    controller.cancel()
    thread.join(2)

    assert thread.is_alive() is False
    assert result["success"] is False
    assert result["cancelled"] is True
    assert all(connection.closed for connection in connections)


def test_duration_cancels_an_in_flight_sequential_query(monkeypatch):
    class Connection:
        def __init__(self):
            self.cancelled = threading.Event()
            self.closed = False

        def cancel(self):
            self.cancelled.set()

        def close(self):
            self.closed = True

    connections: list[Connection] = []

    def open_connection(_config):
        connection = Connection()
        connections.append(connection)
        return connection, "postgresql"

    def execute(connection, *_args, **_kwargs):
        connection.cancelled.wait(5)
        return {"success": False, "error": "cancelled at deadline"}

    monkeypatch.setattr(
        performance_comparison, "_open_persistent_connection", open_connection
    )
    monkeypatch.setattr(performance_comparison, "_execute_on_connection", execute)

    started = time.monotonic()
    result = performance_comparison.run_comparison(
        query="SELECT pg_sleep(30)",
        original_db_config={"engine": "postgresql"},
        readyset_db_config={"engine": "postgresql"},
        iterations=2,
        warmup_iterations=0,
        duration_seconds=1,
    )

    assert time.monotonic() - started < 2
    assert result["success"] is False
    assert connections[0].cancelled.is_set()
    assert all(connection.closed for connection in connections)


def test_duration_waits_for_timer_cancellation_before_closing(monkeypatch):
    cancel_started = threading.Event()
    allow_cancel = threading.Event()
    cancel_finished = threading.Event()

    class Connection:
        def __init__(self):
            self.closed = False

        def cancel(self):
            cancel_started.set()
            allow_cancel.wait(2)
            cancel_finished.set()

        def close(self):
            assert cancel_finished.is_set()
            self.closed = True

    connections: list[Connection] = []

    def open_connection(_config):
        connection = Connection()
        connections.append(connection)
        return connection, "postgresql"

    def execute(*_args, **_kwargs):
        cancel_started.wait(2)
        return {"success": False, "error": "deadline"}

    monkeypatch.setattr(
        performance_comparison, "_open_persistent_connection", open_connection
    )
    monkeypatch.setattr(performance_comparison, "_execute_on_connection", execute)
    result: dict = {}

    thread = threading.Thread(
        target=lambda: result.update(
            performance_comparison.run_comparison(
                query="SELECT 1",
                original_db_config={"engine": "postgresql"},
                readyset_db_config={"engine": "postgresql"},
                iterations=1,
                warmup_iterations=0,
                duration_seconds=1,
            )
        )
    )
    thread.start()
    assert cancel_started.wait(2)
    time.sleep(0.05)
    assert thread.is_alive()
    assert not any(connection.closed for connection in connections)

    allow_cancel.set()
    thread.join(2)

    assert thread.is_alive() is False
    assert result["success"] is False
    assert all(connection.closed for connection in connections)


def test_concurrent_failure_cancels_active_siblings(monkeypatch):
    class Connection:
        def __init__(self, index):
            self.index = index
            self.cancelled = threading.Event()

        def cancel(self):
            self.cancelled.set()

        def close(self):
            pass

    connections: list[Connection] = []
    barrier = threading.Barrier(3)

    def open_connection(_config):
        connection = Connection(len(connections))
        connections.append(connection)
        return connection, "postgresql"

    def execute(connection, *_args, **_kwargs):
        barrier.wait(timeout=1)
        if connection.index == 0:
            return {"success": False, "error": "first worker failed"}
        connection.cancelled.wait(2)
        return {"success": False, "error": "cancelled sibling"}

    monkeypatch.setattr(
        performance_comparison, "_open_persistent_connection", open_connection
    )
    monkeypatch.setattr(performance_comparison, "_execute_on_connection", execute)

    started = time.monotonic()
    result = performance_comparison.run_comparison(
        query="SELECT 1",
        original_db_config={"engine": "postgresql"},
        readyset_db_config={"engine": "postgresql"},
        iterations=6,
        warmup_iterations=0,
        concurrency=3,
    )

    assert time.monotonic() - started < 1
    assert result["success"] is False
    assert "first worker failed" in result["error"]
    assert all(connection.cancelled.is_set() for connection in connections)


def test_duration_limits_each_side_without_discarding_completed_samples(monkeypatch):
    connections = iter([(_Connection(), "postgresql"), (_Connection(), "postgresql")])
    current = 0.0

    def clock():
        nonlocal current
        current += 0.2
        return current

    monkeypatch.setattr(
        performance_comparison,
        "_open_persistent_connection",
        lambda _config: next(connections),
    )
    monkeypatch.setattr(
        performance_comparison,
        "_execute_on_connection",
        lambda *_args, **_kwargs: {"success": True, "execution_time_ms": 1.0},
    )
    monkeypatch.setattr(performance_comparison.time, "perf_counter", clock)

    result = performance_comparison.run_comparison(
        query="SELECT 1",
        original_db_config={"engine": "postgresql"},
        readyset_db_config={"engine": "postgresql"},
        iterations=10,
        warmup_iterations=0,
        duration_seconds=1,
    )

    assert result["success"] is True
    assert 0 < result["original"]["iterations"] < 10
    assert 0 < result["readyset"]["iterations"] < 10
    assert result["iterations"] == min(
        result["original"]["iterations"], result["readyset"]["iterations"]
    )


@pytest.mark.parametrize(
    "options,error",
    [
        ({"interval_ms": 1, "concurrency": 2}, "both interval and concurrency"),
        ({"interval_ms": -1}, "interval"),
        ({"concurrency": 0}, "concurrency"),
        ({"duration_seconds": 0}, "duration"),
    ],
)
def test_invalid_load_controls_are_rejected_before_connecting(
    monkeypatch, options, error
):
    open_connection = pytest.fail
    monkeypatch.setattr(
        performance_comparison, "_open_persistent_connection", open_connection
    )

    result = performance_comparison.run_comparison(
        query="SELECT 1",
        original_db_config={"engine": "postgresql"},
        readyset_db_config={"engine": "postgresql"},
        iterations=2,
        warmup_iterations=0,
        **options,
    )

    assert result["success"] is False
    assert error in result["error"].lower()


def test_result_reports_exact_execution_counts_per_side(monkeypatch):
    """Q11 attribution: each side's completed executions (warmups included)
    are reported exactly so the compare lane can record subtractable
    evidence."""
    connections = iter([(_Connection(), "postgresql"), (_Connection(), "postgresql")])
    monkeypatch.setattr(
        performance_comparison,
        "_open_persistent_connection",
        lambda _config: next(connections),
    )
    monkeypatch.setattr(
        performance_comparison,
        "_execute_on_connection",
        lambda *_args, **_kwargs: {"success": True, "execution_time_ms": 1.0},
    )

    result = performance_comparison.run_comparison(
        query="SELECT 1",
        original_db_config={"engine": "postgresql"},
        readyset_db_config={"engine": "postgresql"},
        iterations=2,
        warmup_iterations=1,
    )

    assert result["success"] is True
    assert result["original"]["executions"] == 3
    assert result["readyset"]["executions"] == 3


def test_readyset_failure_preserves_completed_origin_execution_count(monkeypatch):
    connections = iter([(_Connection(), "postgresql"), (_Connection(), "postgresql")])
    monkeypatch.setattr(
        performance_comparison,
        "_open_persistent_connection",
        lambda _config: next(connections),
    )
    outcomes = iter(
        [
            {"success": True, "execution_time_ms": 10.0},
            {"success": True, "execution_time_ms": 9.0},
            {"success": True, "execution_time_ms": 8.0},
            {"success": False, "error": "Readyset unavailable"},
        ]
    )
    progress: list[tuple[int, int]] = []
    def execute(*_args, **kwargs):
        token = kwargs["on_execute"]() if kwargs.get("on_execute") else None
        result = next(outcomes)
        if result["success"] and kwargs.get("on_complete") is not None:
            kwargs["on_complete"](token)
        return result

    monkeypatch.setattr(performance_comparison, "_execute_on_connection", execute)

    result = performance_comparison.run_comparison(
        query="SELECT 1",
        original_db_config={"engine": "postgresql"},
        readyset_db_config={"engine": "postgresql"},
        iterations=2,
        warmup_iterations=1,
        on_origin_progress=lambda token, occurred_at, count: progress.append(
            (token, occurred_at, count)
        ),
    )

    assert result["success"] is False
    assert "Readyset unavailable" in result["error"]
    assert result["original"]["executions"] == 3
    assert len(progress) == 3
    assert all(count == 1 for _token, _occurred_at, count in progress)


def test_concurrent_result_reports_exact_execution_counts(monkeypatch):
    monkeypatch.setattr(
        performance_comparison,
        "_open_persistent_connection",
        lambda _config: (_Connection(), "postgresql"),
    )
    monkeypatch.setattr(
        performance_comparison,
        "_execute_on_connection",
        lambda *_args, **_kwargs: {"success": True, "execution_time_ms": 1.0},
    )

    result = performance_comparison.run_comparison(
        query="SELECT 1",
        original_db_config={"engine": "postgresql"},
        readyset_db_config={"engine": "postgresql"},
        iterations=4,
        warmup_iterations=2,
        concurrency=2,
    )

    assert result["success"] is True
    assert result["original"]["executions"] == 6
    assert result["readyset"]["executions"] == 6
