"""Tests for detached Query Load Test startup and reservation behavior."""

from contextlib import asynccontextmanager

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from features.query_registry.api import routes
from shared.api.target_guard import TargetGuard


def _http_request(**headers: str) -> Request:
    """A loopback POST, or a remote one once a source header is supplied."""
    raw = [(b"host", b"127.0.0.1:8787")]
    raw += [(name.encode(), value.encode()) for name, value in headers.items()]
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/query-registry/load-test-runs",
            "scheme": "http",
            "query_string": b"",
            "headers": raw,
            "client": ("127.0.0.1", 54321),
            "server": ("127.0.0.1", 8787),
        }
    )


class _Registry:
    def __init__(
        self,
        existing: str | None = None,
        existing_fingerprint: str | None = None,
    ):
        self.existing = existing
        self.existing_fingerprint = existing_fingerprint
        self.started: list[tuple] = []
        self.match_calls: list[tuple] = []

    def find_active_matching(self, kind, target, metadata, keys):
        self.match_calls.append((kind, target, metadata, keys))
        if (
            self.existing is not None
            and metadata["request_fingerprint"] == self.existing_fingerprint
        ):
            return self.existing
        return None

    def start_factory(self, kind, target, factory, metadata=None):
        self.started.append((kind, target, factory, metadata))
        return "load_test_origin_new"


class _Lease:
    def __init__(self, connection):
        self.connection = connection

    async def mark_dirty(self, reason):
        del reason


class _SandboxConnection:
    def as_target_config(self):
        return {
            "engine": "postgresql",
            "host": "127.0.0.1",
            "port": 5433,
            "database": "sandbox",
            "user": "readyset",
            "password": "",
            "target_type": "readyset",
        }


class _Manager:
    def __init__(self, lease_error: Exception | None = None):
        self.reservations: list[dict] = []
        self.leases: list[dict] = []
        self.lease_error = lease_error

    @asynccontextmanager
    async def reserve_measurement(self, **kwargs):
        self.reservations.append(kwargs)
        yield

    @asynccontextmanager
    async def lease(self, **kwargs):
        self.leases.append(kwargs)
        if self.lease_error is not None:
            raise self.lease_error
        yield _Lease(_SandboxConnection())


class _QueryService:
    calls: list[dict] = []

    async def stream_benchmark(self, **kwargs):
        self.calls.append(kwargs)
        if False:
            yield None


@pytest.mark.asyncio
async def test_load_test_preserves_zero_interval_and_holds_reservation(monkeypatch):
    import shared.deploy.sandbox_manager as manager_module
    import shared.run_registry as registry_module

    registry = _Registry()
    manager = _Manager()
    _QueryService.calls = []
    monkeypatch.setattr(registry_module, "run_registry", registry)
    monkeypatch.setattr(manager_module, "sandbox_manager", manager)
    monkeypatch.setattr(
        "features.query_registry.service.QueryService", _QueryService
    )

    response = await routes.start_load_test_run(
        routes.BenchmarkRequest(
            target="origin",
            queries=[routes.BenchmarkQueryInput(sql="SELECT 1")],
            mode="interval",
            interval_ms=0,
            concurrency=1,
            duration_seconds=5,
        ),
        _http_request(),
        TargetGuard("origin", {"engine": "postgresql"}, "postgresql"),
    )

    assert response.run_id == "load_test_origin_new"
    kind, target, factory, metadata = registry.started[0]
    assert (kind, target) == ("load_test", "origin")
    assert metadata["query_count"] == 1
    assert metadata["request"] == {
        "queries": [{"identifier": None, "sql": None}],
        "target": "origin",
        "mode": "interval",
        "interval_ms": 0,
        "concurrency": 1,
        "duration_seconds": 5,
        "max_count": None,
        "lanes": ["origin"],
        "parameter_sets": None,
        "warmup_executions": None,
        "statement_timeout_ms": None,
    }
    async for _event in factory(response.run_id):
        pass
    assert manager.reservations == [
        {"owner_id": response.run_id, "purpose": "load_test"}
    ]
    assert manager.leases == []
    assert _QueryService.calls[0]["interval_ms"] == 0
    assert _QueryService.calls[0]["lanes"] == ["origin"]
    assert _QueryService.calls[0]["readyset"] is None


@pytest.mark.asyncio
async def test_load_test_attaches_to_existing_target_run(monkeypatch):
    import shared.run_registry as registry_module

    request = routes.BenchmarkRequest(
        target="origin",
        queries=[routes.BenchmarkQueryInput(sql="SELECT 1")],
    )
    registry = _Registry(
        existing="load_test_origin_existing",
        existing_fingerprint=routes._benchmark_request_fingerprint(request),
    )
    monkeypatch.setattr(registry_module, "run_registry", registry)

    response = await routes.start_load_test_run(
        request,
        _http_request(),
        TargetGuard("origin", {"engine": "postgresql"}, "postgresql"),
    )

    assert response.run_id == "load_test_origin_existing"
    assert registry.started == []


@pytest.mark.asyncio
async def test_load_test_does_not_attach_to_different_request(monkeypatch):
    import shared.run_registry as registry_module

    registry = _Registry(
        existing="load_test_origin_existing",
        existing_fingerprint="different",
    )
    monkeypatch.setattr(registry_module, "run_registry", registry)

    response = await routes.start_load_test_run(
        routes.BenchmarkRequest(
            target="origin",
            queries=[routes.BenchmarkQueryInput(sql="SELECT 2")],
            duration_seconds=5,
        ),
        _http_request(),
        TargetGuard("origin", {"engine": "postgresql"}, "postgresql"),
    )

    assert response.run_id == "load_test_origin_new"
    assert len(registry.started) == 1


@pytest.mark.asyncio
async def test_cross_site_load_test_start_is_forbidden(monkeypatch):
    """A page on another site cannot start a run against the user's target."""
    import shared.run_registry as registry_module

    registry = _Registry()
    monkeypatch.setattr(registry_module, "run_registry", registry)

    with pytest.raises(HTTPException) as raised:
        await routes.start_load_test_run(
            routes.BenchmarkRequest(
                target="origin",
                queries=[routes.BenchmarkQueryInput(sql="SELECT 1")],
            ),
            _http_request(origin="https://evil.example"),
            TargetGuard("origin", {"engine": "postgresql"}, "postgresql"),
        )

    assert raised.value.status_code == 403
    assert registry.started == []


async def _start_dual_lane_run(monkeypatch, manager):
    """Start a run that asks for both lanes and drain its factory."""
    import shared.deploy.sandbox_manager as manager_module
    import shared.run_registry as registry_module

    registry = _Registry()
    _QueryService.calls = []
    monkeypatch.setattr(registry_module, "run_registry", registry)
    monkeypatch.setattr(manager_module, "sandbox_manager", manager)
    monkeypatch.setattr(
        "features.query_registry.service.QueryService", _QueryService
    )

    response = await routes.start_load_test_run(
        routes.BenchmarkRequest(
            target="origin",
            queries=[routes.BenchmarkQueryInput(sql="SELECT 1")],
            lanes=["origin", "readyset"],
            duration_seconds=5,
        ),
        _http_request(),
        TargetGuard("origin", {"engine": "postgresql"}, "postgresql"),
    )
    _kind, _target, factory, _metadata = registry.started[0]
    async for _event in factory(response.run_id):
        pass
    return response, _QueryService.calls[0]


@pytest.mark.asyncio
async def test_readyset_lane_leases_the_sandbox_for_the_run(monkeypatch):
    """The Readyset lane holds a lease, not the origin-only reservation."""
    manager = _Manager()

    response, call = await _start_dual_lane_run(monkeypatch, manager)

    assert manager.leases == [
        {
            "target": "origin",
            "owner_id": response.run_id,
            "purpose": "load_test",
        }
    ]
    assert manager.reservations == []
    assert call["lanes"] == ["origin", "readyset"]
    assert call["readyset"].available
    assert call["readyset"].config["target_type"] == "readyset"


@pytest.mark.asyncio
async def test_missing_sandbox_falls_back_to_an_origin_only_run(monkeypatch):
    """A sandbox that cannot be leased costs the lane, not the run."""
    manager = _Manager(lease_error=RuntimeError("Docker is not running"))

    response, call = await _start_dual_lane_run(monkeypatch, manager)

    assert manager.reservations == [
        {"owner_id": response.run_id, "purpose": "load_test"}
    ]
    assert call["readyset"].status == "unavailable"
    assert call["readyset"].detail == "Docker is not running"
    assert call["readyset"].config is None


@pytest.mark.asyncio
async def test_unknown_lane_is_rejected_by_the_request_model():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        routes.BenchmarkRequest(
            target="origin",
            queries=[routes.BenchmarkQueryInput(sql="SELECT 1")],
            lanes=["origin", "cache"],
        )
