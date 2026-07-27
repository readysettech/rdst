"""
Fixtures for RDST API integration tests.

The fixtures here are intentionally scoped to this directory so they only
affect the integration test suite. The `realdb` marker is registered in
`tests/conftest.py`.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from unittest.mock import MagicMock

import keyring
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from keyring.backend import KeyringBackend

from shared.api.app import create_app
from shared.config.targets import TargetsConfig
from shared.secret_store_service import SecretStoreService


@pytest.fixture
def collect_sse_events():
    """Return an async helper that drains an SSE stream.

    Used by realdb tests that hit endpoints returning EventSourceResponse
    (analyze, configure/test, query-registry/benchmark, readyset/setup).

    Usage:
        events = await collect_sse_events(
            client, "POST", "/api/analyze", json_body={...}
        )
    Returns: [{'event': str, 'data': dict|str}, ...]
    """

    async def _collect(
        client: AsyncClient,
        method: str,
        url: str,
        *,
        json_body: dict | None = None,
    ) -> list[dict]:
        events: list[dict] = []
        current: dict = {}
        async with client.stream(method, url, json=json_body) as response:
            assert response.status_code == 200, await response.aread()
            async for line in response.aiter_lines():
                if not line:
                    if current:
                        events.append(current)
                        current = {}
                    continue
                if line.startswith("event:"):
                    current["event"] = line[len("event:") :].strip()
                elif line.startswith("data:"):
                    payload = line[len("data:") :].strip()
                    try:
                        current["data"] = json.loads(payload)
                    except json.JSONDecodeError:
                        current["data"] = payload
        if current:
            events.append(current)
        return events

    return _collect


@pytest.fixture
def isolated_telemetry(monkeypatch, tmp_rdst_home):
    """Real `TelemetryManager` rooted at the isolated HOME, with `track`
    captured by a `MagicMock`.

    Tests that want to assert on telemetry events opt in by requesting
    this fixture. The instance is rebound on the per-feature
    `from shared.telemetry import telemetry` imports — those bind at
    module import time, so each consumer needs its own patch.

    Disabled side effects: PostHog send, Sentry init, Slack webhooks.
    Enabled bookkeeping: `_get_stats`, `_increment_stat`, finalizer
    dispatch — these all run for real, so tests can assert on
    counters too.
    """
    from shared.telemetry_manager import TelemetryManager

    tm = TelemetryManager()
    tm._rdst_dir = tmp_rdst_home
    tm._enabled = True
    tm._initialized = True
    tm._device_id = "test-device-id"
    tm._stats = {}

    # Capture every track() call without doing real network work.
    tm.track = MagicMock()
    tm._slack_notify = MagicMock()
    tm._slack_notify_first_analyze = MagicMock()

    monkeypatch.setattr("shared.telemetry.telemetry", tm)
    for mod in (
        "features.analyze.api.routes",
        "features.ask.api.routes",
        "features.top.api.routes",
        "features.scan.api.routes",
    ):
        monkeypatch.setattr(f"{mod}.telemetry", tm, raising=False)
    return tm


@pytest.fixture
def app(tmp_rdst_home):
    """FastAPI app instance, with HOME already isolated."""
    return create_app()


@pytest_asyncio.fixture
async def client(app):
    """In-process httpx client that drives the ASGI app directly.

    Lifespan events do not fire under ASGITransport, which is what we want
    in tests — startup hooks must not run side effects here.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


class _InMemoryKeyring(KeyringBackend):
    """Process-local keyring used by env/dev tests.

    Replaces the OS keychain so `SecretStoreService` reads/writes a dict.
    Priority is high enough that `keyring.set_keyring()` selects us over
    any installed real backend without requiring environment overrides.
    """

    priority = 100  # type: ignore[assignment]

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self._store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self._store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self._store.pop((service, username), None)


@pytest.fixture
def inmemory_keyring(monkeypatch):
    """Install an in-memory keyring backend for the duration of one test.

    Also resets `SecretStoreService._probe_cache` because availability is
    cached per service-name across instances and would otherwise stick to
    whatever the previous test (or process startup) decided.
    """
    backend = _InMemoryKeyring()
    previous = keyring.get_keyring()
    keyring.set_keyring(backend)

    # Class-level cache; clear before AND restore after.
    saved_cache = dict(SecretStoreService._probe_cache)
    SecretStoreService._probe_cache.clear()

    try:
        yield backend
    finally:
        keyring.set_keyring(previous)
        SecretStoreService._probe_cache.clear()
        SecretStoreService._probe_cache.update(saved_cache)


@pytest.fixture
def db_engine() -> str:
    """Database engine under test ('postgresql' or 'mysql').

    Set by `run_api_integration_tests.sh`. Defaults to postgresql for
    local runs where the developer brought up just the postgres container.
    """
    return os.environ.get("RDST_TEST_ENGINE", "postgresql")


@pytest.fixture
def db_target_payload(db_engine: str) -> dict:
    """Connection payload for `POST /api/configure/targets`.

    Hosts/ports/credentials match what `tests/integration/docker-compose.yml`
    exposes and what `run_containerized_tests.sh` exports for the CLI suite.
    The password env var is `RDST_TEST_PASSWORD`, which the runner sets to
    `testpassword`.
    """
    if db_engine == "postgresql":
        return {
            "engine": "postgresql",
            "host": "localhost",
            "port": 15432,
            "database": "testdb",
            "user": "testuser",
            "password_env": "RDST_TEST_PASSWORD",
        }
    if db_engine == "mysql":
        return {
            "engine": "mysql",
            "host": "localhost",
            "port": 13306,
            "database": "testdb",
            "user": "testuser",
            "password_env": "RDST_TEST_PASSWORD",
        }
    raise ValueError(f"Unknown RDST_TEST_ENGINE={db_engine!r}")


@pytest.fixture
def seeded_target_defaults() -> dict:
    """Name and password env a bare ``seed_target()`` writes; override per
    module."""
    return {"name": "testtarget", "env": "TEST_PASSWORD"}


@pytest.fixture
def seed_target(seeded_target_defaults):
    """Write one PostgreSQL target into the isolated ``~/.rdst/config.toml``.

    Requesting this fixture only builds the writer; call it per target. Every
    caller runs under ``tmp_rdst_home``, so the config never touches real HOME.
    """

    def _seed(
        name: str | None = None,
        *,
        env: str | None = None,
        group: str | None = None,
        tags: list[str] | None = None,
        **extra,
    ) -> None:
        name = name or seeded_target_defaults["name"]
        password_env = env or seeded_target_defaults["env"]
        cfg = TargetsConfig()
        cfg.load()
        entry: dict = {
            "engine": "postgresql",
            "host": "127.0.0.1",
            "port": 5432,
            "database": "appdb",
            "user": "appuser",
            "password_env": password_env,
        }
        if group:
            entry["group"] = group
        if tags:
            entry["tags"] = tags
        entry.update(extra)
        cfg.upsert(name, entry)
        cfg.save()

    return _seed


@pytest.fixture
def override_target_guard(app):
    """Force ``require_target`` to resolve a named target for one block.

    The override is popped on exit so it never leaks into the next test.
    """

    @contextmanager
    def _override(target_name: str):
        from shared.api.target_guard import ensure_target_password, require_target

        async def target_guard():
            return ensure_target_password(target_name)

        app.dependency_overrides[require_target] = target_guard
        try:
            yield
        finally:
            app.dependency_overrides.pop(require_target, None)

    return _override


@pytest.fixture
def fleet_member():
    """Build a `FleetMember` for the discovery/import routes.

    Defaults describe a writer-style RDS PostgreSQL instance; MySQL members
    override ``engine`` and ``port``.
    """
    from features.fleet.models import FleetMember

    def _member(name: str, **overrides):
        fields: dict = {
            "name": name,
            "engine": "postgresql",
            "host": f"{name}.abc.us-east-1.rds.amazonaws.com",
            "port": 5432,
            "database": "app",
            "user": "postgres",
            "password_env": "FLEET_PASS",
        }
        fields.update(overrides)
        return FleetMember(**fields)

    return _member


@pytest.fixture
def mock_targets_config():
    """A minimal mocked `TargetsConfig` for CLI command tests.

    Classes whose command reads more of the config override this fixture with
    the extra attributes they need.
    """
    cfg = MagicMock()
    cfg.get_default.return_value = "test-target"
    cfg.get.return_value = {"engine": "postgresql", "host": "localhost"}
    cfg.load = MagicMock()
    return cfg
