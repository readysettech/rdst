"""Integration tests for the env API endpoints.

Drives the real `EnvRequirementsService` and `SecretStoreService` against
an isolated `~/.rdst/` (per-test tmp dir) and an in-memory keyring backend.
The OS keychain is the only system boundary mocked here — everything else
runs end-to-end.
"""

from __future__ import annotations

import os

import pytest
from httpx import ASGITransport, AsyncClient

from shared.api.routes import env as env_routes
from shared.config.targets import TargetsConfig
from shared.secret_store_service import SecretStoreService


@pytest.fixture(autouse=True)
def _isolate_anthropic_env(monkeypatch):
    """Strip ANTHROPIC_API_KEY / RDST_TRIAL_TOKEN so requirement reporting
    is deterministic. Tests that want them set should re-add explicitly."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("RDST_TRIAL_TOKEN", raising=False)
    monkeypatch.setattr("shared.anthropic_env._STARTUP_ANTHROPIC_KEY", None)


@pytest.fixture(autouse=True)
def anthropic_verdicts(monkeypatch):
    """Anthropic's answer per key; keys not listed are accepted. Tests add entries."""
    verdicts: dict[str, str] = {}

    def check(key):
        reason = verdicts.get(key, "ok")
        return {"valid": reason == "ok", "reason": reason, "model": "haiku"}

    monkeypatch.setattr(env_routes, "check_anthropic_key", check)
    monkeypatch.setattr("shared.anthropic_env.check_anthropic_key", check)
    return verdicts


def _seed_target_with_password_env(env_name: str, target: str = "prod") -> None:
    cfg = TargetsConfig()
    cfg.load()
    cfg.upsert(
        target,
        {
            "engine": "postgresql",
            "host": "db.example.com",
            "port": 5432,
            "database": "appdb",
            "user": "appuser",
            "password_env": env_name,
        },
    )
    cfg.save()


async def test_get_env_requirements_lists_seeded_target_password(
    client, tmp_rdst_home, inmemory_keyring, monkeypatch
):
    """Seeded target with `password_env` shows up as an unsatisfied
    `target_password` requirement; missing Anthropic key shows up too."""
    _seed_target_with_password_env("PROD_DB_PASSWORD", target="prod")

    response = await client.get("/api/env/requirements")
    assert response.status_code == 200, response.text

    payload = response.json()
    assert payload["keyring_available"] is True

    by_kind = {req["kind"]: req for req in payload["requirements"]}
    assert "target_password" in by_kind
    assert by_kind["target_password"]["accepted_names"] == ["PROD_DB_PASSWORD"]
    assert by_kind["target_password"]["target"] == "prod"
    assert by_kind["target_password"]["satisfied"] is False
    assert by_kind["target_password"]["source"] == "missing"

    assert "anthropic_api_key" in by_kind
    assert by_kind["anthropic_api_key"]["satisfied"] is False
    assert by_kind["anthropic_api_key"]["source"] == "missing"


async def test_set_password_for_target_without_password_source(
    client, tmp_rdst_home, inmemory_keyring, monkeypatch
):
    cfg = TargetsConfig()
    cfg.load()
    cfg.upsert(
        "imported",
        {
            "engine": "postgresql",
            "host": "db.example.com",
            "port": 5432,
            "database": "appdb",
            "user": "appuser",
        },
    )
    cfg.save()
    monkeypatch.delenv("RDST_IMPORTED_PASSWORD", raising=False)

    requirements = await client.get("/api/env/requirements")
    password_req = next(
        item
        for item in requirements.json()["requirements"]
        if item["kind"] == "target_password"
    )
    response = await client.post(
        "/api/env/set",
        json={
            "name": password_req["accepted_names"][0],
            "value": "session-password",
            "persist": False,
        },
    )

    cfg.load()
    assert response.json()["success"] is True
    assert cfg.get("imported")["password_env"] == "RDST_IMPORTED_PASSWORD"
    assert os.environ["RDST_IMPORTED_PASSWORD"] == "session-password"


async def test_set_env_secret_persists_to_keyring(
    client, tmp_rdst_home, inmemory_keyring, monkeypatch
):
    """`POST /api/env/set` with an allow-listed name and `persist=True`
    actually writes to the keyring backend (in-memory here) and updates
    `os.environ` as a side effect."""
    _seed_target_with_password_env("PROD_DB_PASSWORD")
    monkeypatch.delenv("RDST_PROD_PASSWORD", raising=False)

    response = await client.post(
        "/api/env/set",
        json={"name": "PROD_DB_PASSWORD", "value": "s3cret", "persist": True},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert body["persisted"] is True
    assert body["session_only"] is False

    # Side effects landed where we expect.
    assert os.environ["PROD_DB_PASSWORD"] == "s3cret"
    assert inmemory_keyring.get_password("rdst-web", "PROD_DB_PASSWORD") == "s3cret"


async def test_set_env_secret_session_only_when_persist_false(
    client, tmp_rdst_home, inmemory_keyring, monkeypatch
):
    """`persist=False` should set the env var but skip keyring writes."""
    _seed_target_with_password_env("PROD_DB_PASSWORD")
    monkeypatch.delenv("PROD_DB_PASSWORD", raising=False)

    response = await client.post(
        "/api/env/set",
        json={"name": "PROD_DB_PASSWORD", "value": "transient", "persist": False},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert body["persisted"] is False
    assert body["session_only"] is True

    assert os.environ["PROD_DB_PASSWORD"] == "transient"
    # Persisted=False must mean: did NOT touch the keyring.
    assert inmemory_keyring.get_password("rdst-web", "PROD_DB_PASSWORD") is None


async def test_set_target_password_stays_session_only_when_keyring_unavailable(
    client, tmp_rdst_home, inmemory_keyring, monkeypatch
):
    cfg = TargetsConfig()
    cfg.load()
    cfg.upsert(
        "prod",
        {
            "engine": "postgresql",
            "host": "db.example.com",
            "port": 5432,
            "database": "appdb",
            "user": "appuser",
        },
    )
    cfg.save()
    monkeypatch.delenv("PROD_DB_PASSWORD", raising=False)
    monkeypatch.setattr(SecretStoreService, "is_available", lambda self: False)

    response = await client.post(
        "/api/env/set",
        json={"name": "RDST_PROD_PASSWORD", "value": "durable", "persist": True},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["persisted"] is False
    assert body["session_only"] is True
    assert "session only" in body["message"].lower()
    assert os.environ["RDST_PROD_PASSWORD"] == "durable"

    cfg.load()
    saved = cfg.get("prod")
    assert "password" not in saved
    assert saved["password_env"] == "RDST_PROD_PASSWORD"


async def test_set_anthropic_secret_notifies_parked_work(
    client, tmp_rdst_home, inmemory_keyring, monkeypatch
):
    class Registry:
        wake_calls = 0

        def wake_needs_key(self):
            self.wake_calls += 1

    registry = Registry()
    monkeypatch.setattr(env_routes, "run_registry", registry)

    response = await client.post(
        "/api/env/set",
        json={"name": "ANTHROPIC_API_KEY", "value": "sk-ant-test", "persist": False},
    )

    assert response.status_code == 200, response.text
    assert response.json()["success"] is True
    assert registry.wake_calls == 1

    cfg = TargetsConfig()
    cfg.load()
    assert cfg.get_llm_provider() == "claude"


async def test_set_anthropic_secret_rejects_characters_outside_key_alphabet(
    client, tmp_rdst_home, inmemory_keyring, monkeypatch
):
    response = await client.post(
        "/api/env/set",
        json={"name": "ANTHROPIC_API_KEY", "value": "sk-ant-\u00e9\u6f22", "persist": True},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is False
    assert "isn't a valid Anthropic API key" in body["message"]
    assert SecretStoreService().get_secret("ANTHROPIC_API_KEY") is None
    assert "ANTHROPIC_API_KEY" not in os.environ


def _anthropic_requirement(payload):
    return next(item for item in payload["requirements"] if item["kind"] == "anthropic_api_key")


async def test_rejected_anthropic_key_keeps_the_current_one(
    client, tmp_rdst_home, inmemory_keyring, anthropic_verdicts
):
    saved = await client.post(
        "/api/env/set",
        json={"name": "ANTHROPIC_API_KEY", "value": "sk-ant-good", "persist": True},
    )
    assert saved.json()["success"] is True
    anthropic_verdicts["sk-ant-not-a-real-key"] = "rejected"

    response = await client.post(
        "/api/env/set",
        json={"name": "ANTHROPIC_API_KEY", "value": "sk-ant-not-a-real-key", "persist": True},
    )

    body = response.json()
    assert body["success"] is False
    assert body["message"].startswith("Anthropic didn't accept this key, so it wasn't saved.")
    assert body["message"].endswith("Your current key is still in use.")
    assert SecretStoreService().get_secret("ANTHROPIC_API_KEY") == "sk-ant-good"
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-good"
    requirements = await client.get("/api/env/requirements")
    assert _anthropic_requirement(requirements.json())["satisfied"] is True


async def test_unchecked_anthropic_key_never_replaces_a_working_one(
    client, tmp_rdst_home, inmemory_keyring, anthropic_verdicts
):
    await client.post(
        "/api/env/set",
        json={"name": "ANTHROPIC_API_KEY", "value": "sk-ant-good", "persist": True},
    )
    anthropic_verdicts["sk-ant-other"] = "provider_error"

    response = await client.post(
        "/api/env/set",
        json={"name": "ANTHROPIC_API_KEY", "value": "sk-ant-other", "persist": True},
    )

    body = response.json()
    assert body["success"] is False
    assert "couldn't check this key with Anthropic" in body["message"]
    assert SecretStoreService().get_secret("ANTHROPIC_API_KEY") == "sk-ant-good"


async def test_unchecked_anthropic_key_is_saved_when_there_is_none(
    client, tmp_rdst_home, inmemory_keyring, anthropic_verdicts
):
    anthropic_verdicts["sk-ant-offline"] = "provider_error"

    response = await client.post(
        "/api/env/set",
        json={"name": "ANTHROPIC_API_KEY", "value": "sk-ant-offline", "persist": True},
    )

    body = response.json()
    assert body["success"] is True
    assert body["message"].startswith("Key saved, but RDST couldn't check it with Anthropic.")
    assert SecretStoreService().get_secret("ANTHROPIC_API_KEY") == "sk-ant-offline"


async def test_requirements_report_a_startup_key_only_when_rdst_started_with_one(
    client, tmp_rdst_home, inmemory_keyring, monkeypatch
):
    await client.post(
        "/api/env/set",
        json={"name": "ANTHROPIC_API_KEY", "value": "sk-ant-ui", "persist": True},
    )
    requirements = await client.get("/api/env/requirements")
    assert _anthropic_requirement(requirements.json())["anthropic_key_in_startup_env"] is False

    monkeypatch.setattr("shared.anthropic_env._STARTUP_ANTHROPIC_KEY", "sk-ant-env")
    requirements = await client.get("/api/env/requirements")
    assert _anthropic_requirement(requirements.json())["anthropic_key_in_startup_env"] is True


async def test_switch_ai_provider_persists_and_updates_requirements(
    client, tmp_rdst_home, inmemory_keyring, monkeypatch
):
    await client.post(
        "/api/env/set",
        json={"name": "ANTHROPIC_API_KEY", "value": "sk-ant-test", "persist": True},
    )
    monkeypatch.setattr(
        "shared.env_requirements_service.access_token",
        lambda store=None: "readyset-account-token",
    )

    response = await client.post("/api/env/ai-provider", json={"provider": "readyset"})

    assert response.status_code == 200, response.text
    cfg = TargetsConfig()
    cfg.load()
    assert cfg.get_llm_provider() == "readyset"

    requirements = await client.get("/api/env/requirements")
    ai_requirement = next(
        item
        for item in requirements.json()["requirements"]
        if item["kind"] == "anthropic_api_key"
    )
    assert ai_requirement["source"] == "readyset_account"
    assert ai_requirement["selected_provider"] == "readyset"
    assert ai_requirement["anthropic_key_configured"] is True
    assert ai_requirement["readyset_account_connected"] is True

    response = await client.post("/api/env/ai-provider", json={"provider": "claude"})

    assert response.status_code == 200, response.text
    cfg.load()
    assert cfg.get_llm_provider() == "claude"
    requirements = await client.get("/api/env/requirements")
    ai_requirement = next(
        item
        for item in requirements.json()["requirements"]
        if item["kind"] == "anthropic_api_key"
    )
    assert ai_requirement["source"] == "process_env"
    assert ai_requirement["selected_provider"] == "claude"


async def test_switch_ai_provider_rejects_unavailable_provider(
    client, tmp_rdst_home, inmemory_keyring
):
    response = await client.post("/api/env/ai-provider", json={"provider": "readyset"})

    assert response.status_code == 409
    assert "Sign in to Readyset" in response.json()["detail"]


async def test_set_env_secret_rejects_non_allowlisted_name(
    client, tmp_rdst_home, inmemory_keyring
):
    """An env name that is not derivable from any target's `password_env`
    nor in the Anthropic accepted set must be refused with success=False."""
    response = await client.post(
        "/api/env/set",
        json={"name": "NOT_ALLOWED", "value": "x", "persist": True},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert body["name"] == "NOT_ALLOWED"
    assert body["persisted"] is False
    message = (body.get("message") or "").lower()
    assert message
    assert "export" not in message


async def test_set_env_secret_rejects_cross_site_origin(
    app, tmp_rdst_home, inmemory_keyring
):
    """Loopback request carrying another site's `Origin` must be 403'd by the
    same-host check. Loopback aliases all name this server and are
    interchangeable; a real site is not."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8787") as c:
        response = await c.post(
            "/api/env/set",
            headers={"origin": "https://evil.example"},
            json={"name": "PROD_DB_PASSWORD", "value": "x", "persist": True},
        )
    assert response.status_code == 403


async def test_env_routes_reject_non_loopback_client(app, tmp_rdst_home):
    """A headerless non-loopback peer is not treated as an RDST browser."""
    transport = ASGITransport(app=app, client=("203.0.113.10", 50000))
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        response = await c.get("/api/env/requirements")
    assert response.status_code == 403


async def test_env_routes_accept_remote_same_origin_browser(
    app, tmp_rdst_home, inmemory_keyring
):
    """The settings UI works when RDST is opened through a VM/private IP."""
    transport = ASGITransport(app=app, client=("192.168.56.1", 50000))
    async with AsyncClient(
        transport=transport, base_url="http://192.168.56.10:8787"
    ) as c:
        headers = {"origin": "http://192.168.56.10:8787"}
        requirements = await c.get(
            "/api/env/requirements",
            headers={"referer": "http://192.168.56.10:8787/settings"},
        )
        saved = await c.post(
            "/api/env/set",
            headers=headers,
            json={
                "name": "ANTHROPIC_API_KEY",
                "value": "sk-ant-test",
                "persist": False,
            },
        )

    assert requirements.status_code == 200, requirements.text
    assert saved.status_code == 200, saved.text
    assert saved.json()["success"] is True
