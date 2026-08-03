"""Tests for starting manual schema annotation as a background run."""

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from features.schema.api import semantic_layer_routes
from shared.api.target_guard import TargetGuard, require_target_body


class StubRegistry:
    def __init__(self, annotation=None, bootstrap=None):
        self.annotation = annotation
        self.bootstrap = bootstrap
        self.started = []

    def find_active(self, kinds, target):
        if kinds == "schema_annotation":
            return self.annotation
        if kinds == "bootstrap":
            return self.bootstrap
        return None

    def start(self, kind, target, generator):
        self.started.append((kind, target, generator))
        return "schema_annotation_imdb_new"


class StubAnnotateService:
    def annotate(self, target, target_config, table_name, sample_rows):
        async def generator():
            if False:
                yield None

        return generator()


def _app(monkeypatch, registry):
    monkeypatch.setattr(semantic_layer_routes, "_registry", registry)
    monkeypatch.setattr(
        semantic_layer_routes, "AnnotateService", StubAnnotateService
    )
    app = FastAPI()
    app.include_router(semantic_layer_routes.router, prefix="/api")

    async def target_guard():
        return TargetGuard("imdb", {"engine": "postgresql"}, "postgresql")

    app.dependency_overrides[require_target_body] = target_guard
    return app


@pytest.mark.asyncio
async def test_starts_annotation_in_shared_registry(monkeypatch):
    registry = StubRegistry()
    app = _app(monkeypatch, registry)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/semantic-layer/annotation-runs",
            json={"target": "imdb", "sample_rows": 9},
        )

    assert response.status_code == 200
    assert response.json() == {
        "run_id": "schema_annotation_imdb_new",
        "reused": False,
    }
    assert [(kind, target) for kind, target, _gen in registry.started] == [
        ("schema_annotation", "imdb")
    ]


@pytest.mark.asyncio
async def test_reuses_existing_annotation_run(monkeypatch):
    registry = StubRegistry(annotation="schema_annotation_imdb_existing")
    app = _app(monkeypatch, registry)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/semantic-layer/annotation-runs", json={"target": "imdb"}
        )

    assert response.json() == {
        "run_id": "schema_annotation_imdb_existing",
        "reused": True,
    }
    assert registry.started == []


@pytest.mark.asyncio
async def test_rejects_annotation_while_bootstrap_owns_target(monkeypatch):
    registry = StubRegistry(bootstrap="bootstrap_imdb_existing")
    app = _app(monkeypatch, registry)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/semantic-layer/annotation-runs", json={"target": "imdb"}
        )

    assert response.status_code == 409
    assert "setup is already running" in response.json()["detail"].lower()
    assert registry.started == []
