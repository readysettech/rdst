"""Shared fixtures for unit tests.

Unit tests must never touch the developer's real OS keychain: a test that
persists a secret through `SecretStoreService` (or raw `keyring`) would
write into the actual keychain and leak past the test run. Every test in
this suite gets a process-local in-memory keyring instead.
"""

from __future__ import annotations

import keyring
import pytest
from keyring.backend import KeyringBackend

from shared.secret_store_service import SecretStoreService


class _InMemoryKeyring(KeyringBackend):
    """Process-local keyring backend backed by a plain dict."""

    priority = 100  # type: ignore[assignment]

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self._store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self._store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self._store.pop((service, username), None)


@pytest.fixture(autouse=True)
def _isolated_keyring():
    """Route all keyring access to an in-memory backend for every test.

    Also resets `SecretStoreService._probe_cache`, which caches backend
    availability per service name across instances and would otherwise
    carry state between tests (or from process startup).
    """
    backend = _InMemoryKeyring()
    previous = keyring.get_keyring()
    keyring.set_keyring(backend)

    saved_cache = dict(SecretStoreService._probe_cache)
    SecretStoreService._probe_cache.clear()

    try:
        yield backend
    finally:
        keyring.set_keyring(previous)
        SecretStoreService._probe_cache.clear()
        SecretStoreService._probe_cache.update(saved_cache)


@pytest.fixture
def read_run_events():
    """Return a reader for the generic background-run SSE stream."""

    def _read(client, run_id: str) -> list[tuple[str, str]]:
        with client.stream(
            "GET", f"/api/runs/{run_id}/events?after_seq=0"
        ) as response:
            assert response.status_code == 200
            frames: list[tuple[str, str]] = []
            event = "message"
            for line in response.iter_lines():
                if line.startswith("event:"):
                    event = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    frames.append((event, line.split(":", 1)[1].strip()))
            return frames

    return _read


@pytest.fixture
def blocking_capture_service():
    """A capture service whose run never finishes, so its run stays active."""
    import asyncio

    class BlockingCaptureService:
        def run_capture(self, **kwargs):
            async def generator():
                from features.audit.events import WorkloadStatusEvent

                yield WorkloadStatusEvent(
                    type="status", phase="config", message="Connecting..."
                )
                await asyncio.Event().wait()

            return generator()

    return BlockingCaptureService


@pytest.fixture
def run_registry(monkeypatch):
    """One RunRegistry shared by the audit, fleet, and generic run routers."""
    from features.audit.api import routes as audit_routes
    from features.fleet.api import routes as fleet_routes
    from shared.api.routes import runs as run_routes
    from shared.run_registry import RunRegistry

    instance = RunRegistry()
    for module in (audit_routes, fleet_routes, run_routes):
        monkeypatch.setattr(module, "_registry", instance)
    return instance


@pytest.fixture
def run_api_target() -> str:
    """Target name the run-API guard resolves to; override per module."""
    return "imdb"


@pytest.fixture
def run_api_client(run_registry, run_api_target):
    """TestClient over the audit, fleet, and generic run routers.

    Entered as a context manager so every request shares one event loop and
    detached runs outlive the request that started them.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from features.audit.api import routes as audit_routes
    from features.fleet.api import routes as fleet_routes
    from shared.api.routes import runs as run_routes
    from shared.api.target_guard import TargetGuard, require_target_body

    app = FastAPI()
    for module in (audit_routes, fleet_routes, run_routes):
        app.include_router(module.router, prefix="/api")
    app.dependency_overrides[require_target_body] = lambda: TargetGuard(
        run_api_target, {"engine": "postgresql"}, "postgresql"
    )
    with TestClient(app) as test_client:
        yield test_client
