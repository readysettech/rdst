"""Tests for Readyset cache benchmarking in fleet audit.

Covers the managed sandbox lifecycle invariant, the gating rules (capture
required, Docker required), and the readyset_comparison payload.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from features.audit.capture_service import CaptureService
from features.audit.events import WorkloadCompleteEvent
from features.audit.models import AuditMetrics, AuditResult
from features.audit.readyset_benchmark import (
    benchmark_queries,
    build_comparison,
    run_managed_benchmark,
)
from features.audit.service import AuditService
from features.cache.events import CacheAddEvent, CacheListEvent
from shared.deploy.lifecycle import ProbeResult, ProbeState, ephemeral_lifecycle
from shared.service_events import ErrorEvent


pytestmark = pytest.mark.usefixtures("run_blocking_inline")


RAW_RESULTS = [
    {
        "query_hash": "aaa111",
        "query_text": "SELECT id FROM users WHERE id = 1",
        "cacheable": True,
        "not_cacheable_reason": "",
        "baseline_ms": 100.0,
        "upstream_source": "capture_window",
        "readyset_ms": 10.0,
        "speedup": 10.0,
        "static_cacheable": True,
        "static_reason": None,
        "deep_supported": True,
        "deep_reason": None,
    },
    {
        "query_hash": "bbb222",
        "query_text": "SELECT count(*) FROM orders",
        "cacheable": True,
        "not_cacheable_reason": "",
        "baseline_ms": 200.0,
        "upstream_source": "capture_window",
        "readyset_ms": 10.0,
        "speedup": 20.0,
        "static_cacheable": True,
        "static_reason": None,
        "deep_supported": False,
        "deep_reason": "unsupported aggregate",
    },
    {
        "query_hash": "ccc333",
        "query_text": "SELECT NOW()",
        "cacheable": False,
        "not_cacheable_reason": "Uses non-deterministic NOW() function",
        "baseline_ms": 1.0,
        "upstream_source": "capture_window",
        "readyset_ms": 0,
        "speedup": 0,
        "static_cacheable": False,
        "static_reason": "Uses non-deterministic NOW() function",
        "deep_supported": None,
        "deep_reason": "Deep-cache support is pending",
    },
]


class _FakeConfig:
    def __init__(self, targets: dict):
        self._targets = targets

    def get(self, name):
        return self._targets.get(name)


def _fake_result(target: str) -> AuditResult:
    return AuditResult(
        target_name=target,
        engine="postgresql",
        host="db.example.com",
        metrics=AuditMetrics(max_connections=100),
        audited_at="2026-07-20T00:00:00+00:00",
    )


async def _fake_run_capture(self, **kwargs):
    yield WorkloadCompleteEvent(
        type="complete",
        success=True,
        run_id="r1",
        summary={
            "duration_seconds": kwargs["duration_seconds"],
            "queries": [
                {"query_hash": "aaa111", "query_text": "SELECT 1", "avg_time_ms": 5.0}
            ],
        },
        analysis=None,
    )


class _SandboxTracker:
    """Fake capacity-one manager that records lease ownership."""

    def __init__(self):
        self.active = 0
        self.max_active = 0
        self.log: list[str] = []
        self.dirty_reasons: list[str] = []
        self.start_count = 0

    async def start(self):
        self.start_count += 1

    async def stop(self):
        self.start_count -= 1

    @asynccontextmanager
    async def lease(self, *, target, owner_id, purpose, priority):
        assert owner_id.startswith(f"audit-benchmark-{target}-")
        assert purpose == "audit_benchmark"
        self.log.append(f"enter:{target}")
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        connection = SimpleNamespace(
            as_target_config=lambda: {
                "target_type": "readyset",
                "engine": "postgresql",
                "host": "127.0.0.1",
                "port": 5433,
                "database": "app",
                "user": "app",
                "password": "secret",
            }
        )
        async def mark_dirty(reason):
            self.dirty_reasons.append(reason)

        try:
            yield SimpleNamespace(connection=connection, mark_dirty=mark_dirty)
        finally:
            self.active -= 1
            self.log.append(f"exit:{target}")


class _BenchmarkCacheService:
    """Small CacheService fake that exposes SHOW CACHES state transitions."""

    def __init__(self, add_events, cache_snapshots, *, deep_result=None):
        self.add_events = list(add_events)
        self.cache_snapshots = list(cache_snapshots)
        self.deep_result = deep_result or {
            "success": True,
            "output": "readyset supported | yes",
        }
        self.list_calls = 0
        self.sql_calls: list[str] = []
        self.deleted: list[str] = []
        self.cache_config = {
            "target_type": "readyset",
            "engine": "postgresql",
            "host": "readyset.local",
            "port": 5433,
            "database": "app",
        }

    def _resolve_cache_target(self, target):
        return f"{target}-cache", self.cache_config

    def _connection_kwargs(self, cache_cfg):
        return {}

    def _run_readyset_sql(self, sql, **kwargs):
        self.sql_calls.append(sql)
        return self.deep_result

    async def add_cache(self, input_data, options):
        for event in self.add_events:
            yield event

    async def list_caches(self, input_data):
        index = min(self.list_calls, len(self.cache_snapshots) - 1)
        self.list_calls += 1
        caches = self.cache_snapshots[index] if self.cache_snapshots else []
        yield CacheListEvent(
            type="cache_list", success=True, caches=caches, count=len(caches),
        )

    async def delete_cache(self, input_data):
        self.deleted.append(input_data.cache_id)
        if False:
            yield None


def _run_query_benchmark(service, *, avg_time_ms=12.5, static_cacheable=True):
    """Run one benchmark query with ReadySet-only connections and no sleeps."""
    query = "SELECT id FROM users WHERE id = $1"
    connection = MagicMock()
    cursor = MagicMock()
    connection.cursor.return_value = cursor
    perf_values = iter([0.000, 0.010, 1.000, 1.020, 2.000, 2.030])

    with (
        patch("features.cache.service.CacheService", return_value=service),
        patch("shared.db_connection.create_direct_connection", return_value=connection) as connect,
        patch("features.audit.readyset_benchmark.time.sleep"),
        patch(
            "features.audit.readyset_benchmark.time.perf_counter",
            side_effect=lambda: next(perf_values),
        ),
        patch(
            "features.cache.readyset_cacheability.check_readyset_cacheability",
            return_value={
                "cacheable": static_cacheable,
                "issues": [] if static_cacheable else ["static false negative"],
                "warnings": [],
            },
        ),
    ):
        results = benchmark_queries(
            "prod",
            [{"query_hash": "abc123", "query_text": query, "avg_time_ms": avg_time_ms}],
        )
    return results, connection, cursor, connect


class TestBenchmarkCorrectness:
    @pytest.mark.parametrize(
        "add_event",
        [
            CacheAddEvent(
                type="cache_add", success=False, supported=True,
                query="SELECT 1", detail="creation failed",
            ),
            CacheAddEvent(
                type="cache_add", success=True, supported=False,
                query="SELECT 1", detail="unsupported join",
            ),
        ],
    )
    def test_timing_requires_success_and_supported(self, add_event):
        service = _BenchmarkCacheService([add_event], [[], []])
        results, _connection, cursor, _connect = _run_query_benchmark(service)

        assert results[0]["cacheable"] is False
        assert cursor.execute.call_count == 0
        # Initial inventory plus cleanup verification: unsupported results
        # never reach shallow verification or timing.
        assert service.list_calls == 3

    def test_error_event_from_add_cache_is_reported_and_not_timed(self):
        service = _BenchmarkCacheService(
            [ErrorEvent(type="error", message="ReadySet rejected CREATE", stage="add")],
            [[], []],
        )
        results, _connection, cursor, _connect = _run_query_benchmark(service)

        assert results[0]["cacheable"] is False
        assert results[0]["not_cacheable_reason"] == "ReadySet rejected CREATE"
        assert cursor.execute.call_count == 0

    def test_show_caches_must_confirm_shallow_before_timing(self):
        cache = {
            "cache_id": "q_deep",
            "query": "SELECT id FROM users WHERE id = $1",
            "type": "deep",
        }
        service = _BenchmarkCacheService(
            [CacheAddEvent(
                type="cache_add", success=True, supported=True,
                query=cache["query"],
            )],
            [[], [cache], [cache]],
        )
        results, _connection, cursor, _connect = _run_query_benchmark(service)

        assert results[0]["cacheable"] is False
        assert "not shallow" in results[0]["not_cacheable_reason"]
        assert cursor.execute.call_count == 0
        assert service.list_calls == 4

    def test_static_false_negative_and_pending_deep_do_not_gate_shallow(self):
        cache = {
            "cache_id": "q_shallow",
            "query": "SELECT id FROM users WHERE id = $1",
            "type": "shallow",
        }
        service = _BenchmarkCacheService(
            [CacheAddEvent(
                type="cache_add", success=True, supported=True,
                query=cache["query"],
            )],
            [[], [cache], [cache]],
            deep_result={
                "success": True,
                "output": "readyset supported | pending",
            },
        )
        results, _connection, cursor, connect = _run_query_benchmark(
            service, avg_time_ms=25.0, static_cacheable=False,
        )

        result = results[0]
        assert result["cacheable"] is True
        assert result["static_cacheable"] is False
        assert result["static_reason"] == "static false negative"
        assert result["deep_supported"] is None
        assert "pending" in result["deep_reason"].lower()
        assert service.sql_calls == [
            "EXPLAIN CREATE DEEP CACHE FROM SELECT id FROM users WHERE id = $1"
        ]
        assert cursor.execute.call_count == 3  # one warmup + two measured
        assert result["readyset_ms"] == 25.0
        assert result["speedup"] == 1.0
        assert all(call.args[0] == service.cache_config for call in connect.call_args_list)

    def test_failed_readyset_execution_is_not_reported_as_a_measurement(self):
        cache = {
            "cache_id": "q_shallow",
            "query": "SELECT id FROM users WHERE id = $1",
            "type": "shallow",
        }
        service = _BenchmarkCacheService(
            [CacheAddEvent(
                type="cache_add", success=True, supported=True,
                query=cache["query"],
            )],
            [[], [cache], [cache], []],
        )
        connection = MagicMock()
        connection.cursor.return_value.execute.side_effect = RuntimeError("query failed")

        with (
            patch("features.cache.service.CacheService", return_value=service),
            patch("shared.db_connection.create_direct_connection", return_value=connection),
            patch("features.audit.readyset_benchmark.time.sleep"),
            patch(
                "features.cache.readyset_cacheability.check_readyset_cacheability",
                return_value={"cacheable": True, "issues": [], "warnings": []},
            ),
        ):
            results = benchmark_queries(
                "prod",
                [{
                    "query_hash": "abc123",
                    "query_text": cache["query"],
                    "avg_time_ms": 25.0,
                }],
            )

        assert results[0]["cacheable"] is False
        assert results[0]["readyset_ms"] == 0
        assert results[0]["speedup"] == 0
        assert "query failed" in results[0]["not_cacheable_reason"]

    def test_upstream_ms_is_capture_average_passthrough(self):
        cache = {
            "cache_id": "q_shallow",
            "query": "SELECT id FROM users WHERE id = $1",
            "type": "shallow",
        }
        service = _BenchmarkCacheService(
            [CacheAddEvent(
                type="cache_add", success=True, supported=True,
                query=cache["query"],
            )],
            [[], [cache], [cache]],
        )
        results, _connection, _cursor, _connect = _run_query_benchmark(
            service, avg_time_ms=321.5,
        )
        payload = build_comparison(results)

        assert results[0]["baseline_ms"] == 321.5
        assert payload["queries"][0]["upstream_ms"] == 321.5
        assert payload["queries"][0]["upstream_source"] == "capture_window"


def _run_fleet(targets, *, benchmark_side_effect=None, docker=True):
    """Run audit_fleet with duration against fakes; returns (events, tracker)."""
    import asyncio

    tracker = _SandboxTracker()
    benchmark = MagicMock(return_value=list(RAW_RESULTS))
    if benchmark_side_effect is not None:
        benchmark.side_effect = benchmark_side_effect

    service = AuditService(
        config=_FakeConfig({name: {"engine": "postgresql"} for name in targets})
    )

    async def _collect():
        return [
            e async for e in service.audit_fleet(targets, duration_seconds=30)
        ]

    with (
        patch.object(AuditService, "audit_target", side_effect=lambda n, c, on_progress=None: _fake_result(n)),
        patch.object(CaptureService, "run_capture", _fake_run_capture),
        patch("features.audit.readyset_benchmark.docker_available", return_value=docker),
        patch("shared.deploy.sandbox_manager.sandbox_manager", tracker),
        patch("features.audit.readyset_benchmark.benchmark_queries", benchmark),
    ):
        events = asyncio.run(_collect())
    return events, tracker, benchmark


class TestLifecycleInvariant:
    @pytest.mark.asyncio
    async def test_all_capture_windows_overlap_before_any_benchmark(self):
        import asyncio

        targets = ["db1", "db2", "db3"]
        active_captures = 0
        max_active_captures = 0
        all_capturing = asyncio.Event()

        async def overlapping_capture(self, **kwargs):
            nonlocal active_captures, max_active_captures
            active_captures += 1
            max_active_captures = max(max_active_captures, active_captures)
            if active_captures == len(targets):
                all_capturing.set()
            try:
                await asyncio.wait_for(all_capturing.wait(), timeout=1)
                yield WorkloadCompleteEvent(
                    type="complete",
                    success=True,
                    run_id=f"r-{kwargs['target_name']}",
                    summary={"duration_seconds": 30, "queries": []},
                )
            finally:
                active_captures -= 1

        service = AuditService(config=_FakeConfig({
            name: {"engine": "postgresql"} for name in targets
        }))
        with (
            patch.object(
                AuditService, "audit_target",
                side_effect=lambda name, _config: _fake_result(name),
            ),
            patch.object(CaptureService, "run_capture", overlapping_capture),
            patch("features.audit.readyset_benchmark.docker_available", return_value=False),
        ):
            events = [
                event async for event in service.audit_fleet(
                    targets, duration_seconds=30,
                )
            ]

        assert max_active_captures == len(targets)
        assert [event.type for event in events[:4]] == [
            "status", "target_start", "target_start", "target_start",
        ]
        last_capture_complete = max(
            index for index, event in enumerate(events)
            if event.type == "status" and event.phase == "capture_complete"
        )
        first_readyset = min(
            index for index, event in enumerate(events)
            if event.type == "status" and event.phase == "readyset"
        )
        assert last_capture_complete < first_readyset

    @pytest.mark.asyncio
    async def test_cancelling_fleet_audit_cancels_active_captures(self):
        import asyncio

        capture_started = asyncio.Event()
        capture_stopped = asyncio.Event()

        async def blocking_capture(self, **kwargs):
            capture_started.set()
            try:
                await asyncio.Event().wait()
                if False:
                    yield None
            finally:
                capture_stopped.set()

        service = AuditService(
            config=_FakeConfig({"db1": {"engine": "postgresql"}})
        )

        async def consume():
            return [
                event async for event in service.audit_fleet(
                    ["db1"], duration_seconds=30,
                )
            ]

        with (
            patch.object(AuditService, "audit_target", return_value=_fake_result("db1")),
            patch.object(CaptureService, "run_capture", blocking_capture),
            patch("features.audit.readyset_benchmark.docker_available", return_value=False),
        ):
            task = asyncio.create_task(consume())
            await asyncio.wait_for(capture_started.wait(), timeout=1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert capture_stopped.is_set()

    def test_sequential_single_container_with_teardown_between_targets(self):
        targets = ["db1", "db2", "db3"]
        events, tracker, _ = _run_fleet(targets)

        # One lease at a time, released before the next target starts.
        assert tracker.max_active == 1
        assert tracker.log == [
            "enter:db1", "exit:db1",
            "enter:db2", "exit:db2",
            "enter:db3", "exit:db3",
        ]
        assert tracker.active == 0
        assert tracker.start_count == 0

        completes = [e for e in events if e.type == "target_complete"]
        assert [e.target_name for e in completes] == targets
        for e in completes:
            assert e.result["readyset_comparison"]["queries_tested"] == 3

    def test_lease_release_on_mid_run_benchmark_failure(self):
        calls = {"n": 0}

        def flaky_benchmark(
            target,
            queries,
            max_queries=20,
            console=None,
            cache_config=None,
            cleanup_errors=None,
        ):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("benchmark blew up")
            return list(RAW_RESULTS)

        targets = ["db1", "db2", "db3"]
        events, tracker, _ = _run_fleet(
            targets, benchmark_side_effect=flaky_benchmark,
        )

        # Every lease was released, including the one whose benchmark raised,
        # and the fleet run still completed.
        assert tracker.log == [
            "enter:db1", "exit:db1",
            "enter:db2", "exit:db2",
            "enter:db3", "exit:db3",
        ]
        assert tracker.active == 0

        completes = {e.target_name: e for e in events if e.type == "target_complete"}
        assert set(completes) == set(targets)
        assert "readyset_comparison" not in completes["db2"].result
        assert "readyset_comparison" in completes["db1"].result
        assert "readyset_comparison" in completes["db3"].result
        failure_notice = next(
            event for event in events
            if event.type == "status"
            and event.phase == "readyset"
            and event.target_name == "db2"
            and event.step == "failed"
        )
        assert failure_notice.message == (
            "db2: Readyset benchmark could not run: benchmark blew up"
        )
        assert events[-1].type == "complete" and events[-1].success is True

    @pytest.mark.asyncio
    async def test_cancelled_benchmark_keeps_lease_until_worker_stops(
        self, monkeypatch
    ):
        import asyncio
        import functools
        import threading

        from shared.async_utils import start_blocking

        tracker = _SandboxTracker()
        worker_started = threading.Event()
        worker_release = threading.Event()

        async def threaded(func, /, *args, **kwargs):
            call = functools.partial(func, *args, **kwargs)
            future = start_blocking(call)
            while not future.done():
                await asyncio.sleep(0.01)
            return future.result()

        def blocking_benchmark(*args, **kwargs):
            worker_started.set()
            worker_release.wait(timeout=2)
            raise RuntimeError("worker failed during cancellation")

        monkeypatch.setattr(asyncio, "to_thread", threaded)
        with (
            patch("features.audit.readyset_benchmark.docker_available", return_value=True),
            patch("shared.deploy.sandbox_manager.sandbox_manager", tracker),
            patch(
                "features.audit.readyset_benchmark.benchmark_queries",
                side_effect=blocking_benchmark,
            ),
        ):
            task = asyncio.create_task(
                run_managed_benchmark("db1", [{"query_text": "SELECT 1"}])
            )
            for _ in range(100):
                if worker_started.is_set():
                    break
                await asyncio.sleep(0.01)
            assert worker_started.is_set()

            task.cancel()
            await asyncio.sleep(0.05)
            assert tracker.active == 1

            worker_release.set()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert tracker.active == 0
        assert tracker.log == ["enter:db1", "exit:db1"]
        assert tracker.dirty_reasons == ["Audit benchmark cache cleanup failed"]

    @pytest.mark.asyncio
    async def test_managed_benchmark_passes_lease_connection_config(self):
        tracker = _SandboxTracker()
        benchmark = MagicMock(return_value=list(RAW_RESULTS))

        with (
            patch("features.audit.readyset_benchmark.docker_available", return_value=True),
            patch("shared.deploy.sandbox_manager.sandbox_manager", tracker),
            patch("features.audit.readyset_benchmark.benchmark_queries", benchmark),
        ):
            outcome = await run_managed_benchmark("db1", [{"query_text": "SELECT 1"}])

        assert outcome == {"status": "ok", "results": RAW_RESULTS}
        assert benchmark.call_args.kwargs["cache_config"] == {
            "target_type": "readyset",
            "engine": "postgresql",
            "host": "127.0.0.1",
            "port": 5433,
            "database": "app",
            "user": "app",
            "password": "secret",
        }
        assert tracker.log == ["enter:db1", "exit:db1"]
        assert tracker.start_count == 0

    @pytest.mark.asyncio
    async def test_cleanup_failure_marks_sandbox_dirty_and_preserves_results(self):
        tracker = _SandboxTracker()

        def benchmark(*args, cleanup_errors, **kwargs):
            cleanup_errors.append("DROP CACHE failed")
            return list(RAW_RESULTS)

        with (
            patch("features.audit.readyset_benchmark.docker_available", return_value=True),
            patch("shared.deploy.sandbox_manager.sandbox_manager", tracker),
            patch(
                "features.audit.readyset_benchmark.benchmark_queries",
                side_effect=benchmark,
            ),
        ):
            outcome = await run_managed_benchmark(
                "db1", [{"query_text": "SELECT 1"}]
            )

        assert outcome["status"] == "ok"
        assert outcome["results"] == RAW_RESULTS
        assert outcome["cleanup_warning"] == "DROP CACHE failed"
        assert tracker.dirty_reasons == ["Audit benchmark cache cleanup failed"]


class TestFreshEphemeralLifecycle:
    def test_stale_container_is_removed_before_deploy_and_again_on_exit(self):
        stale = ProbeResult(
            state=ProbeState.DEPLOYED_RUNNING,
            mode="docker",
            container_id="rdst-readyset-db1",
        )
        absent = ProbeResult(
            state=ProbeState.NOT_DEPLOYED,
            mode="docker",
            container_id="rdst-readyset-db1",
        )
        deploy_result = SimpleNamespace(ok=True, message="deployed")

        with (
            patch("shared.deploy.lifecycle.probe", side_effect=[stale, absent]),
            patch(
                "shared.deploy.lifecycle._cleanup_ephemeral_deploy",
                return_value=None,
            ) as cleanup,
            patch(
                "shared.deploy.lifecycle._wait_for_cache_ready",
                return_value=True,
            ),
            patch("features.cache.cli.deploy.DeployCommand") as deploy_command,
        ):
            deploy_command.return_value.execute.return_value = deploy_result
            with ephemeral_lifecycle("db1", fresh=True) as outcome:
                assert outcome.error is None
                assert outcome.removed_stale is True
                assert outcome.we_deployed is True

        assert cleanup.call_count == 2
        deploy_command.return_value.execute.assert_called_once_with(
            target="db1", mode="docker", quiet=True,
        )


class TestFleetSnapshotAggregates:
    @pytest.mark.asyncio
    async def test_saved_snapshot_rolls_up_successful_target_values(
        self, tmp_rdst_home
    ):
        service = AuditService(
            config=_FakeConfig({name: {"engine": "postgresql"} for name in ("db1", "db2", "db3")})
        )

        def audit_target(name, _config, on_progress=None):
            if name == "db3":
                return AuditResult(
                    target_name=name,
                    engine="postgresql",
                    host="db.example.com",
                    error="unreachable",
                )
            result = _fake_result(name)
            if name == "db1":
                result.sizing = {
                    "current_monthly_cost_usd": 300.0,
                    "potential_savings_usd": 75.0,
                }
                result.cache_opportunity = {"score": 80}
            else:
                result.sizing = {
                    "current_monthly_cost_usd": 125.5,
                    "potential_savings_usd": None,
                }
                result.cache_opportunity = {"score": 40}
            return result

        with patch.object(AuditService, "audit_target", side_effect=audit_target):
            events = [
                event
                async for event in service.audit_fleet(
                    ["db1", "db2", "db3"], save_name="fleet_rollup"
                )
            ]

        assert events[-1].success is True
        snapshot = json.loads(
            (tmp_rdst_home / "fleet" / "snapshots" / "fleet_rollup.json").read_text()
        )
        assert snapshot["total_monthly_cost_usd"] == 425.5
        assert snapshot["potential_savings_usd"] == 75.0
        assert snapshot["avg_cache_opportunity"] == 60.0


class TestGating:
    @pytest.mark.asyncio
    async def test_no_duration_means_no_benchmark(self):
        service = AuditService(config=_FakeConfig({"db1": {"engine": "postgresql"}}))
        with (
            patch.object(AuditService, "audit_target", return_value=_fake_result("db1")),
            patch.object(
                AuditService, "run_readyset_comparison",
                side_effect=AssertionError("benchmark should not run"),
            ),
        ):
            events = [e async for e in service.audit_fleet(["db1"])]
        complete = next(e for e in events if e.type == "target_complete")
        assert "readyset_comparison" not in complete.result

    @pytest.mark.asyncio
    async def test_no_captured_queries_means_no_benchmark(self):
        async def capture_without_queries(self, **kwargs):
            yield WorkloadCompleteEvent(
                type="complete", success=True, run_id="r1",
                summary={"duration_seconds": 30, "queries": []},
            )

        service = AuditService(config=_FakeConfig({"db1": {"engine": "postgresql"}}))
        with (
            patch.object(AuditService, "audit_target", return_value=_fake_result("db1")),
            patch.object(CaptureService, "run_capture", capture_without_queries),
            patch("features.audit.readyset_benchmark.docker_available", return_value=True),
            patch.object(
                AuditService, "run_readyset_comparison",
                side_effect=AssertionError("benchmark should not run"),
            ),
        ):
            events = [
                e async for e in service.audit_fleet(["db1"], duration_seconds=30)
            ]
        complete = next(e for e in events if e.type == "target_complete")
        assert "readyset_comparison" not in complete.result

    def test_docker_unavailable_emits_notice_not_failure(self):
        events, tracker, benchmark = _run_fleet(["db1"], docker=False)

        assert tracker.log == []  # no container ever started
        benchmark.assert_not_called()

        notices = [
            e for e in events
            if e.type == "status" and getattr(e, "phase", "") == "readyset"
        ]
        assert len(notices) == 1
        assert "db1" in notices[0].message
        assert "Docker" in notices[0].message

        complete = next(e for e in events if e.type == "target_complete")
        assert "readyset_comparison" not in complete.result
        assert events[-1].type == "complete" and events[-1].success is True


class TestComparisonPayload:
    def test_build_comparison_shape(self):
        comparison = build_comparison(RAW_RESULTS)
        assert comparison["queries_tested"] == 3
        assert comparison["supported_count"] == 2
        assert comparison["unsupported_count"] == 1
        assert comparison["deep_supported_count"] == 1
        assert comparison["deep_unsupported_count"] == 1
        assert comparison["deep_unknown_count"] == 1
        assert comparison["avg_speedup"] == 15.0
        assert len(comparison["queries"]) == 3

        supported = comparison["queries"][0]
        assert supported == {
            "query_hash": "aaa111",
            "query_text": "SELECT id FROM users WHERE id = 1",
            "supported": True,
            "reason": None,
            "upstream_ms": 100.0,
            "upstream_source": "capture_window",
            "readyset_ms": 10.0,
            "speedup": 10.0,
            "static_cacheable": True,
            "static_reason": None,
            "deep_supported": True,
            "deep_reason": None,
        }
        unsupported = comparison["queries"][2]
        assert unsupported["supported"] is False
        assert unsupported["reason"] == "Uses non-deterministic NOW() function"

    def test_no_supported_queries_yields_zero_avg(self):
        comparison = build_comparison([RAW_RESULTS[2]])
        assert comparison["avg_speedup"] == 0.0
        assert comparison["supported_count"] == 0
        assert comparison["unsupported_count"] == 1


class TestCaptureReadysetParity:
    """POST /audit/capture parity: run_capture(readyset_testing=True) exposes
    the same readyset_comparison section in the complete event's summary."""

    def _run_capture(self, monkeypatch, *, readyset_testing, docker=True):
        import asyncio
        import itertools

        from features.audit import capture_service as capture_module
        from features.audit.models import DatabaseSnapshot

        end_rows = [{
            "queryid": "1",
            "query": "SELECT id FROM users WHERE id = 1",
            "calls": 5,
            "total_exec_time": 50.0,
            "mean_exec_time": 10.0,
            "max_exec_time": 12.0,
            "shared_blks_hit": 0,
            "shared_blks_read": 0,
            "rows": 5,
        }]
        snapshot = DatabaseSnapshot(timestamp="t", cache_hit_ratio=0.9, active_connections=1)

        fake_time = MagicMock()
        fake_time.monotonic.side_effect = itertools.count(0, 100)

        service = CaptureService(
            config=_FakeConfig({"mydb": {"engine": "postgresql", "host": "localhost"}})
        )

        async def _collect():
            return [
                e async for e in service.run_capture(
                    "mydb",
                    duration_seconds=10,
                    run_analysis=False,
                    save_top_queries=0,
                    save_capture=False,
                    readyset_testing=readyset_testing,
                )
            ]

        with (
            patch("shared.db_connection.create_direct_connection", return_value=MagicMock()),
            patch.object(capture_module, "time", fake_time),
            patch.object(
                capture_module.query_stats_module, "collect_database_snapshot",
                return_value=snapshot,
            ),
            patch.object(
                capture_module.query_stats_module, "collect_table_stats", return_value=[],
            ),
            patch.object(
                capture_module.query_stats_module, "collect_pg_stat_statements",
                side_effect=[[], end_rows],
            ),
            patch.object(
                capture_module.query_stats_module, "compute_snapshot_delta",
                return_value={},
            ),
            patch(
                "features.audit.readyset_benchmark.docker_available",
                return_value=docker,
            ),
            patch(
                "features.audit.readyset_benchmark.run_managed_benchmark",
                new_callable=AsyncMock,
                return_value={"status": "ok", "results": list(RAW_RESULTS)},
            ) as bench,
        ):
            events = asyncio.run(_collect())
        return events, bench

    def test_readyset_flag_adds_comparison_to_summary(
        self, tmp_rdst_home, monkeypatch
    ):
        events, bench = self._run_capture(monkeypatch, readyset_testing=True)
        complete = events[-1]
        assert complete.type == "complete" and complete.success is True
        comparison = complete.summary["readyset_comparison"]
        assert comparison["queries_tested"] == 3
        assert comparison["supported_count"] == 2
        assert {"upstream_ms", "readyset_ms", "speedup"} <= set(comparison["queries"][0])
        bench.assert_called_once()
        assert bench.call_args.args[0] == "mydb"

    def test_flag_off_keeps_summary_unchanged(self, tmp_rdst_home, monkeypatch):
        events, bench = self._run_capture(monkeypatch, readyset_testing=False)
        complete = events[-1]
        assert complete.type == "complete"
        assert "readyset_comparison" not in complete.summary
        bench.assert_not_called()

    def test_docker_unavailable_emits_notice(self, tmp_rdst_home, monkeypatch):
        events, bench = self._run_capture(
            monkeypatch, readyset_testing=True, docker=False,
        )
        complete = events[-1]
        assert complete.type == "complete"
        assert "readyset_comparison" not in complete.summary
        bench.assert_not_called()
        notices = [
            e for e in events
            if e.type == "status" and getattr(e, "phase", "") == "readyset"
        ]
        assert len(notices) == 1
        assert "Docker" in notices[0].message
