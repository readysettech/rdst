"""Tests for starting manual schema annotation as a background run."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

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


def _client(monkeypatch, registry):
    monkeypatch.setattr(semantic_layer_routes, "_registry", registry)
    monkeypatch.setattr(
        semantic_layer_routes, "AnnotateService", StubAnnotateService
    )
    app = FastAPI()
    app.include_router(semantic_layer_routes.router, prefix="/api")
    app.dependency_overrides[require_target_body] = lambda: TargetGuard(
        "imdb", {"engine": "postgresql"}, "postgresql"
    )
    return TestClient(app)


def test_starts_annotation_in_shared_registry(monkeypatch):
    registry = StubRegistry()
    client = _client(monkeypatch, registry)

    response = client.post(
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


def test_reuses_existing_annotation_run(monkeypatch):
    registry = StubRegistry(annotation="schema_annotation_imdb_existing")
    client = _client(monkeypatch, registry)

    response = client.post(
        "/api/semantic-layer/annotation-runs", json={"target": "imdb"}
    )

    assert response.json() == {
        "run_id": "schema_annotation_imdb_existing",
        "reused": True,
    }
    assert registry.started == []


def test_rejects_annotation_while_bootstrap_owns_target(monkeypatch):
    registry = StubRegistry(bootstrap="bootstrap_imdb_existing")
    client = _client(monkeypatch, registry)

    response = client.post(
        "/api/semantic-layer/annotation-runs", json={"target": "imdb"}
    )

    assert response.status_code == 409
    assert "setup is already running" in response.json()["detail"].lower()
    assert registry.started == []
