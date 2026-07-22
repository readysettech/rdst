"""Tests for starting cache comparisons as detached background runs."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from features.cache.api import routes
from shared.api.target_guard import TargetGuard, require_target_body


class StubRegistry:
    def __init__(self):
        self.started = []

    def start(self, kind, target, generator, metadata=None):
        self.started.append((kind, target, generator, metadata))
        return "cache_test_imdb_new"


class StubCacheService:
    def run_comparison(self, input_data, iterations=5, warmup=2):
        async def generator():
            if False:
                yield None

        assert input_data.target == "imdb"
        assert input_data.query == "SELECT 1"
        assert iterations == 3
        assert warmup == 1
        return generator()


def test_starts_comparison_in_shared_registry(monkeypatch):
    registry = StubRegistry()
    monkeypatch.setattr(routes, "_run_registry", registry)
    monkeypatch.setattr(routes, "CacheService", StubCacheService)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    app.dependency_overrides[require_target_body] = lambda: TargetGuard(
        "imdb", {"engine": "postgresql"}, "postgresql"
    )
    client = TestClient(app)

    response = client.post(
        "/api/cache/test-runs",
        json={
            "target": "imdb",
            "query": "SELECT 1",
            "query_hash": "abc123",
            "label": "Health check",
            "iterations": 3,
            "warmup": 1,
        },
    )

    assert response.status_code == 200
    assert response.json() == {"run_id": "cache_test_imdb_new"}
    kind, target, _generator, metadata = registry.started[0]
    assert (kind, target) == ("cache_test", "imdb")
    assert metadata == {"query_hash": "abc123", "label": "Health check"}


def test_comparison_controller_interrupts_registered_connection():
    from features.cache.performance_comparison import ComparisonController

    class Connection:
        def __init__(self):
            self.cancelled = False

        def cancel(self):
            self.cancelled = True

    connection = Connection()
    controller = ComparisonController()
    controller.register(connection)

    controller.cancel()

    assert controller.cancelled is True
    assert connection.cancelled is True
