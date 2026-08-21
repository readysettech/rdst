"""A load test measures the origin and Readyset side by side.

The Readyset lane is additive: an origin-only request runs exactly as it
always has, and a run that asks for the second lane keeps its origin
measurement whatever the sandbox does.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from features.query_registry.api.routes import _progress_to_sse
from features.query_registry.readyset_lane import (
    SKIP_READYSET_CACHE_FAILED,
    ReadysetEndpoint,
    normalize_lanes,
)
from features.query_registry.service import QueryService

pytestmark = pytest.mark.usefixtures("run_executor_inline")

ORIGIN_CONFIG = {"engine": "postgresql", "host": "127.0.0.1"}
SANDBOX_CONFIG = {
    "engine": "postgresql",
    "host": "127.0.0.1",
    "port": 5433,
    "database": "sandbox",
    "user": "readyset",
    "password": "",
    "target_type": "readyset",
}


class _FakeCursor:
    def __init__(self, conn: "_FakeConnection") -> None:
        self._conn = conn

    def execute(self, sql: str) -> None:
        self._conn.executed.append(sql)
        if sql.strip().lower().startswith("set ") and self._conn.refuse_set:
            raise RuntimeError("SET is not supported here")

    def fetchall(self):
        return []

    def close(self) -> None:
        pass


class _FakeConnection:
    def __init__(self, refuse_set: bool = False) -> None:
        self.executed: list[str] = []
        self.refuse_set = refuse_set

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self)

    def close(self) -> None:
        pass


class _FakeTargetsConfig:
    def load(self) -> None:
        pass

    def get_default(self) -> str:
        return "demo"

    def get(self, name: str) -> dict:
        del name
        return dict(ORIGIN_CONFIG)


class _Endpoints:
    """Hands each lane its own connections, keyed by the config it dials."""

    def __init__(self, refuse_readyset_set: bool = False) -> None:
        self.refuse_readyset_set = refuse_readyset_set
        self.opened: list[tuple[str, str, _FakeConnection]] = []

    def create(self, config, *, lane=None):
        endpoint = (
            "readyset" if config.get("target_type") == "readyset" else "origin"
        )
        connection = _FakeConnection(
            refuse_set=endpoint == "readyset" and self.refuse_readyset_set
        )
        self.opened.append((endpoint, lane, connection))
        return connection

    def sql(self, endpoint: str) -> list[str]:
        return [
            statement
            for opened, _lane, connection in self.opened
            if opened == endpoint
            for statement in connection.executed
            if not statement.lower().startswith("set ")
        ]


async def _run(endpoints: _Endpoints, *, refused=None, **kwargs):
    """Drive one benchmark to completion against the fake endpoints."""
    run = {
        "target": "demo",
        "mode": "interval",
        "interval_ms": 0,
        "concurrency": 1,
        "duration_seconds": 1,
        "max_count": 4,
        "warmup_executions": 1,
        **kwargs,
    }
    events = []
    with (
        patch(
            "shared.db_connection.create_direct_connection",
            side_effect=endpoints.create,
        ),
        patch(
            "shared.config.targets.create_targets_config",
            return_value=_FakeTargetsConfig(),
        ),
        patch(
            "features.query_registry.service.prepare_lane_caches",
            return_value=dict(refused or {}),
        ),
    ):
        async for event in QueryService().stream_benchmark(**run):
            events.append(event)
    return events


def _sandbox() -> ReadysetEndpoint:
    return ReadysetEndpoint(config=dict(SANDBOX_CONFIG))


class TestLaneValidation:
    def test_normalize_orders_and_deduplicates(self):
        assert normalize_lanes(["readyset", "origin", "origin"]) == (
            "origin",
            "readyset",
        )

    def test_absent_lanes_mean_origin(self):
        assert normalize_lanes(None) == ("origin",)

    @pytest.mark.parametrize("lanes", [["cache"], ["origin", "upstream"], []])
    def test_unusable_lane_lists_are_refused(self, lanes):
        with pytest.raises(ValueError):
            normalize_lanes(lanes)

    @pytest.mark.asyncio
    async def test_unknown_lane_rejects_the_run(self):
        events = await _run(
            _Endpoints(),
            queries=[{"identifier": "q", "sql": "SELECT 1"}],
            lanes=["origin", "cache"],
        )

        assert len(events) == 1
        assert events[0].type == "error"
        assert events[0].code == "benchmark_lane_invalid"
        assert "cache" in events[0].message


class TestOriginOnlyIsUnchanged:
    @pytest.mark.asyncio
    async def test_a_run_without_lanes_reports_no_lane_fields(self):
        endpoints = _Endpoints()
        events = await _run(
            endpoints, queries=[{"identifier": "q", "sql": "SELECT 1"}]
        )

        complete = events[-1]
        assert complete.type == "complete"
        assert complete.total_executions == 4
        assert complete.lanes_run == ["origin"]
        assert complete.readyset_setup is None
        assert complete.queries[0].lanes is None
        assert {endpoint for endpoint, _, _ in endpoints.opened} == {"origin"}

    @pytest.mark.asyncio
    async def test_the_payload_keeps_the_shape_it_always_had(self):
        events = await _run(
            _Endpoints(), queries=[{"identifier": "q", "sql": "SELECT 1"}]
        )

        import json

        payload = json.loads(_progress_to_sse(events[-1])["data"])
        assert "lanes" not in payload["queries"][0]
        assert "readyset_setup" not in payload
        assert payload["lanes_run"] == ["origin"]
        # last_error is unset on a clean run and still reported as null.
        assert payload["queries"][0]["last_error"] is None


class TestBothLanes:
    @pytest.mark.asyncio
    async def test_each_lane_measures_the_same_workload(self):
        endpoints = _Endpoints()
        events = await _run(
            endpoints,
            queries=[{"identifier": "q", "sql": "SELECT 1"}],
            lanes=["origin", "readyset"],
            readyset=_sandbox(),
        )

        complete = events[-1]
        assert complete.type == "complete"
        assert complete.lanes_run == ["origin", "readyset"]
        assert complete.readyset_setup == {"status": "ok", "detail": ""}
        stats = complete.queries[0]
        # The top-level fields stay the origin lane's, for a reader that
        # knows nothing about lanes.
        assert set(stats.lanes) == {"origin", "readyset"}
        assert stats.lanes["origin"]["executions"] == stats.executions == 4
        assert stats.lanes["readyset"]["executions"] == 4
        assert "lanes" not in stats.lanes["origin"]
        assert len(endpoints.sql("origin")) == len(endpoints.sql("readyset"))

    @pytest.mark.asyncio
    async def test_each_lane_gets_its_own_pool_and_traffic_tag(self):
        """Equal pools per lane: neither lane's clients queue behind the other."""
        endpoints = _Endpoints()
        await _run(
            endpoints,
            queries=[{"identifier": "q", "sql": "SELECT 1"}],
            mode="concurrency",
            concurrency=3,
            max_count=12,
            lanes=["origin", "readyset"],
            readyset=_sandbox(),
        )

        opened = [(endpoint, lane) for endpoint, lane, _ in endpoints.opened]
        assert opened.count(("origin", "rdst/loadtest")) == 3
        assert opened.count(("readyset", "rdst/loadtest-readyset")) == 3

    @pytest.mark.asyncio
    async def test_evidence_is_written_under_one_tag_per_lane(self, monkeypatch):
        recorded: list[tuple[str, str]] = []

        class Writer:
            def __init__(self, target, *, lane):
                self.entry = (target, lane)

            def record(self, executions, *, run_id, started_at, ended_at):
                del executions, run_id, started_at, ended_at
                recorded.append(self.entry)

            def close(self):
                return None

        monkeypatch.setattr(
            "features.query_registry.service.ExecutionEvidenceWriter", Writer
        )
        await _run(
            _Endpoints(),
            queries=[{"identifier": "q", "sql": "SELECT 1"}],
            lanes=["origin", "readyset"],
            readyset=_sandbox(),
        )

        assert set(recorded) == {
            ("demo", "rdst/loadtest"),
            ("demo", "rdst/loadtest-readyset"),
        }


class TestReadysetLaneFallback:
    @pytest.mark.asyncio
    async def test_a_missing_sandbox_leaves_an_origin_only_run(self):
        endpoints = _Endpoints()
        events = await _run(
            endpoints,
            queries=[{"identifier": "q", "sql": "SELECT 1"}],
            lanes=["origin", "readyset"],
            readyset=ReadysetEndpoint(
                status="unavailable", detail="Docker is not running"
            ),
        )

        complete = events[-1]
        assert complete.type == "complete"
        assert complete.total_successes == 4
        assert complete.lanes_run == ["origin"]
        assert complete.readyset_setup == {
            "status": "unavailable",
            "detail": "Docker is not running",
        }
        assert complete.queries[0].lanes is None
        assert {endpoint for endpoint, _, _ in endpoints.opened} == {"origin"}

    @pytest.mark.asyncio
    async def test_an_unleased_sandbox_is_reported_rather_than_assumed(self):
        events = await _run(
            _Endpoints(),
            queries=[{"identifier": "q", "sql": "SELECT 1"}],
            lanes=["origin", "readyset"],
        )

        complete = events[-1]
        assert complete.lanes_run == ["origin"]
        assert complete.readyset_setup["status"] == "unavailable"

    @pytest.mark.asyncio
    async def test_a_readyset_lane_that_cannot_start_keeps_the_origin_run(self):
        """A sandbox session the run cannot make read-only costs that lane."""
        endpoints = _Endpoints(refuse_readyset_set=True)
        events = await _run(
            endpoints,
            queries=[{"identifier": "q", "sql": "SELECT 1"}],
            lanes=["origin", "readyset"],
            readyset=_sandbox(),
        )

        complete = events[-1]
        assert complete.type == "complete"
        assert complete.total_successes == 4
        assert complete.lanes_run == ["origin"]
        assert complete.readyset_setup["status"] == "unavailable"
        assert "read-only" in complete.readyset_setup["detail"]
        assert endpoints.sql("readyset") == []

    @pytest.mark.asyncio
    async def test_readyset_lane_alone_falls_back_to_the_origin(self):
        endpoints = _Endpoints()
        events = await _run(
            endpoints,
            queries=[{"identifier": "q", "sql": "SELECT 1"}],
            lanes=["readyset"],
        )

        complete = events[-1]
        assert complete.lanes_run == ["origin"]
        assert complete.total_successes == 4
        assert {endpoint for endpoint, _, _ in endpoints.opened} == {"origin"}


class TestUncacheableQueries:
    QUERIES = [
        {"identifier": "cached", "sql": "SELECT 1"},
        {"identifier": "refused", "sql": "SELECT 2"},
    ]

    @pytest.mark.asyncio
    async def test_a_query_readyset_will_not_cache_is_skipped_for_that_lane(self):
        endpoints = _Endpoints()
        events = await _run(
            endpoints,
            queries=self.QUERIES,
            lanes=["origin", "readyset"],
            readyset=_sandbox(),
            refused={"refused": "unsupported placeholder"},
        )

        complete = events[-1]
        assert complete.type == "complete"
        assert complete.lanes_run == ["origin", "readyset"]
        # The skip is the Readyset lane's alone: the run is not short a query.
        assert complete.skipped_count == 0
        assert [
            (skip.query_hash, skip.reason, skip.lanes)
            for skip in complete.skipped_queries
        ] == [
            (
                "refused",
                SKIP_READYSET_CACHE_FAILED,
                {"readyset": SKIP_READYSET_CACHE_FAILED},
            )
        ]

        by_hash = {stats.query_hash: stats for stats in complete.queries}
        assert set(by_hash["cached"].lanes) == {"origin", "readyset"}
        assert set(by_hash["refused"].lanes) == {"origin"}
        assert by_hash["refused"].executions > 0
        assert "SELECT 2" not in endpoints.sql("readyset")

    @pytest.mark.asyncio
    async def test_a_lane_skip_reaches_the_client_tagged(self):
        import json

        events = await _run(
            _Endpoints(),
            queries=self.QUERIES,
            lanes=["origin", "readyset"],
            readyset=_sandbox(),
            refused={"refused": "unsupported placeholder"},
        )

        payload = json.loads(_progress_to_sse(events[-1])["data"])
        assert payload["skipped_count"] == 0
        assert payload["skipped_queries"] == [
            {
                "query_hash": "refused",
                "query_name": "refused",
                "reason": SKIP_READYSET_CACHE_FAILED,
                "lanes": {"readyset": SKIP_READYSET_CACHE_FAILED},
            }
        ]

    @pytest.mark.asyncio
    async def test_no_cacheable_query_leaves_an_origin_only_run(self):
        endpoints = _Endpoints()
        events = await _run(
            endpoints,
            queries=self.QUERIES,
            lanes=["origin", "readyset"],
            readyset=_sandbox(),
            refused={"cached": "no", "refused": "no"},
        )

        complete = events[-1]
        assert complete.lanes_run == ["origin"]
        assert complete.readyset_setup["status"] == "unavailable"
        assert {endpoint for endpoint, _, _ in endpoints.opened} == {"origin"}
