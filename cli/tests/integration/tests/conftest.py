"""
Fixtures for RDST API integration tests.

The fixtures here are intentionally scoped to this directory so they only
affect the integration test suite. The `realdb` marker is registered in
`tests/conftest.py`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import keyring
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from keyring.backend import KeyringBackend

from shared.api.app import create_app
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
def tmp_rdst_home(monkeypatch, tmp_path: Path) -> Path:
    """Relocate `~/.rdst/` to a fresh tmp dir for the duration of one test.

    Coverage is *partial* — beware before adding tests for new surfaces.

    Covered:
    - `Path.home()` callsites (e.g. `shared/config/targets.py:119`,
      `features/audit/storage.py:20`, `features/fleet/snapshot_store.py:18`)
      — these resolve at call time, so setting HOME isolates them.
    - Modules that read `shared.constants.RDST_DATA_DIR` *through* the
      `shared.constants` namespace at call time — the monkeypatch on the
      attribute reaches them.

    NOT covered (will silently read/write the developer's real `~/.rdst/`):
    - Modules that did `from shared.constants import RDST_DATA_DIR` at
      import time — they captured the original `Path` object before the
      monkeypatch ran. Examples:
      `shared/api/routes/status.py:5`, `features/ask/history/ask_history.py:13`,
      `shared/telemetry_manager.py:22`.
    - Modules that derive a *module-scope* path from `RDST_DATA_DIR` at
      import time — those paths are frozen for the process lifetime
      regardless of monkeypatch. Examples:
      `features/agent/config.py:24` (`AGENTS_DIR`),
      `features/slack/config.py:24`, `features/guard/config.py:24`,
      `features/scan/cli/scan_corpus.py:41`,
      `features/scan/cli/snippet_cache.py:39`.

    Realdb tests today don't exercise any of those surfaces, which is why
    this fixture is sufficient now. Before adding a realdb test for
    /api/agent, /api/slack, /api/guard, /api/scan/*, or anything that
    reads `status.data_directory`, either patch each module's *local*
    `RDST_DATA_DIR` binding too or route all access through a getter.
    """
    rdst_home = tmp_path / "home"
    rdst_data_dir = rdst_home / ".rdst"
    rdst_data_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("HOME", str(rdst_home))
    monkeypatch.setattr("shared.constants.RDST_DATA_DIR", rdst_data_dir)

    return rdst_data_dir


@pytest.fixture
def app(tmp_rdst_home):
    """FastAPI app instance, with HOME already isolated."""
    return create_app()


@pytest_asyncio.fixture
async def client(app):
    """In-process httpx client that drives the ASGI app directly.

    Lifespan events do not fire under ASGITransport, which is what we want
    in tests — startup hooks like `start_prepull` would otherwise spawn a
    `docker pull` against the ReadySet image.
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
