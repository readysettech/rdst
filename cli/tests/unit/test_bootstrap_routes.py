"""
Unit tests for the bootstrap API routes.

SSE mapping is tested by driving the frame generator directly (the repo
convention -- no SSE streaming through TestClient); JSON endpoints use a
minimal FastAPI app with the target guard overridden.
"""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from features.bootstrap.api import routes as bootstrap_routes
from features.bootstrap.events import BootstrapStageEvent
from shared.api.target_guard import TargetGuard, require_target_body
from shared.run_registry import RunRegistry


def _client(monkeypatch, service_cls=None):
    registry = RunRegistry()
    monkeypatch.setattr(bootstrap_routes, "_registry", registry)
    if service_cls is not None:
        monkeypatch.setattr(bootstrap_routes, "TargetBootstrapService", service_cls)
    app = FastAPI()
    app.include_router(bootstrap_routes.router, prefix="/api")
    app.dependency_overrides[require_target_body] = lambda: TargetGuard(
        "imdb", {"engine": "postgresql"}, "postgresql"
    )
    return TestClient(app), registry


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
    def test_start_returns_run_id_immediately(self, monkeypatch):
        client, registry = _client(monkeypatch, service_cls=StubService)

        response = client.post("/api/bootstrap", json={"target": "imdb"})

        assert response.status_code == 200
        run_id = response.json()["run_id"]
        assert run_id.startswith("bootstrap_imdb_")
        # The detached run completes; its events landed in the registry.
        assert registry.status(run_id) in ("running", "done")

    def test_start_forwards_deploy_options(self, monkeypatch):
        captured = {}

        class RecordingService(StubService):
            async def run(self, target, target_config, options=None, key_wakeup=None):
                captured["target"] = target
                captured["options"] = options
                return
                yield  # pragma: no cover

        client, _registry = _client(monkeypatch, service_cls=RecordingService)

        response = client.post(
            "/api/bootstrap",
            json={"target": "imdb", "deploy": False, "deploy_mode": "kubernetes"},
        )

        assert response.status_code == 200
        assert captured["target"] == "imdb"
        assert captured["options"].deploy is False
        assert captured["options"].deploy_mode == "kubernetes"


class TestStatusRoute:
    def test_status_of_unknown_run_is_404(self, monkeypatch):
        client, _registry = _client(monkeypatch)
        response = client.get("/api/bootstrap/runs/nope")
        assert response.status_code == 404
        assert "nope" in response.json()["detail"]

    def test_status_of_finished_run(self, monkeypatch):
        client, registry = _client(monkeypatch, service_cls=StubService)
        run_id = client.post("/api/bootstrap", json={"target": "imdb"}).json()["run_id"]

        response = client.get(f"/api/bootstrap/runs/{run_id}")

        assert response.status_code == 200
        body = response.json()
        assert body["run_id"] == run_id
        assert body["status"] in ("running", "done")

    def test_events_route_404_for_unknown_run(self, monkeypatch):
        client, _registry = _client(monkeypatch)
        response = client.get("/api/bootstrap/runs/nope/events")
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
