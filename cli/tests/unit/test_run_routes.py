"""Tests for the shared background-run API."""

import asyncio
import json

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response

from shared.api.routes import runs as run_routes
from shared.run_registry import RunRegistry


class _Client:
    def __init__(self, app: FastAPI):
        self.app = app

    def request(self, method: str, path: str, **kwargs) -> Response:
        async def send() -> Response:
            async with AsyncClient(
                transport=ASGITransport(app=self.app),
                base_url="http://testserver",
            ) as client:
                return await client.request(method, path, **kwargs)

        return asyncio.run(send())

    def get(self, path: str, **kwargs) -> Response:
        return self.request("GET", path, **kwargs)

    def delete(self, path: str, **kwargs) -> Response:
        return self.request("DELETE", path, **kwargs)


class StubRegistry:
    def __init__(self):
        self.cancelled = []

    def describe(self, run_id):
        if run_id == "missing":
            return None
        return {
            "run_id": run_id,
            "kind": "schema_annotation",
            "target": "imdb",
            "status": "running",
            "last_seq": 4,
            "metadata": {"query_hash": "abc123"},
        }

    def status(self, run_id):
        return None if run_id == "missing" else "running"

    def cancel(self, run_id):
        self.cancelled.append(run_id)
        return True


@pytest.fixture
def client(monkeypatch):
    registry = StubRegistry()
    monkeypatch.setattr(run_routes, "_registry", registry)
    app = FastAPI()
    app.include_router(run_routes.router, prefix="/api")
    return _Client(app), registry


def test_status_exposes_kind_target_and_replay_cursor(client):
    http, _registry = client

    response = http.get("/api/runs/run-1")

    assert response.status_code == 200
    assert response.json() == {
        "run_id": "run-1",
        "kind": "schema_annotation",
        "target": "imdb",
        "status": "running",
        "last_seq": 4,
        "metadata": {"query_hash": "abc123"},
    }


def test_unknown_run_is_404(client):
    http, _registry = client
    assert http.get("/api/runs/missing").status_code == 404
    assert http.delete("/api/runs/missing").status_code == 404


def test_cancel_forwards_to_registry(client):
    http, registry = client

    response = http.delete("/api/runs/run-1")

    assert response.json() == {"run_id": "run-1", "cancelled": True}
    assert registry.cancelled == ["run-1"]


def test_record_to_sse_includes_sequence():
    frame = run_routes._record_to_sse(
        {
            "seq": 2,
            "event": "annotate_progress",
            "data": {"table": "title"},
            "ts": "now",
        }
    )

    assert frame["event"] == "annotate_progress"
    assert frame["id"] == "2"
    assert json.loads(frame["data"]) == {"table": "title", "seq": 2}


@pytest.mark.asyncio
async def test_frames_replay_from_common_registry():
    from dataclasses import dataclass

    @dataclass
    class Event:
        type: str

    async def events():
        yield Event("one")
        yield Event("two")

    registry = RunRegistry()
    run_id = registry.start("schema_annotation", "imdb", events())
    all_frames = [
        frame async for frame in run_routes._sse_frames(registry, run_id, 0)
    ]
    replay = [
        frame async for frame in run_routes._sse_frames(registry, run_id, 1)
    ]

    assert [frame["event"] for frame in all_frames] == ["one", "two", "run_end"]
    assert [frame["id"] for frame in replay] == ["2", "3"]
