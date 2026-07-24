"""Tests for starting cache comparisons as detached background runs."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from features.cache.api import routes
from shared.api.target_guard import TargetGuard, require_target_body


class StubRegistry:
    def __init__(self):
        self.started = []
        self.match_calls = []

    def find_active_matching(self, kind, target, metadata, keys):
        self.match_calls.append((kind, target, metadata, keys))
        return None

    def start_factory(self, kind, target, factory, metadata=None):
        self.started.append((kind, target, factory, metadata))
        return "speed_test_imdb_new"


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


async def healthy_upstream(_target_config):
    return {"success": True, "error": None}


async def healthy_docker():
    return {"installed": True, "running": True}


def test_starts_comparison_in_shared_registry(monkeypatch):
    registry = StubRegistry()
    monkeypatch.setattr(routes, "_run_registry", registry)
    monkeypatch.setattr(routes, "_docker_runtime_status", healthy_docker)
    monkeypatch.setattr(routes, "_probe_upstream", healthy_upstream)
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
    assert response.json() == {"run_id": "speed_test_imdb_new"}
    kind, target, _factory, metadata = registry.started[0]
    assert (kind, target) == ("speed_test", "imdb")
    assert metadata["query_hash"] == "abc123"
    assert metadata["label"] == "Health check"
    assert len(metadata["parameter_fingerprint"]) == 64
    assert metadata["iterations"] == 3
    assert metadata["warmup"] == 1
    assert registry.match_calls[0][3] == (
        "query_hash",
        "parameter_fingerprint",
        "iterations",
        "warmup",
    )


def test_rejects_invalid_speed_test_counts_before_start(monkeypatch):
    registry = StubRegistry()
    monkeypatch.setattr(routes, "_run_registry", registry)
    monkeypatch.setattr(routes, "_docker_runtime_status", healthy_docker)
    monkeypatch.setattr(routes, "_probe_upstream", healthy_upstream)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    app.dependency_overrides[require_target_body] = lambda: TargetGuard(
        "imdb", {"engine": "postgresql"}, "postgresql"
    )
    client = TestClient(app)

    zero = client.post(
        "/api/cache/test-runs",
        json={"target": "imdb", "query": "SELECT 1", "iterations": 0},
    )
    excessive = client.post(
        "/api/cache/test-runs",
        json={"target": "imdb", "query": "SELECT 1", "iterations": 1001},
    )

    assert zero.status_code == 422
    assert excessive.status_code == 422
    assert registry.started == []


def test_unhealthy_upstream_rejects_before_starting_run(monkeypatch):
    registry = StubRegistry()

    async def unhealthy_upstream(_target_config):
        return {"success": False, "error": "connection refused"}

    monkeypatch.setattr(routes, "_run_registry", registry)
    monkeypatch.setattr(routes, "_docker_runtime_status", healthy_docker)
    monkeypatch.setattr(routes, "_probe_upstream", unhealthy_upstream)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    app.dependency_overrides[require_target_body] = lambda: TargetGuard(
        "imdb", {"engine": "postgresql"}, "postgresql"
    )
    client = TestClient(app)

    response = client.post(
        "/api/cache/test-runs",
        json={"target": "imdb", "query": "SELECT 1"},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "upstream_unavailable"
    assert "no Readyset work was queued" in response.json()["detail"]["message"]
    assert registry.started == []


def test_unhealthy_upstream_rejects_before_requesting_prewarm(monkeypatch):
    class StubSandboxManager:
        def __init__(self):
            self.prewarmed = []

        def request_prewarm(self, target):
            self.prewarmed.append(target)

    async def unhealthy_upstream(_target_config):
        return {"success": False, "error": "connection refused"}

    from shared.deploy import sandbox_manager as sandbox_manager_module

    manager = StubSandboxManager()
    monkeypatch.setattr(routes, "_docker_runtime_status", healthy_docker)
    monkeypatch.setattr(routes, "_probe_upstream", unhealthy_upstream)
    monkeypatch.setattr(sandbox_manager_module, "sandbox_manager", manager)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    app.dependency_overrides[require_target_body] = lambda: TargetGuard(
        "imdb", {"engine": "postgresql"}, "postgresql"
    )
    client = TestClient(app)

    response = client.post(
        "/api/cache/sandbox/prewarm",
        json={"target": "imdb"},
    )

    assert response.status_code == 503
    assert manager.prewarmed == []


def test_missing_docker_rejects_before_probe_or_start(monkeypatch):
    registry = StubRegistry()
    probe_calls = []

    async def missing_docker():
        return {"installed": False, "running": False}

    async def tracked_probe(target_config):
        probe_calls.append(target_config)
        return {"success": True}

    monkeypatch.setattr(routes, "_run_registry", registry)
    monkeypatch.setattr(routes, "_docker_runtime_status", missing_docker)
    monkeypatch.setattr(routes, "_probe_upstream", tracked_probe)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    app.dependency_overrides[require_target_body] = lambda: TargetGuard(
        "imdb", {"engine": "postgresql"}, "postgresql"
    )
    client = TestClient(app)

    response = client.post(
        "/api/cache/test-runs",
        json={"target": "imdb", "query": "SELECT 1"},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "docker_not_installed"
    assert registry.started == []
    assert probe_calls == []


def test_sandbox_status_includes_docker_capability(monkeypatch):
    class StubSandboxManager:
        async def diagnostics(self):
            return {
                "phase": "absent",
                "current_target": None,
                "generation": 0,
                "lease_owner": None,
                "lease_purpose": None,
                "queued_requests": 0,
                "dirty_reason": None,
                "failed_target": None,
                "last_error": None,
                "last_released_at": None,
                "expires_at": None,
                "container_name": "rdst-readyset-sandbox",
                "healthy": False,
            }

    async def stopped_docker():
        return {"installed": True, "running": False}

    from shared.deploy import sandbox_manager as sandbox_manager_module

    monkeypatch.setattr(
        sandbox_manager_module,
        "sandbox_manager",
        StubSandboxManager(),
    )
    monkeypatch.setattr(routes, "_docker_runtime_status", stopped_docker)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    client = TestClient(app)

    response = client.get("/api/cache/sandbox")

    assert response.status_code == 200
    assert response.json()["docker_installed"] is True
    assert response.json()["docker_running"] is False


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
