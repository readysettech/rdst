"""Unit tests for manager-owned temporary Readyset experiments."""

import asyncio
import gc
import threading
import time
from contextlib import asynccontextmanager

import pytest

from features.cache.events import CacheCompareCompleteEvent, CacheRunCompleteEvent
from features.cache.experiment_service import (
    COMPARE_CANCEL_GRACE_SECONDS,
    _CompareEvidenceRecorder,
    ReadysetExperimentService,
    _execute_rows_cancellable,
    _has_top_level_limit_without_order,
    _run_comparison_cancellable,
    _run_readyset_sql_settled,
    parameter_fingerprint,
    temporary_cache_name,
)
from features.cache.live_comparison import LiveComparisonController
from shared.deploy.sandbox_manager import SandboxConnection
from shared.service_events import ErrorEvent


def _capture_evidence(monkeypatch, recorded):
    class Writer:
        def __init__(self, target, *, lane):
            self.target = target
            self.lane = lane

        def record(self, executions, **kwargs):
            recorded.append(
                ((self.target, list(executions)), {"lane": self.lane, **kwargs})
            )

        def close(self):
            return None

    monkeypatch.setattr(
        "features.cache.experiment_service.ExecutionEvidenceWriter", Writer
    )


class _Lease:
    def __init__(self) -> None:
        self.connection = SandboxConnection(
            engine="postgresql",
            host="127.0.0.1",
            port=5433,
            database="app",
            user="app",
            password="secret",
            cache_target="origin-sandbox",
        )
        self.dirty_reasons: list[str] = []

    async def mark_dirty(self, reason: str) -> None:
        self.dirty_reasons.append(reason)


class _Manager:
    def __init__(self) -> None:
        self.acquired: list[dict] = []
        self.lease_value = _Lease()
        self.active = False

    @asynccontextmanager
    async def lease(self, **kwargs):
        self.acquired.append(kwargs)
        self.active = True
        try:
            yield self.lease_value
        finally:
            self.active = False


class _Cache:
    def __init__(
        self,
        *,
        drop_success: bool = True,
        unsupported: bool = False,
        manager: _Manager | None = None,
    ):
        self.statements: list[str] = []
        self.drop_success = drop_success
        self.unsupported = unsupported
        self.manager = manager

    def _run_readyset_sql(self, statement: str, **_kwargs):
        self.statements.append(statement)
        if statement.startswith("EXPLAIN"):
            return {
                "success": True,
                "output": "unsupported" if self.unsupported else "supported",
            }
        if statement.startswith("DROP"):
            if self.manager is not None:
                assert self.manager.active is True
            return {"success": self.drop_success, "error": "drop failed"}
        return {"success": True}


async def _events(service: ReadysetExperimentService):
    return [
        event
        async for event in service.compare(
            owner_id="speed_test_123",
            target="origin",
            query="SELECT 1",
            iterations=3,
            warmup=1,
        )
    ]


async def _wait_for_thread_event(event: threading.Event, timeout: float) -> bool:
    """Poll without consuming another default-executor worker."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if event.is_set():
            return True
        await asyncio.sleep(0.01)
    return event.is_set()


@pytest.mark.asyncio
async def test_validation_cancellation_waits_for_database_thread(monkeypatch):
    started = threading.Event()
    cancelled = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def rows(_config, _query, controller):
        started.set()
        while not controller.cancelled:
            cancelled.wait(0.01)
        cancelled.set()
        release.wait(2)
        finished.set()
        return []

    monkeypatch.setattr("features.cache.experiment_service._execute_rows", rows)
    task = asyncio.create_task(_execute_rows_cancellable({}, "SELECT 1"))
    assert await _wait_for_thread_event(started, 1)

    task.cancel()
    assert await _wait_for_thread_event(cancelled, 1)
    await asyncio.sleep(0)
    assert task.done() is False
    release.set()
    assert await _wait_for_thread_event(finished, 1)
    await asyncio.sleep(0)

    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()


@pytest.mark.asyncio
async def test_cancel_closes_connection_blocked_in_execute(monkeypatch):
    executing = threading.Event()
    closed = threading.Event()

    class _Cursor:
        def execute(self, _query):
            executing.set()
            if not closed.wait(5):
                raise TimeoutError("execute was never interrupted")
            raise RuntimeError("connection closed")

        def close(self):
            return None

    class _Conn:
        def cursor(self):
            return _Cursor()

        def close(self):
            closed.set()

    monkeypatch.setattr(
        "shared.db_connection.create_direct_connection",
        lambda _config, lane: _Conn(),
    )
    monkeypatch.setattr(
        "shared.db_connection.close_connection", lambda _conn: None
    )
    task = asyncio.create_task(_execute_rows_cancellable({}, "SELECT 1"))
    assert await _wait_for_thread_event(executing, 1)

    started = time.monotonic()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()
    assert time.monotonic() - started < COMPARE_CANCEL_GRACE_SECONDS


@pytest.mark.asyncio
async def test_cancel_during_connect_is_bounded_and_discards_late_result(
    monkeypatch, caplog
):
    monkeypatch.setattr(
        "features.cache.experiment_service.COMPARE_CANCEL_GRACE_SECONDS", 0.2
    )
    connecting = threading.Event()
    connect_release = threading.Event()
    closed = threading.Event()
    observed = []

    class _Conn:
        def close(self):
            closed.set()

    def slow_connect(_config, lane):
        connecting.set()
        if not connect_release.wait(5):
            raise TimeoutError("connect release was never signalled")
        return _Conn()

    monkeypatch.setattr(
        "shared.db_connection.create_direct_connection", slow_connect
    )
    monkeypatch.setattr(
        "shared.db_connection.close_connection", lambda _conn: None
    )
    task = asyncio.create_task(
        _execute_rows_cancellable(
            {},
            "SELECT 1",
            on_execute=lambda: observed.append("start"),
            on_complete=lambda _token, _at: observed.append("complete"),
        )
    )
    assert await _wait_for_thread_event(connecting, 1)

    started = time.monotonic()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert time.monotonic() - started < 2

    with caplog.at_level("ERROR"):
        connect_release.set()
        assert await _wait_for_thread_event(closed, 1)
        # Give the loop time to settle the abandoned future and run its
        # retrieval callback, then force any unretrieved-exception report.
        await asyncio.sleep(0.05)
        del task
        gc.collect()
        await asyncio.sleep(0)
    assert observed == []
    assert not caplog.records


@pytest.mark.asyncio
async def test_hung_readyset_ddl_abandons_after_grace_and_marks_dirty(monkeypatch):
    monkeypatch.setattr(
        "features.cache.experiment_service.COMPARE_CANCEL_GRACE_SECONDS", 0.1
    )
    release = threading.Event()
    lease = _Lease()

    class _HungCache:
        def _run_readyset_sql(self, _statement, **_kwargs):
            release.wait(5)
            return {"success": True}

    task = asyncio.create_task(
        _run_readyset_sql_settled(_HungCache(), "DROP CACHE x", lease)
    )
    await asyncio.sleep(0.05)
    started = time.monotonic()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert time.monotonic() - started < 2
    assert lease.dirty_reasons == ["Readyset DDL was abandoned during cancel"]
    release.set()


@pytest.mark.asyncio
async def test_comparison_bridge_drains_ordered_progress(monkeypatch):
    def comparison(**kwargs):
        kwargs["on_progress"]("origin_warmup", 1, 1)
        for current in range(1, 4):
            kwargs["on_progress"]("origin", current, 3)
        kwargs["on_progress"]("cache_warmup", 1, 1)
        for current in range(1, 4):
            kwargs["on_progress"]("cache", current, 3)
        return {"success": True}

    monkeypatch.setattr(
        "features.cache.experiment_service.run_comparison", comparison
    )
    updates: list[tuple[str, str, int]] = []

    async def progress(stage, message, percent):
        updates.append((stage, message, percent))

    result = await _run_comparison_cancellable(
        query="SELECT 1",
        origin={},
        readyset={},
        iterations=3,
        warmup=0,
        interval_ms=None,
        concurrency=2,
        duration_seconds=None,
        progress=progress,
    )

    assert result["success"] is True
    assert [stage for stage, _, _ in updates] == (
        ["benchmarking_origin"] * 4 + ["benchmarking_readyset"] * 4
    )
    assert "Warming origin" in updates[0][1]
    assert "Warming Readyset" in updates[4][1]
    percents = [percent for _, _, percent in updates]
    assert percents == sorted(percents)


@pytest.mark.asyncio
async def test_comparison_bridge_persists_closed_bucket_before_runner_finishes(
    monkeypatch
):
    emitted = threading.Event()
    release = threading.Event()
    recorded = []

    def comparison(**kwargs):
        occurred_at = time.time() - 2
        kwargs["on_origin_start"]("one", occurred_at - 0.01)
        kwargs["on_origin_progress"]("one", occurred_at, 1)
        emitted.set()
        release.wait(2)
        return {"success": False, "error": "done"}

    monkeypatch.setattr(
        "features.cache.experiment_service.run_comparison", comparison
    )
    _capture_evidence(monkeypatch, recorded)
    recorder = _CompareEvidenceRecorder("origin", "SELECT 1")

    async def progress(*_args):
        return None

    task = asyncio.create_task(
        _run_comparison_cancellable(
            query="SELECT 1",
            origin={},
            readyset={},
            iterations=1,
            warmup=0,
            interval_ms=None,
            concurrency=None,
            duration_seconds=None,
            progress=progress,
            evidence=recorder,
        )
    )
    try:
        assert await _wait_for_thread_event(emitted, 1)
        deadline = asyncio.get_running_loop().time() + 1
        while not recorded and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)

        assert task.done() is False
        assert recorded[0][0] == (
            "origin",
            [{"sql": "SELECT 1", "exec_count": 1}],
        )
    finally:
        release.set()
    assert (await task)["success"] is False


@pytest.mark.asyncio
async def test_recorder_keeps_earliest_concurrent_execution_unresolved(monkeypatch):
    recorded = []
    _capture_evidence(monkeypatch, recorded)
    recorder = _CompareEvidenceRecorder("origin", "SELECT 1")
    try:
        recorder.note_started("old-hung", 10.0)
        recorder.note_started("new-complete", 12.0)
        recorder.note_completed("new-complete", 13.0)
        await recorder.flush_all()

        assert recorder.uncertain_started_at() == 10.0
        await recorder.record_unknown(recorder.uncertain_started_at(), 20.0)
        exact, unknown = recorded
        assert exact[0][1][0]["exec_count"] == 1
        assert unknown[0][1][0]["exec_count"] is None
        assert unknown[1]["started_at"] == 10.0
    finally:
        await recorder.close()


@pytest.mark.asyncio
async def test_reconcile_deficit_marks_unknown_without_fabricating_exact_bucket(
    monkeypatch,
):
    recorded = []
    _capture_evidence(monkeypatch, recorded)
    recorder = _CompareEvidenceRecorder("origin", "SELECT 1")
    try:
        recorder.note_started("observed", 100.0)
        recorder.note_completed("observed", 101.0)
        assert recorder.reconcile(3, uncertain_from=99.0) is False
        await recorder.flush_all()
        uncertain = recorder.uncertain_started_at()
        assert uncertain == 99.0
        await recorder.record_unknown(uncertain, 120.0)

        assert [call[0][1][0]["exec_count"] for call in recorded] == [1, None]
        assert recorded[-1][1]["started_at"] == 99.0
    finally:
        await recorder.close()


@pytest.fixture
def experiment_stubs(monkeypatch):
    monkeypatch.setattr(
        "features.cache.experiment_service._origin_connection_config",
        lambda _target: {"engine": "postgresql"},
    )
    monkeypatch.setattr(
        "features.cache.experiment_service._readyset_query",
        lambda query, _engine: query,
    )
    def rows(_config, _query, _controller=None, on_execute=None):
        if on_execute is not None:
            on_execute()
        return [(1,)]

    monkeypatch.setattr(
        "features.cache.experiment_service._execute_rows", rows
    )

    async def comparison(**_kwargs):
        return {
            "success": True,
            "iterations": 3,
            "original": {"stats": {"mean": 10.0, "median": 9.0}},
            "readyset": {"stats": {"mean": 1.0, "median": 0.9}},
            "speedup": {
                "mean": 10.0,
                "median": 10.0,
                "improvement_pct": 90.0,
            },
            "winner": "readyset",
        }

    monkeypatch.setattr(
        "features.cache.experiment_service._run_comparison_cancellable",
        comparison,
    )


@pytest.mark.asyncio
async def test_compare_forwards_load_controls_to_benchmark(
    experiment_stubs, monkeypatch
):
    captured: dict = {}

    async def comparison(**kwargs):
        captured.update(kwargs)
        return {
            "success": True,
            "iterations": 10,
            "original": {
                "iterations": 12,
                "stats": {"mean": 2.0, "median": 2.0},
            },
            "readyset": {
                "iterations": 10,
                "stats": {"mean": 1.0, "median": 1.0},
            },
            "speedup": {
                "mean": 2.0,
                "median": 2.0,
                "improvement_pct": 50.0,
            },
            "winner": "readyset",
        }

    monkeypatch.setattr(
        "features.cache.experiment_service._run_comparison_cancellable",
        comparison,
    )
    manager = _Manager()
    service = ReadysetExperimentService(manager, _Cache(manager=manager))

    events = [
        event
        async for event in service.compare(
            owner_id="speed_test_123",
            target="origin",
            query="SELECT 1",
            iterations=20,
            warmup=1,
            interval_ms=25,
            duration_seconds=10,
        )
    ]

    result = next(event for event in events if isinstance(event, CacheRunCompleteEvent))
    assert result.iterations == 10
    assert result.origin_iterations == 12
    assert result.cache_iterations == 10
    assert captured["iterations"] == 20
    assert captured["warmup"] == 1
    assert captured["interval_ms"] == 25
    assert captured["concurrency"] is None
    assert captured["duration_seconds"] == 10


@pytest.mark.asyncio
async def test_compare_creates_and_drops_only_its_named_cache(experiment_stubs):
    manager = _Manager()
    cache = _Cache(manager=manager)

    events = await _events(ReadysetExperimentService(manager, cache))

    result = next(event for event in events if isinstance(event, CacheRunCompleteEvent))
    cache_name = temporary_cache_name("speed_test_123", "SELECT 1")
    assert result.speedup_mean == 10.0
    assert result.origin_iterations == 3
    assert result.cache_iterations == 3
    assert cache.statements == [
        "EXPLAIN CREATE CACHE FROM SELECT 1",
        f"CREATE CACHE {cache_name} FROM SELECT 1",
        f"DROP CACHE {cache_name}",
    ]
    assert manager.acquired[0]["target"] == "origin"
    assert manager.lease_value.dirty_reasons == []


@pytest.mark.asyncio
async def test_live_compare_reuses_the_temporary_cache_lifecycle(
    experiment_stubs, monkeypatch
):
    async def live_comparison(**_kwargs):
        return {
            "success": True,
            "query": "SELECT 1",
            "duration_seconds": 30,
            "elapsed_seconds": 30.1,
            "concurrency": 4,
            "origin": {"throughput_rps": 10.0, "mean_ms": 20.0},
            "readyset": {"throughput_rps": 100.0, "mean_ms": 1.0},
            "timeline": [],
            "phases": [{"elapsed_seconds": 0.0, "concurrency": 4}],
            "speedup_mean": 20.0,
            "improvement_pct": 1900.0,
            "winner": "readyset",
        }

    monkeypatch.setattr(
        "features.cache.experiment_service._run_live_comparison_cancellable",
        live_comparison,
    )
    manager = _Manager()
    cache = _Cache(manager=manager)
    controller = LiveComparisonController(4)
    service = ReadysetExperimentService(manager, cache)

    events = [
        event
        async for event in service.compare_live(
            owner_id="cache_compare_123",
            target="origin",
            query="SELECT 1",
            duration_seconds=30,
            controller=controller,
        )
    ]

    result = next(
        event for event in events if isinstance(event, CacheCompareCompleteEvent)
    )
    cache_name = temporary_cache_name("cache_compare_123", "SELECT 1")
    assert result.readyset["throughput_rps"] == 100.0
    assert cache.statements == [
        "EXPLAIN CREATE CACHE FROM SELECT 1",
        f"CREATE CACHE {cache_name} FROM SELECT 1",
        f"DROP CACHE {cache_name}",
    ]
    assert manager.acquired[0]["purpose"] == "live_compare"


@pytest.mark.asyncio
async def test_result_mismatch_records_validation_and_drops_temporary_cache(
    experiment_stubs, monkeypatch
):
    calls = 0
    recorded = []

    def different_rows(
        _config, _query, _controller=None, on_execute=None
    ):
        nonlocal calls
        if on_execute is not None:
            on_execute()
        calls += 1
        return [(calls,)]

    monkeypatch.setattr(
        "features.cache.experiment_service._execute_rows", different_rows
    )
    _capture_evidence(monkeypatch, recorded)
    manager = _Manager()
    cache = _Cache()

    events = await _events(ReadysetExperimentService(manager, cache))

    assert any(
        isinstance(event, ErrorEvent) and "different result" in event.message
        for event in events
    )
    assert cache.statements[-1].startswith("DROP CACHE rdst_tmp_")
    assert recorded[0][0] == (
        "origin",
        [{"sql": "SELECT 1", "exec_count": 1}],
    )


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("SELECT title FROM posts LIMIT 5", True),
        ("SELECT title FROM posts LIMIT $2", True),
        ("SELECT title FROM posts ORDER BY title LIMIT 5", False),
        ("SELECT title FROM posts ORDER BY title", False),
        ("SELECT title FROM (SELECT title FROM posts LIMIT 5) sub", False),
        ("not sql at all ((", False),
    ],
)
def test_has_top_level_limit_without_order(query, expected):
    assert _has_top_level_limit_without_order(query) is expected


@pytest.mark.asyncio
async def test_result_mismatch_with_limit_and_no_order_explains_why(
    experiment_stubs, monkeypatch
):
    calls = 0

    def different_rows(
        _config, _query, _controller=None, on_execute=None
    ):
        nonlocal calls
        if on_execute is not None:
            on_execute()
        calls += 1
        return [(calls,)]

    monkeypatch.setattr(
        "features.cache.experiment_service._execute_rows", different_rows
    )
    service = ReadysetExperimentService(_Manager(), _Cache())
    events = [
        event
        async for event in service.compare(
            owner_id="speed_test_123",
            target="origin",
            query="SELECT title FROM posts LIMIT 5",
            iterations=3,
            warmup=1,
        )
    ]

    assert any(
        isinstance(event, ErrorEvent)
        and "LIMIT without ORDER BY" in event.message
        and "Add an ORDER BY" in event.message
        for event in events
    )


@pytest.mark.asyncio
async def test_unordered_result_rows_are_compared_as_a_bag(
    experiment_stubs, monkeypatch
):
    def rows(config, _query, _controller=None, on_execute=None):
        if on_execute is not None:
            on_execute()
        if config.get("host") == "127.0.0.1":
            return [(2,), (1,), (1,)]
        return [(1,), (2,), (1,)]

    monkeypatch.setattr(
        "features.cache.experiment_service._execute_rows", rows
    )
    events = await _events(
        ReadysetExperimentService(_Manager(), _Cache())
    )

    assert any(isinstance(event, CacheRunCompleteEvent) for event in events)
    assert not any(isinstance(event, ErrorEvent) for event in events)


@pytest.mark.asyncio
async def test_ordered_result_rows_must_preserve_order(
    experiment_stubs, monkeypatch
):
    def rows(config, _query, _controller=None, on_execute=None):
        if on_execute is not None:
            on_execute()
        if config.get("host") == "127.0.0.1":
            return [(2,), (1,)]
        return [(1,), (2,)]

    monkeypatch.setattr(
        "features.cache.experiment_service._execute_rows", rows
    )
    service = ReadysetExperimentService(_Manager(), _Cache())
    events = [
        event
        async for event in service.compare(
            owner_id="speed_test_123",
            target="origin",
            query="SELECT value FROM items ORDER BY value",
            iterations=3,
            warmup=1,
        )
    ]

    assert any(
        isinstance(event, ErrorEvent) and "different result" in event.message
        for event in events
    )


@pytest.mark.asyncio
async def test_ordered_result_rows_may_reorder_within_ties(
    experiment_stubs, monkeypatch
):
    def rows(config, _query, _controller=None, on_execute=None):
        if on_execute is not None:
            on_execute()
        if config.get("host") == "127.0.0.1":
            return [("books", 2), ("books", 1), ("games", 3)]
        return [("books", 1), ("books", 2), ("games", 3)]

    monkeypatch.setattr(
        "features.cache.experiment_service._execute_rows", rows
    )
    service = ReadysetExperimentService(_Manager(), _Cache())
    events = [
        event
        async for event in service.compare(
            owner_id="speed_test_123",
            target="origin",
            query="SELECT category, id FROM items ORDER BY category",
            iterations=3,
            warmup=1,
        )
    ]

    assert any(isinstance(event, CacheRunCompleteEvent) for event in events)
    assert not any(isinstance(event, ErrorEvent) for event in events)


@pytest.mark.asyncio
async def test_validation_failure_settles_sibling_before_cleanup(
    experiment_stubs, monkeypatch
):
    manager = _Manager()
    cache = _Cache(manager=manager)
    sibling_started = asyncio.Event()
    sibling_finished = asyncio.Event()
    release_sibling = asyncio.Event()
    readyset_calls = 0

    async def rows(config, _query, *, on_execute=None, on_complete=None):
        nonlocal readyset_calls
        if config.get("host") == "127.0.0.1":
            readyset_calls += 1
            if readyset_calls == 1:
                return [(1,)]
            sibling_started.set()
            try:
                await release_sibling.wait()
                return [(1,)]
            finally:
                sibling_finished.set()

        await sibling_started.wait()
        if on_execute is not None:
            on_execute()
        raise RuntimeError("origin validation failed")

    monkeypatch.setattr(
        "features.cache.experiment_service._execute_rows_cancellable", rows
    )

    try:
        events = await asyncio.wait_for(
            _events(ReadysetExperimentService(manager, cache)), timeout=1
        )
        assert any(
            isinstance(event, ErrorEvent)
            and "origin validation failed" in event.message
            for event in events
        )
        assert sibling_finished.is_set()
        assert manager.active is False
        assert cache.statements[-1].startswith("DROP CACHE rdst_tmp_")
    finally:
        release_sibling.set()
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_cleanup_failure_marks_sandbox_dirty(experiment_stubs):
    manager = _Manager()
    cache = _Cache(drop_success=False)

    events = await _events(ReadysetExperimentService(manager, cache))

    assert any(isinstance(event, CacheRunCompleteEvent) for event in events)
    assert any(
        isinstance(event, ErrorEvent)
        and event.code == "speed_test_cleanup_failed"
        for event in events
    )
    assert manager.lease_value.dirty_reasons == [
        "Temporary cache cleanup failed: RuntimeError"
    ]


@pytest.mark.asyncio
async def test_unsupported_query_does_not_create_a_cache(experiment_stubs):
    manager = _Manager()
    cache = _Cache(unsupported=True)

    events = await _events(ReadysetExperimentService(manager, cache))

    assert any(
        isinstance(event, ErrorEvent) and event.code == "readyset_unsupported"
        for event in events
    )
    assert cache.statements == ["EXPLAIN CREATE CACHE FROM SELECT 1"]


@pytest.mark.asyncio
async def test_non_read_only_query_never_reaches_the_sandbox(experiment_stubs):
    """Request SQL is interpolated into cache DDL and executed on the origin."""
    manager = _Manager()
    cache = _Cache()
    service = ReadysetExperimentService(manager, cache)

    events = [
        event
        async for event in service.compare(
            owner_id="speed_test_123",
            target="origin",
            query="SELECT 1; DROP TABLE users",
            iterations=3,
            warmup=1,
        )
    ]

    assert any(
        isinstance(event, ErrorEvent) and event.code == "speed_test_read_only"
        for event in events
    )
    assert cache.statements == []
    assert manager.acquired == []


@pytest.mark.asyncio
async def test_cancellation_during_create_waits_then_drops_under_lease(
    experiment_stubs
):
    manager = _Manager()
    create_started = threading.Event()
    create_finished = threading.Event()
    allow_create = threading.Event()

    class BlockingCache(_Cache):
        def _run_readyset_sql(self, statement: str, **kwargs):
            if statement.startswith("CREATE"):
                self.statements.append(statement)
                create_started.set()
                allow_create.wait(timeout=2)
                create_finished.set()
                return {"success": True}
            return super()._run_readyset_sql(statement, **kwargs)

    cache = BlockingCache(manager=manager)
    service = ReadysetExperimentService(manager, cache)

    async def consume():
        return await _events(service)

    task = asyncio.create_task(consume())
    assert await _wait_for_thread_event(create_started, 1)
    task.cancel()
    await asyncio.sleep(0)
    assert task.done() is False
    allow_create.set()
    assert await _wait_for_thread_event(create_finished, 1)
    await asyncio.sleep(0)
    with pytest.raises(asyncio.CancelledError):
        await task

    assert cache.statements[-1].startswith("DROP CACHE rdst_tmp_")
    assert manager.active is False
    assert manager.lease_value.dirty_reasons == []


def test_fingerprints_and_cache_names_are_stable_without_embedding_sql():
    query = "SELECT secret_column FROM private_table"

    assert parameter_fingerprint(query) == parameter_fingerprint(f" {query} ")
    name = temporary_cache_name("SPEED/TEST:ABC", query)
    assert name == temporary_cache_name("SPEED/TEST:ABC", query)
    assert "secret" not in name
    assert len(name) <= 63


@pytest.mark.asyncio
async def test_compare_records_execution_evidence(experiment_stubs, monkeypatch):
    recorded = []
    _capture_evidence(monkeypatch, recorded)

    async def comparison(**kwargs):
        evidence = kwargs["evidence"]
        for token in range(4):
            evidence.note_started(token)
            evidence.note_completed(token)
        return {
            "success": True,
            "iterations": 3,
            "original": {"stats": {"mean": 10.0, "median": 9.0}, "executions": 4},
            "readyset": {"stats": {"mean": 1.0, "median": 0.9}, "executions": 4},
            "speedup": {"mean": 10.0, "median": 10.0, "improvement_pct": 90.0},
            "winner": "readyset",
        }

    monkeypatch.setattr(
        "features.cache.experiment_service._run_comparison_cancellable",
        comparison,
    )
    manager = _Manager()

    events = await _events(ReadysetExperimentService(manager, _Cache(manager=manager)))

    assert any(isinstance(event, CacheRunCompleteEvent) for event in events)
    # 4 completed benchmark executions against the origin, +1 validation.
    counts = [args[1][0]["exec_count"] for args, _kwargs in recorded]
    assert sum(counts) == 5
    assert all(count is not None for count in counts)
    assert all(kwargs["lane"] == "rdst/compare" for _args, kwargs in recorded)
    assert all(
        kwargs["started_at"] <= kwargs["ended_at"]
        for _args, kwargs in recorded
    )


@pytest.mark.asyncio
async def test_compare_without_exact_counts_records_unknown(
    experiment_stubs, monkeypatch
):
    recorded = []
    _capture_evidence(monkeypatch, recorded)
    manager = _Manager()

    async def comparison(**kwargs):
        kwargs["evidence"].note_started("hung")
        return {
            "success": True,
            "iterations": 3,
            "original": {"stats": {"mean": 10.0, "median": 9.0}},
            "readyset": {"stats": {"mean": 1.0, "median": 0.9}},
            "speedup": {
                "mean": 10.0,
                "median": 10.0,
                "improvement_pct": 90.0,
            },
            "winner": "readyset",
        }

    monkeypatch.setattr(
        "features.cache.experiment_service._run_comparison_cancellable",
        comparison,
    )

    # The experiment_stubs comparison result carries no execution count, so
    # the evidence must say "mark, don't subtract".
    events = await _events(ReadysetExperimentService(manager, _Cache(manager=manager)))

    assert any(isinstance(event, CacheRunCompleteEvent) for event in events)
    counts = [args[1][0]["exec_count"] for args, _kwargs in recorded]
    assert counts == [1, None]


@pytest.mark.asyncio
async def test_origin_benchmark_failure_before_execute_keeps_validation_exact(
    experiment_stubs, monkeypatch
):
    recorded = []
    _capture_evidence(monkeypatch, recorded)

    async def comparison(**_kwargs):
        return {
            "success": False,
            "error": "origin connection failed",
            "executions": 0,
        }

    monkeypatch.setattr(
        "features.cache.experiment_service._run_comparison_cancellable",
        comparison,
    )

    events = await _events(ReadysetExperimentService(_Manager(), _Cache()))

    assert any(
        isinstance(event, ErrorEvent)
        and "origin connection failed" in event.message
        for event in events
    )
    counts = [args[1][0]["exec_count"] for args, _kwargs in recorded]
    assert counts == [1]


@pytest.mark.asyncio
async def test_origin_benchmark_failure_after_execute_marks_only_tail_unknown(
    experiment_stubs, monkeypatch
):
    recorded = []
    _capture_evidence(monkeypatch, recorded)

    async def comparison(**kwargs):
        evidence = kwargs["evidence"]
        evidence.note_started("done-1")
        evidence.note_completed("done-1")
        evidence.note_started("done-2")
        evidence.note_completed("done-2")
        evidence.note_started("failed")
        return {
            "success": False,
            "error": "origin iteration failed",
            # Successful completions are exact, but the failed execute may
            # still affect database counters; this top-level count is not a
            # proof that the unresolved tail is zero.
            "executions": 2,
        }

    monkeypatch.setattr(
        "features.cache.experiment_service._run_comparison_cancellable",
        comparison,
    )

    await _events(ReadysetExperimentService(_Manager(), _Cache()))

    counts = [args[1][0]["exec_count"] for args, _kwargs in recorded]
    assert sum(count for count in counts if count is not None) == 3
    assert counts[-1] is None


@pytest.mark.asyncio
async def test_failed_compare_records_completed_origin_executions(
    experiment_stubs, monkeypatch
):
    recorded = []
    _capture_evidence(monkeypatch, recorded)

    async def comparison(**kwargs):
        evidence = kwargs["evidence"]
        for token in range(3):
            evidence.note_started(token)
            evidence.note_completed(token)
        return {
            "success": False,
            "error": "benchmark failed",
            "original": {"executions": 3},
        }

    monkeypatch.setattr(
        "features.cache.experiment_service._run_comparison_cancellable",
        comparison,
    )

    events = await _events(ReadysetExperimentService(_Manager(), _Cache()))

    assert any(
        isinstance(event, ErrorEvent) and "benchmark failed" in event.message
        for event in events
    )
    counts = [args[1][0]["exec_count"] for args, _kwargs in recorded]
    assert sum(counts) == 4
    assert all(count is not None for count in counts)


@pytest.mark.asyncio
async def test_cancelled_compare_after_benchmark_starts_records_unknown(
    experiment_stubs, monkeypatch
):
    recorded = []
    benchmark_started = asyncio.Event()
    never = asyncio.Event()
    _capture_evidence(monkeypatch, recorded)

    async def comparison(**kwargs):
        kwargs["evidence"].note_started("hung")
        benchmark_started.set()
        await never.wait()

    monkeypatch.setattr(
        "features.cache.experiment_service._run_comparison_cancellable",
        comparison,
    )
    task = asyncio.create_task(
        _events(ReadysetExperimentService(_Manager(), _Cache()))
    )
    await asyncio.wait_for(benchmark_started.wait(), timeout=1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    counts = [args[1][0]["exec_count"] for args, _kwargs in recorded]
    assert counts == [1, None]


@pytest.mark.asyncio
async def test_compare_records_nothing_before_origin_begins(
    experiment_stubs, monkeypatch
):
    recorded = []
    _capture_evidence(monkeypatch, recorded)

    events = await _events(
        ReadysetExperimentService(_Manager(), _Cache(unsupported=True))
    )

    assert any(
        isinstance(event, ErrorEvent) and event.code == "readyset_unsupported"
        for event in events
    )
    assert recorded == []


@pytest.mark.asyncio
async def test_incremental_evidence_is_applied_to_its_own_observation_window(
    tmp_path, monkeypatch
):
    from features.cache import experiment_service
    from features.query_registry.discovery import QueryDiscoveryCollector
    from shared.query_registry.observation_store import (
        ExecutionEvidenceWriter,
        ObservationStore,
    )
    from shared.query_registry.query_registry import hash_sql

    path = tmp_path / "cache.db"
    store = ObservationStore(path)

    monkeypatch.setattr(
        experiment_service,
        "ExecutionEvidenceWriter",
        lambda target, *, lane: ExecutionEvidenceWriter(
            target, lane=lane, cache_db_path=path
        ),
    )
    recorder = experiment_service._CompareEvidenceRecorder(
        "origin", "SELECT 1"
    )

    try:
        # A closed second is durable before the compare finishes.
        for token in ("a", "b"):
            recorder.note_started(token, occurred_at=1019.9)
            recorder.note_completed(token, occurred_at=1020.1)
        await recorder.flush_closed(now=1021)
        normalized = hash_sql("SELECT 1")
        assert store.rdst_executions_overlapping(
            "origin", normalized, 1000, 1059
        )[0]["exec_count"] == 2

        # A later completion gets a distinct point bucket rather than
        # extending an aggregate span across both observation windows.
        for token in ("c", "d", "e"):
            recorder.note_started(token, occurred_at=1079.9)
            recorder.note_completed(token, occurred_at=1080.1)
        await recorder.flush_closed(now=1081)
        for token in ("f", "g", "h", "i"):
            recorder.note_started(token, occurred_at=1059.9)
            recorder.note_completed(token, occurred_at=1060.0)
        await recorder.flush_closed(now=1081)
        assert recorder.last_event_at == 1080.1

        discovery = object.__new__(QueryDiscoveryCollector)
        discovery.target = "origin"
        discovery._store = store
        rows = [
            {
                "normalized_hash": normalized,
                "window_start": 1000,
                "window_end": 1060,
                "calls_delta": 12,
                "exec_time_delta": 120.0,
                "approximate_qps": 12 / 60,
                "completeness": "complete",
            },
            {
                "normalized_hash": normalized,
                "window_start": 1060,
                "window_end": 1119,
                "calls_delta": 12,
                "exec_time_delta": 120.0,
                "approximate_qps": 12 / 59,
                "completeness": "complete",
            },
        ]

        attributed = discovery._apply_rdst_attribution(rows)

        # The point bucket exactly on the shared boundary belongs only to the
        # preceding (start, end] window, never both adjacent windows.
        assert [row["calls_delta"] for row in attributed] == [6, 9]
        assert [row["completeness"] for row in attributed] == [
            "complete",
            "complete",
        ]
        assert [row["attribution"] for row in attributed] == [
            "contains_rdst_traffic",
            "contains_rdst_traffic",
        ]
    finally:
        await recorder.close()
        store.close()


@pytest.mark.asyncio
async def test_origin_connection_failure_before_execute_records_nothing(
    experiment_stubs, monkeypatch
):
    recorded = []
    _capture_evidence(monkeypatch, recorded)

    def rows(config, _query, _controller=None, on_execute=None):
        if config.get("host") == "127.0.0.1":
            return [(1,)]
        # Simulate create_direct_connection()/cursor() failing before the
        # callback immediately preceding cursor.execute().
        raise RuntimeError("origin connection failed")

    monkeypatch.setattr(
        "features.cache.experiment_service._execute_rows", rows
    )

    events = await _events(ReadysetExperimentService(_Manager(), _Cache()))

    assert any(
        isinstance(event, ErrorEvent)
        and "origin connection failed" in event.message
        for event in events
    )
    assert recorded == []


@pytest.mark.asyncio
async def test_cancellation_while_origin_execute_is_in_flight_records_unknown(
    experiment_stubs, monkeypatch
):
    recorded = []
    origin_started = threading.Event()
    origin_cancelled = threading.Event()
    _capture_evidence(monkeypatch, recorded)

    def rows(config, _query, controller=None, on_execute=None):
        if config.get("host") == "127.0.0.1":
            return [(1,)]
        assert controller is not None
        assert on_execute is not None
        on_execute()
        origin_started.set()
        while not controller.cancelled:
            origin_cancelled.wait(0.01)
        origin_cancelled.set()
        raise RuntimeError("cancelled in flight")

    monkeypatch.setattr(
        "features.cache.experiment_service._execute_rows", rows
    )
    task = asyncio.create_task(
        _events(ReadysetExperimentService(_Manager(), _Cache()))
    )
    assert await _wait_for_thread_event(origin_started, 1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert origin_cancelled.is_set()
    assert recorded[0][0] == (
        "origin",
        [{"sql": "SELECT 1", "exec_count": None}],
    )


@pytest.mark.asyncio
async def test_evidence_store_failure_does_not_fail_the_compare(
    experiment_stubs, monkeypatch, tmp_path
):
    # Drive the real best-effort helper into a broken store: cache.db exists,
    # but opening it raises. The speed test must still complete.
    from shared.query_registry import observation_store

    cache_db = tmp_path / "cache.db"
    cache_db.touch()
    monkeypatch.setattr(
        observation_store, "default_cache_db_path", lambda: cache_db
    )

    def broken_store(*args, **kwargs):
        raise RuntimeError("store exploded")

    monkeypatch.setattr(observation_store, "ObservationStore", broken_store)
    manager = _Manager()

    events = await _events(ReadysetExperimentService(manager, _Cache(manager=manager)))

    assert any(isinstance(event, CacheRunCompleteEvent) for event in events)
    assert not any(isinstance(event, ErrorEvent) for event in events)
