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


class _Manager:
    def __init__(self):
        self.reservations: list[dict] = []

    @asynccontextmanager
    async def reserve_measurement(self, **kwargs):
        self.reservations.append(kwargs)
        yield


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
        "parameter_sets": None,
        "warmup_executions": None,
        "statement_timeout_ms": None,
    }
    async for _event in factory(response.run_id):
        pass
    assert manager.reservations == [
        {"owner_id": response.run_id, "purpose": "load_test"}
    ]
    assert _QueryService.calls[0]["interval_ms"] == 0


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
