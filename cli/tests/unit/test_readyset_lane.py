"""The sandbox side of a load test's Readyset lane.

The lease is held for the whole run, the caches the run creates are dropped
before the next holder sees the sandbox, and a sandbox that cannot be leased
degrades the run to its origin lane instead of failing it.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from features.cache.experiment_service import COLD_CREATE_CACHE_TIMEOUT_MS
from features.query_registry import readyset_lane
from features.query_registry.readyset_lane import (
    ReadysetEndpoint,
    benchmark_sandbox,
    prepare_lane_caches,
    unavailable_detail,
)

SANDBOX_CONFIG = {
    "engine": "postgresql",
    "host": "127.0.0.1",
    "port": 5433,
    "database": "sandbox",
    "user": "readyset",
    "password": "",
    "target_type": "readyset",
    "upstream_target": "demo",
}


class _Connection:
    def as_target_config(self):
        return dict(SANDBOX_CONFIG)


class _Lease:
    def __init__(self):
        self.connection = _Connection()
        self.dirty: list[str] = []

    async def mark_dirty(self, reason):
        self.dirty.append(reason)


class _Manager:
    def __init__(self, lease_error: Exception | None = None):
        self.lease_error = lease_error
        self.lease_obj = _Lease()
        self.reservations: list[dict] = []
        self.leases: list[dict] = []

    @asynccontextmanager
    async def reserve_measurement(self, **kwargs):
        self.reservations.append(kwargs)
        yield None

    @asynccontextmanager
    async def lease(self, **kwargs):
        self.leases.append(kwargs)
        if self.lease_error is not None:
            raise self.lease_error
        yield self.lease_obj


class _Readyset:
    """Records the DDL a run sends the sandbox and answers as told."""

    def __init__(self, failures: dict[str, str] | None = None):
        self.statements: list[tuple[str, int | None]] = []
        self.failures = failures or {}

    def _run_readyset_sql(self, sql, *, statement_timeout_ms=None, **connection):
        del connection
        self.statements.append((sql, statement_timeout_ms))
        for needle, error in self.failures.items():
            if needle in sql:
                return {"success": False, "error": error}
        return {"success": True, "output": ""}


@pytest.fixture()
def readyset(monkeypatch):
    service = _Readyset()
    monkeypatch.setattr(
        "features.cache.service.CacheService", lambda *a, **k: service
    )
    return service


class TestBenchmarkSandbox:
    @pytest.mark.asyncio
    async def test_origin_only_run_reserves_without_leasing(self):
        manager = _Manager()

        async with benchmark_sandbox(
            target="demo", owner_id="run", lanes=["origin"], manager=manager
        ) as endpoint:
            assert endpoint is None

        assert manager.leases == []
        assert manager.reservations == [
            {"owner_id": "run", "purpose": "load_test"}
        ]

    @pytest.mark.asyncio
    async def test_readyset_run_leases_the_target_sandbox(self):
        manager = _Manager()

        async with benchmark_sandbox(
            target="demo",
            owner_id="run",
            lanes=["origin", "readyset"],
            manager=manager,
        ) as endpoint:
            assert endpoint.available
            assert endpoint.config["target_type"] == "readyset"

        assert manager.leases == [
            {"target": "demo", "owner_id": "run", "purpose": "load_test"}
        ]
        assert manager.reservations == []

    @pytest.mark.asyncio
    async def test_an_unavailable_sandbox_still_reserves_the_measurement(self):
        manager = _Manager(lease_error=RuntimeError("Docker is not running"))

        async with benchmark_sandbox(
            target="demo",
            owner_id="run",
            lanes=["origin", "readyset"],
            manager=manager,
        ) as endpoint:
            assert endpoint.status == "unavailable"
            assert endpoint.detail == "Docker is not running"

        assert manager.reservations == [
            {"owner_id": "run", "purpose": "load_test"}
        ]

    @pytest.mark.asyncio
    async def test_the_runs_caches_are_dropped_before_the_lease_ends(
        self, readyset
    ):
        manager = _Manager()

        async with benchmark_sandbox(
            target="demo",
            owner_id="run",
            lanes=["readyset"],
            manager=manager,
        ) as endpoint:
            endpoint.note_cache("rdst_tmp_run_abc")
            endpoint.note_cache("rdst_tmp_run_def")

        assert [sql for sql, _ in readyset.statements] == [
            "DROP CACHE rdst_tmp_run_abc",
            "DROP CACHE rdst_tmp_run_def",
        ]
        assert manager.lease_obj.dirty == []

    @pytest.mark.asyncio
    async def test_a_cache_the_sandbox_keeps_marks_it_dirty(self, monkeypatch):
        service = _Readyset(failures={"DROP CACHE": "cache is busy"})
        monkeypatch.setattr(
            "features.cache.service.CacheService", lambda *a, **k: service
        )
        manager = _Manager()

        async with benchmark_sandbox(
            target="demo",
            owner_id="run",
            lanes=["readyset"],
            manager=manager,
        ) as endpoint:
            endpoint.note_cache("rdst_tmp_run_abc")

        assert manager.lease_obj.dirty == [
            "Load test cache cleanup failed for 1 cache(s)"
        ]

    @pytest.mark.asyncio
    async def test_a_failing_run_still_releases_the_lease_and_caches(
        self, readyset
    ):
        manager = _Manager()

        with pytest.raises(RuntimeError, match="run exploded"):
            async with benchmark_sandbox(
                target="demo",
                owner_id="run",
                lanes=["readyset"],
                manager=manager,
            ) as endpoint:
                endpoint.note_cache("rdst_tmp_run_abc")
                raise RuntimeError("run exploded")

        assert [sql for sql, _ in readyset.statements] == [
            "DROP CACHE rdst_tmp_run_abc"
        ]


class TestPrepareLaneCaches:
    def test_each_query_gets_a_cache_with_the_cold_create_timeout(
        self, readyset
    ):
        endpoint = ReadysetEndpoint(config=dict(SANDBOX_CONFIG))

        refused = prepare_lane_caches(
            endpoint,
            "0123456789abcdef",
            [("one", "SELECT 1"), ("two", "SELECT 2")],
        )

        assert refused == {}
        assert len(endpoint.created_caches()) == 2
        assert all(
            sql.startswith("CREATE CACHE rdst_tmp_")
            and timeout == COLD_CREATE_CACHE_TIMEOUT_MS
            for sql, timeout in readyset.statements
        )
        assert [sql.split(" FROM ")[1] for sql, _ in readyset.statements] == [
            "SELECT 1",
            "SELECT 2",
        ]

    def test_a_refused_cache_is_reported_and_not_recorded(self, monkeypatch):
        service = _Readyset(failures={"SELECT 2": "unsupported placeholder"})
        monkeypatch.setattr(
            "features.cache.service.CacheService", lambda *a, **k: service
        )
        endpoint = ReadysetEndpoint(config=dict(SANDBOX_CONFIG))

        refused = prepare_lane_caches(
            endpoint,
            "0123456789abcdef",
            [("one", "SELECT 1"), ("two", "SELECT 2")],
        )

        assert refused == {"two": "unsupported placeholder"}
        assert len(endpoint.created_caches()) == 1

    def test_without_a_sandbox_every_query_is_refused(self):
        endpoint = ReadysetEndpoint(status="unavailable", detail="none")

        refused = prepare_lane_caches(
            endpoint, "owner", [("one", "SELECT 1")]
        )

        assert set(refused) == {"one"}


def test_unavailable_detail_falls_back_to_the_exception_kind():
    assert unavailable_detail(TimeoutError()).endswith("(TimeoutError).")
    assert unavailable_detail(RuntimeError("  no  docker ")) == "no docker"


def test_the_module_names_one_traffic_tag_per_lane():
    assert readyset_lane.LANE_TAGS == {
        "origin": "rdst/loadtest",
        "readyset": "rdst/loadtest-readyset",
    }
