"""
Unit tests for the bootstrap API routes.

SSE mapping is tested by driving the frame generator directly (the repo
convention -- no SSE streaming through TestClient); JSON endpoints use a
minimal FastAPI app with the target guard overridden.
"""

import asyncio
import json

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from features.bootstrap.api import routes as bootstrap_routes
from features.bootstrap.events import BootstrapStageEvent
from shared.api.target_guard import TargetGuard, require_target_body
from shared.run_registry import RunRegistry


def _app(monkeypatch, service_cls=None):
    registry = RunRegistry()
    monkeypatch.setattr(bootstrap_routes, "_registry", registry)
    if service_cls is not None:
        monkeypatch.setattr(bootstrap_routes, "TargetBootstrapService", service_cls)
    app = FastAPI()
    app.include_router(bootstrap_routes.router, prefix="/api")
    async def target_guard():
        return TargetGuard("imdb", {"engine": "postgresql"}, "postgresql")

    app.dependency_overrides[require_target_body] = target_guard
    return app, registry


async def _request(app, method, path, json_body=None):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.request(method, path, json=json_body)


class StubService:
    """Stands in for TargetBootstrapService; yields two stage events."""

    async def run(self, target, target_config, options=None, key_wakeup=None):
        yield BootstrapStageEvent(
            type="bootstrap_stage", stage="connection_test", status="started"
        )
        yield BootstrapStageEvent(
            type="bootstrap_stage", stage="connection_test", status="done"
        )


class TestStartRoute:
    @pytest.mark.asyncio
    async def test_start_returns_run_id_immediately(self, monkeypatch):
        app, registry = _app(monkeypatch, service_cls=StubService)

        response = await _request(app, "POST", "/api/bootstrap", {"target": "imdb"})

        assert response.status_code == 200
        run_id = response.json()["run_id"]
        assert run_id.startswith("bootstrap_imdb_")
        # The detached run completes; its events landed in the registry.
        assert registry.status(run_id) in ("running", "done")

    @pytest.mark.asyncio
    async def test_start_forwards_annotation_option(self, monkeypatch):
        captured = {}

        class RecordingService(StubService):
            async def run(self, target, target_config, options=None, key_wakeup=None):
                captured["target"] = target
                captured["options"] = options
                return
                yield  # pragma: no cover

        app, _registry = _app(monkeypatch, service_cls=RecordingService)

        response = await _request(
            app,
            "POST",
            "/api/bootstrap",
            {"target": "imdb", "annotate": False},
        )

        assert response.status_code == 200
        for _ in range(20):
            if "target" in captured:
                break
            await asyncio.sleep(0.005)
        assert captured["target"] == "imdb"
        assert captured["options"].annotate is False


class TestStatusRoute:
    @pytest.mark.asyncio
    async def test_status_of_unknown_run_is_404(self, monkeypatch):
        app, _registry = _app(monkeypatch)
        response = await _request(app, "GET", "/api/bootstrap/runs/nope")
        assert response.status_code == 404
        assert "nope" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_status_of_finished_run(self, monkeypatch):
        app, registry = _app(monkeypatch, service_cls=StubService)
        run_id = (
            await _request(app, "POST", "/api/bootstrap", {"target": "imdb"})
        ).json()["run_id"]

        response = await _request(app, "GET", f"/api/bootstrap/runs/{run_id}")

        assert response.status_code == 200
        body = response.json()
        assert body["run_id"] == run_id
        assert body["status"] in ("running", "done")

    @pytest.mark.asyncio
    async def test_events_route_404_for_unknown_run(self, monkeypatch):
        app, _registry = _app(monkeypatch)
        response = await _request(app, "GET", "/api/bootstrap/runs/nope/events")
        assert response.status_code == 404


class TestSseFrames:
    def test_record_to_sse_shape(self):
        record = {
            "seq": 3,
            "event": "bootstrap_stage",
            "data": {"stage": "profile", "status": "done"},
            "ts": "t",
        }

        frame = bootstrap_routes._record_to_sse(record)

        assert frame["event"] == "bootstrap_stage"
        assert frame["id"] == "3"
        payload = json.loads(frame["data"])
        assert payload["stage"] == "profile"
        assert payload["seq"] == 3

    @pytest.mark.asyncio
    async def test_frames_replay_with_after_seq(self):
        registry = RunRegistry()
        run_id = registry.start("bootstrap", "imdb", _gen_events(3))
        # Drain the live run first so replay comes from the buffer.
        async for _frame in bootstrap_routes._sse_frames(registry, run_id, 0):
            pass

        frames = [
            f async for f in bootstrap_routes._sse_frames(registry, run_id, 2)
        ]

        assert [f["id"] for f in frames] == ["3", "4"]
        assert frames[-1]["event"] == "run_end"


def _gen_events(n):
    async def gen():
        for i in range(n):
            yield BootstrapStageEvent(
                type="bootstrap_stage", stage="structure", status="progress",
                message=str(i),
            )

    return gen()
