"""Unit tests for manager-owned temporary Readyset experiments."""

import asyncio
import threading
from contextlib import asynccontextmanager

import pytest

from features.cache.events import CacheRunCompleteEvent
from features.cache.experiment_service import (
    ReadysetExperimentService,
    parameter_fingerprint,
    temporary_cache_name,
)
from shared.deploy.sandbox_manager import SandboxConnection
from shared.service_events import ErrorEvent


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
    monkeypatch.setattr(
        "features.cache.experiment_service._execute_rows",
        lambda _config, _query, _controller=None: [(1,)],
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
async def test_compare_creates_and_drops_only_its_named_cache(experiment_stubs):
    manager = _Manager()
    cache = _Cache(manager=manager)

    events = await _events(ReadysetExperimentService(manager, cache))

    result = next(event for event in events if isinstance(event, CacheRunCompleteEvent))
    cache_name = temporary_cache_name("speed_test_123", "SELECT 1")
    assert result.speedup_mean == 10.0
    assert cache.statements == [
        "EXPLAIN CREATE CACHE FROM SELECT 1",
        f"CREATE CACHE {cache_name} FROM SELECT 1",
        f"DROP CACHE {cache_name}",
    ]
    assert manager.acquired[0]["target"] == "origin"
    assert manager.lease_value.dirty_reasons == []


@pytest.mark.asyncio
async def test_result_mismatch_still_drops_temporary_cache(
    experiment_stubs, monkeypatch
):
    calls = 0

    def different_rows(_config, _query, _controller=None):
        nonlocal calls
        calls += 1
        return [(calls,)]

    monkeypatch.setattr(
        "features.cache.experiment_service._execute_rows", different_rows
    )
    manager = _Manager()
    cache = _Cache()

    events = await _events(ReadysetExperimentService(manager, cache))

    assert any(
        isinstance(event, ErrorEvent) and "different result" in event.message
        for event in events
    )
    assert cache.statements[-1].startswith("DROP CACHE rdst_tmp_")


@pytest.mark.asyncio
async def test_unordered_result_rows_are_compared_as_a_bag(
    experiment_stubs, monkeypatch
):
    def rows(config, _query, _controller=None):
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
    def rows(config, _query, _controller=None):
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
    def rows(config, _query, _controller=None):
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

    async def rows(config, _query):
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
async def test_cancellation_during_create_waits_then_drops_under_lease(
    experiment_stubs
):
    manager = _Manager()
    create_started = threading.Event()
    allow_create = threading.Event()

    class BlockingCache(_Cache):
        def _run_readyset_sql(self, statement: str, **kwargs):
            if statement.startswith("CREATE"):
                self.statements.append(statement)
                create_started.set()
                allow_create.wait(timeout=2)
                return {"success": True}
            return super()._run_readyset_sql(statement, **kwargs)

    cache = BlockingCache(manager=manager)
    service = ReadysetExperimentService(manager, cache)

    async def consume():
        return await _events(service)

    task = asyncio.create_task(consume())
    await asyncio.to_thread(create_started.wait, 1)
    task.cancel()
    await asyncio.sleep(0)
    assert task.done() is False
    allow_create.set()
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
