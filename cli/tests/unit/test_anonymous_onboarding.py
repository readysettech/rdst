"""Hosted AI without sign-in: anonymous sessions, the sign-in prompt and onboarding telemetry.

Own-key users and existing signed-in users must behave exactly as before; these tests pin
that alongside the anonymous flows.
"""

from __future__ import annotations

import base64
import json
import os
import time
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import shared.account_session as account_session
import shared.config.targets as targets_mod
import shared.secret_store_service as secret_mod
from features.account.service import AccountService
from shared import machine_id, product_telemetry
from shared.llm_manager import key_resolution
from shared.llm_manager.base import LLMError

MACHINE = "a" * 64
_real_machine_hash = machine_id.machine_hash


def _jwt(claims: dict[str, Any]) -> str:
    def part(value: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    return f"{part({'alg': 'none'})}.{part(claims)}.sig"


ANON_TOKEN = _jwt({"sub": "anon-user", "is_anonymous": True})
SIGNED_TOKEN = _jwt({"sub": "user-1", "is_anonymous": False})


class _Response:
    def __init__(self, status_code: int, body: Any, headers: dict[str, str] | None = None):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}

    def json(self) -> Any:
        return self._body


class _Mint:
    """Stands in for POST /account-auth/anonymous."""

    def __init__(self, response: _Response | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.response = response or _Response(200, {
            "access_token": ANON_TOKEN, "refresh_token": "anon-refresh", "expires_in": 3600,
            "expires_at": int(time.time()) + 3600, "user_id": "anon-user",
            "is_anonymous": True, "machine_first_seen_at": int(time.time()) - 86400,
            "trial_days": 30,
        })

    def __call__(self, payload: dict[str, Any]) -> _Response:
        self.calls.append(payload)
        return self.response


class _Config:
    def __init__(self, provider: str | None = None) -> None:
        self.provider = provider

    def load(self) -> None:
        return None

    def get_llm_provider(self) -> str | None:
        return self.provider


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    for name in ("ANTHROPIC_API_KEY", *account_session.SESSION_SECRET_NAMES):
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    monkeypatch.setattr(machine_id, "machine_hash", lambda: MACHINE)
    yield
    # Saving a session applies it to the process environment as well as the keyring.
    for name in account_session.SESSION_SECRET_NAMES:
        os.environ.pop(name, None)


@pytest.fixture
def mint(monkeypatch) -> _Mint:
    fake = _Mint()
    monkeypatch.setattr(account_session, "_post_anonymous", fake)
    return fake


def _signed_in(monkeypatch, token: str = SIGNED_TOKEN) -> None:
    monkeypatch.setattr(account_session, "access_token", lambda **_: token)


# Machine id


def test_machine_hash_is_salted_and_never_the_raw_id(monkeypatch):
    machine_id.reset_cache()
    monkeypatch.setattr(machine_id, "raw_machine_id", lambda: "raw-machine-guid")
    try:
        value = _real_machine_hash()
        assert value == machine_id.hash_id("raw-machine-guid")
        assert "raw-machine-guid" not in value and len(value) == 64
        monkeypatch.setattr(machine_id, "raw_machine_id", lambda: "changed")
        assert _real_machine_hash() == value
    finally:
        machine_id.reset_cache()


def test_machine_hash_falls_back_to_the_install_device_id(monkeypatch, tmp_rdst_home):
    machine_id.reset_cache()
    monkeypatch.setattr(machine_id, "raw_machine_id", lambda: None)
    try:
        first = _real_machine_hash()
        device_id = (tmp_rdst_home / "device_id").read_text().strip()
        assert first == machine_id.hash_id(f"device:{device_id}")
        machine_id.reset_cache()
        assert _real_machine_hash() == first
    finally:
        machine_id.reset_cache()


def test_machine_id_override_stands_in_for_the_os_id(monkeypatch):
    monkeypatch.setenv("RDST_MACHINE_ID_OVERRIDE", "simulated-second-computer")
    assert machine_id.raw_machine_id() == "simulated-second-computer"
    monkeypatch.delenv("RDST_MACHINE_ID_OVERRIDE")
    assert machine_id.raw_machine_id() != "simulated-second-computer"


# Anonymous sessions


def test_no_session_starts_one_anonymous_session_for_hosted_ai(mint):
    resolution = key_resolution.resolve_api_key()

    assert resolution.provider == "readyset" and resolution.api_key == ANON_TOKEN
    assert mint.calls == [{"machine_hash": MACHINE, "client_version": mint.calls[0]["client_version"],
                           "surface": product_telemetry.surface(), "internal_user": True}]
    assert account_session.is_anonymous_session()
    metadata = account_session.account_metadata()
    assert metadata["is_anonymous"] is True and metadata["trial_days"] == 30
    # The stored session is reused: one sign-up per install.
    assert key_resolution.resolve_api_key().api_key == ANON_TOKEN
    assert len(mint.calls) == 1


def test_anonymous_session_satisfies_monitoring(mint):
    key_resolution.require_monitoring_session()

    assert len(mint.calls) == 1
    assert key_resolution.hosted_ai_ready() is True


def test_machine_linked_reinstall_must_sign_in_and_is_not_retried(mint):
    mint.response = _Response(409, {"code": "machine_linked_signin_required",
                                    "reason": "machine_linked_signin_required"})

    with pytest.raises(LLMError) as raised:
        key_resolution.resolve_api_key()
    assert raised.value.code == "LOGIN_REQUIRED"
    with pytest.raises(LLMError):
        key_resolution.require_monitoring_session()

    assert len(mint.calls) == 1
    assert account_session.signin_required() == "machine_linked_signin_required"


def test_mint_outage_keeps_the_signed_out_answer_and_backs_off(mint):
    mint.response = _Response(502, {"code": "upstream_error"})

    with pytest.raises(LLMError) as raised:
        key_resolution.resolve_api_key()
    assert raised.value.code == "LOGIN_REQUIRED"
    with pytest.raises(LLMError):
        key_resolution.resolve_api_key()
    assert len(mint.calls) == 1


def test_day_30_refusal_surfaces_as_sign_in_required(mint):
    from shared.llm_manager.hosted_provider import HostedProvider

    key_resolution.resolve_api_key()
    with pytest.raises(LLMError) as raised:
        HostedProvider._raise_response_error(_Response(403, {
            "code": "anonymous_signin_required", "reason": "anonymous_signin_required",
            "detail": "Sign in to keep AI monitoring running"}))
    assert raised.value.code == "LOGIN_REQUIRED"
    assert account_session.signin_required() == "anonymous_signin_required"

    with pytest.raises(LLMError) as raised:
        key_resolution.resolve_api_key()
    assert raised.value.code == "LOGIN_REQUIRED"
    assert "Sign in" in str(raised.value)
    assert key_resolution.hosted_ai_ready() is False
    from features.monitor import database_limit, loop

    assert database_limit._signed_in() is False and loop.signed_in() is False


def test_jev_refusal_for_anonymous_cutoff_is_login_required(mint, monkeypatch):
    from shared.llm_manager.jev_client import JevClient, JevClientError

    class Session:
        def post(self, *args, **kwargs):
            return _Response(403, {"code": "anonymous_signin_required"})

    with pytest.raises(JevClientError) as raised:
        JevClient(session=Session())._send("/v1/jev/assess", {"x": 1})
    assert raised.value.code == "LOGIN_REQUIRED"
    assert account_session.signin_required() == "anonymous_signin_required"
    with pytest.raises(JevClientError) as raised:
        JevClient(session=Session())._send("/v1/jev/assess", {"x": 1})
    assert raised.value.code == "LOGIN_REQUIRED"


def test_own_key_monitoring_keeps_running_past_the_anonymous_cutoff(mint, monkeypatch):
    key_resolution.resolve_api_key()
    account_session.record_signin_required("anonymous_signin_required")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-own")

    assert key_resolution.hosted_ai_ready() is True
    assert key_resolution.resolve_api_key().provider == "claude"


# Regressions: own-key and existing signed-in users


def test_own_key_user_never_starts_an_anonymous_session(monkeypatch, mint):
    monkeypatch.setattr(targets_mod, "TargetsConfig", lambda: _Config())
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env")
    assert key_resolution.resolve_api_key().api_key == "sk-ant-env"

    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.setattr(secret_mod, "SecretStoreService", lambda: type(
        "Store", (), {"get_secret": lambda self, name: "sk-ant-keyring"
                      if name == "ANTHROPIC_API_KEY" else None})())
    resolution = key_resolution.resolve_api_key()
    assert resolution.provider == "claude" and resolution.api_key == "sk-ant-keyring"
    assert mint.calls == []


def test_own_key_user_status_does_not_start_a_session(monkeypatch, mint):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env")

    status = AccountService().status(False, include_access=True)

    assert mint.calls == []
    assert status["signed_in"] is False and status["has_session"] is False
    assert status["signin_prompt"] == "none"


def test_signed_in_user_resolution_is_unchanged(monkeypatch, mint):
    monkeypatch.setattr(targets_mod, "TargetsConfig", lambda: _Config())
    _signed_in(monkeypatch)

    resolution = key_resolution.resolve_api_key()

    assert (resolution.provider, resolution.api_key) == ("readyset", SIGNED_TOKEN)
    key_resolution.require_monitoring_session()
    assert key_resolution.hosted_ai_ready() is True
    assert mint.calls == []


def test_signed_in_status_without_access_is_unchanged(monkeypatch, mint):
    _signed_in(monkeypatch)
    monkeypatch.setattr(account_session, "account_metadata", lambda: {
        "user_id": "user-1", "email": "person@example.com", "analytics_account_id": "rdst_account_x"})

    assert AccountService().status(include_quota=False) == {
        "signed_in": True, "user_id": "user-1", "email": "person@example.com",
        "analytics_account_id": "rdst_account_x"}
    assert mint.calls == []


def test_signed_in_status_reports_no_prompt(monkeypatch, mint):
    _signed_in(monkeypatch)
    monkeypatch.setattr(account_session, "account_metadata", lambda: {
        "user_id": "user-1", "email": "person@example.com", "analytics_account_id": "rdst_account_x"})
    requested: list[str] = []

    def get(url, **kwargs):
        requested.append(url)
        return _Response(200, {"model": "m", "quota": {"limit_microusd": 5_000_000,
                                                       "remaining_microusd": 4_000_000}})

    monkeypatch.setattr("features.account.service.requests.get", get)
    status = AccountService().status(False, include_access=True)

    assert status["signed_in"] is True and status["is_anonymous"] is False
    assert status["signin_prompt"] == "none" and status["percent_used"] == 20.0
    assert status["days_remaining"] is None and status["trial_days"] is None


# /api/account/status


def _inference_status(**fields: Any) -> dict[str, Any]:
    return {"model": "m", "quota": {"limit_microusd": 1_000_000, "remaining_microusd": 100_000},
            "is_anonymous": True, "limit_microusd": 1_000_000, "used_microusd": 900_000,
            "percent_used": 90.0, "machine_first_seen_at": int(time.time()) - 10 * 86400,
            "trial_days": 30, "days_remaining": 20, **fields}


@pytest.mark.asyncio
async def test_account_status_route_reports_anonymous_access_and_caches(monkeypatch, mint):
    from features.account.api import routes
    from features.account.service import account_service

    calls: list[str] = []

    def get(url, **kwargs):
        calls.append(url)
        return _Response(200, _inference_status(signin_prompt="budget"))

    monkeypatch.setattr("features.account.service.requests.get", get)
    monkeypatch.setattr(account_service, "_access_cache", None)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1") as client:
        first = (await client.get("/api/account/status")).json()
        second = (await client.get("/api/account/status")).json()

    assert first == second
    assert len(calls) == 1 and calls[0].endswith("/v1/inference/status")
    assert first["signed_in"] is False and first["has_session"] is True
    assert first["is_anonymous"] is True and first["signin_prompt"] == "budget"
    assert first["percent_used"] == 90.0 and first["days_remaining"] == 20
    assert first["trial_days"] == 30 and first["signin_reason"] is None
    assert len(mint.calls) == 1


def test_status_day_30_is_required_and_blocks_hosted_ai(monkeypatch, mint):
    monkeypatch.setattr("features.account.service.requests.get", lambda url, **kw: _Response(
        200, _inference_status(signin_prompt="required", days_remaining=0)))

    status = AccountService().status(False, include_access=True)

    assert status["signin_prompt"] == "required"
    assert status["signin_reason"] == "anonymous_signin_required"
    with pytest.raises(LLMError) as raised:
        key_resolution.resolve_api_key()
    assert raised.value.code == "LOGIN_REQUIRED"


def test_status_for_a_machine_that_signed_in_before_is_required(mint):
    mint.response = _Response(409, {"reason": "machine_linked_signin_required"})

    status = AccountService().status(False, include_access=True)

    assert status["signed_in"] is False and status["has_session"] is False
    assert status["signin_prompt"] == "required"
    assert status["signin_reason"] == "machine_linked_signin_required"


def test_status_computes_the_prompt_when_the_key_service_omits_it(monkeypatch, mint):
    old = {key: value for key, value in _inference_status().items()
           if key in ("model", "quota")}
    monkeypatch.setattr("features.account.service.requests.get",
                        lambda url, **kw: _Response(200, old))

    status = AccountService().status(False, include_access=True)

    assert status["percent_used"] == 90.0 and status["signin_prompt"] == "budget"
    assert status["trial_days"] == 30 and status["days_remaining"] == 29


def test_status_offline_falls_back_to_the_local_prompt(monkeypatch, mint):
    import requests

    def offline(url, **kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr("features.account.service.requests.get", offline)
    status = AccountService().status(False, include_access=True)

    assert status["is_anonymous"] is True and status["signin_prompt"] == "none"


# Sign-in upgrade


def test_sign_in_from_anonymous_links_and_reports(monkeypatch, mint):
    key_resolution.resolve_api_key()
    account_session.record_signin_required("anonymous_signin_required")
    service = AccountService()
    calls: list[tuple[str, dict[str, Any]]] = []
    linked: list[dict[str, Any]] = []
    monkeypatch.setattr(product_telemetry, "account_linked", lambda **kw: linked.append(kw))

    def post(path: str, body: dict[str, Any]) -> dict[str, Any]:
        calls.append((path, body))
        if path == "/account-auth/start":
            return {"login_id": "login-1", "state": "state-1", "auth_url": "https://a",
                    "publishable_key": "pk", "callback_url": "https://c", "expires_in": 600}
        if path == "/account-auth/result":
            return {"status": "ready",
                    "tokens": {"access_token": SIGNED_TOKEN, "refresh_token": "r2"},
                    "account": {"user_id": "anon-user", "email": "person@example.com"}}
        return {}

    monkeypatch.setattr(service, "_post", post)
    service.start_login("http://127.0.0.1:9000/account-login")
    assert calls[0][1]["anonymous_access_token"] == ANON_TOKEN

    assert service.login_status("login-1")["state"] == "success"
    assert linked == [{"from_anonymous": True, "days_since_first_seen": 1}]
    assert account_session.is_anonymous_session() is False
    assert not account_session.account_metadata().get("is_anonymous")
    assert account_session.signin_required() is None


def test_signed_in_start_login_sends_no_anonymous_token(monkeypatch):
    _signed_in(monkeypatch)
    service = AccountService()
    bodies: list[dict[str, Any]] = []

    def post(path, body):
        bodies.append(body)
        return {"login_id": "l", "state": "s", "auth_url": "a", "publishable_key": "p",
                "callback_url": "c", "expires_in": 600}

    monkeypatch.setattr(service, "_post", post)
    service.start_login("http://127.0.0.1:9000/account-login")

    assert "anonymous_access_token" not in bodies[0]


def _start_context(service: AccountService) -> None:
    service._post = lambda path, body: {  # type: ignore[method-assign]
        "login_id": "l", "state": "s", "auth_url": "a", "publishable_key": "p",
        "callback_url": "c", "expires_in": 600}


def test_anonymous_start_login_hands_the_browser_the_session_to_upgrade(mint):
    key_resolution.resolve_api_key()
    service = AccountService()
    _start_context(service)

    result = service.start_login("http://127.0.0.1:9000/account-login")

    assert result["anonymous_session"] == {
        "access_token": ANON_TOKEN, "refresh_token": "anon-refresh"}
    assert "anonymous_session" not in service._logins["l"]
    assert "anonymous_session" not in service.login_context("l")


def test_anonymous_session_near_expiry_is_refreshed_before_the_browser_gets_it(
    monkeypatch, mint,
):
    key_resolution.resolve_api_key()
    session = account_session.load_session()
    account_session.save_session({**session, "expires_in": 120})
    fresh = _jwt({"sub": "anon-user", "is_anonymous": True, "n": 2})
    refreshed: list[bool] = []

    def refresh(*_args, **_kwargs):
        refreshed.append(True)
        account_session.save_session(
            {"access_token": fresh, "refresh_token": "anon-refresh-2", "expires_in": 3600})
        return fresh

    monkeypatch.setattr(account_session, "refresh_session", refresh)
    service = AccountService()
    _start_context(service)

    result = service.start_login("http://127.0.0.1:9000/account-login")

    assert refreshed == [True]
    assert result["anonymous_session"] == {
        "access_token": fresh, "refresh_token": "anon-refresh-2"}


def test_signed_in_start_login_returns_no_anonymous_session(monkeypatch):
    _signed_in(monkeypatch)
    service = AccountService()
    _start_context(service)

    assert "anonymous_session" not in service.start_login("http://127.0.0.1:9000/a")


# Telemetry


def test_anonymous_session_stays_a_device_only_person(mint):
    key_resolution.resolve_api_key()
    account_session.save_account_metadata({"analytics_account_id": "rdst_account_anon"})

    person = product_telemetry.identity("install-1")

    assert person == {"distinct_id": "install-1", "device_id": "install-1",
                      "account_kind": "device_only", "email": ""}
    assert product_telemetry.account_kind() == "anonymous"


@pytest.mark.parametrize(("parts", "expected"), [
    (("ssh_passphrase_required", "", "needs a passphrase"), "ssh_failed"),
    (("", "tls_verification_failed", "certificate verify failed"), "tls"),
    (("28P01", "", 'password authentication failed for user "app"'), "auth_failed"),
    (("", "", 'database "shop" does not exist'), "db_not_found"),
    (("", "", "permission denied for table orders"), "permission_denied"),
    (("", "", "connection timed out"), "timeout"),
    (("", "", "could not connect to server: Connection refused"), "host_unreachable"),
    (("", "", "something else"), "unknown"),
])
def test_connection_errors_map_to_a_fixed_class(parts, expected):
    assert product_telemetry.connection_error_class(*parts) == expected


def test_onboarding_events_carry_only_safe_fields(monkeypatch):
    sent: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(product_telemetry, "enabled", lambda: True)
    monkeypatch.setattr(product_telemetry, "capture",
                        lambda event, props=None, **kw: sent.append((event, props)))

    product_telemetry.database_add_test_completed("postgres", via="agent", ok=False,
                                                  error_class="auth_failed")
    product_telemetry.database_add_saved("mysql", via="bogus", is_first_database=True)
    product_telemetry.database_add_failed("postgresql", via="url", error_class="db.example.com")
    product_telemetry.monitoring_enable_failed("local_limit")
    product_telemetry.account_linked(from_anonymous=True, days_since_first_seen=3)

    assert sent == [
        ("database_add_test_completed", {"engine": "postgresql", "via": "agent", "ok": False,
                                         "error_class": "auth_failed"}),
        ("database_add_saved", {"engine": "mysql", "via": None, "is_first_database": True}),
        ("database_add_failed", {"engine": "postgresql", "via": "url",
                                 "error_class": "unknown"}),
        ("monitoring_enable_failed", {"reason": "local_limit"}),
        ("account_linked", {"from_anonymous": True, "days_since_first_seen": 3}),
    ]


def test_signin_prompt_change_is_sent_once_per_state(monkeypatch):
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(product_telemetry, "enabled", lambda: True)
    monkeypatch.setattr(product_telemetry, "capture",
                        lambda event, props=None, **kw: sent.append(props))

    for state in ("none", "none", "soft", "soft", "budget", "required", "bogus"):
        product_telemetry.signin_prompt_state(state)

    assert sent == [{"state": "soft"}, {"state": "budget"}, {"state": "required"}]


async def _configure(method: str, path: str, body: Any = None, via: str | None = None):
    from features.configure.api import routes

    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    headers = {"X-RDST-Via": via} if via else {}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.request(method, path, json=body, headers=headers)


@pytest.mark.usefixtures("run_blocking_inline")
@pytest.mark.parametrize(("persisted", "first"), [(True, True), (False, False)])
async def test_add_database_reports_persistence_and_sends_saved(monkeypatch, persisted, first):
    from features.configure.api import routes

    if not first:
        cfg = targets_mod.TargetsConfig()
        cfg.load()
        cfg.upsert("other", {"engine": "mysql", "host": "h", "port": 3306, "database": "d",
                             "user": "u"})
        cfg.save()
    monkeypatch.setattr(routes.SecretStoreService, "set_secret",
                        lambda self, name, value, persist=True: {"persisted": persisted})
    saved: list[dict[str, Any]] = []
    monkeypatch.setattr(product_telemetry, "database_add_saved",
                        lambda engine, **kw: saved.append({"engine": engine, **kw}))

    response = await _configure("POST", "/api/configure/targets", {
        "name": "prod", "target": {"engine": "postgresql", "host": "db.internal", "port": 5432,
                                   "database": "app", "user": "app", "password": "pw"}},
        via="agent")

    assert response.json()["success"] is True
    assert response.json()["persisted"] is persisted
    assert saved == [{"engine": "postgresql", "via": "agent", "is_first_database": first}]


@pytest.mark.usefixtures("run_blocking_inline")
async def test_connection_test_sends_its_outcome_class(monkeypatch):
    from unittest.mock import Mock

    from shared.ssh_tunnel import SshPassphraseRequired

    cfg = Mock()
    cfg.get.return_value = {"engine": "postgresql", "host": "db.internal", "port": 5432,
                            "database": "app", "user": "app", "password": "x",
                            "ssh": {"host": "jump.example.com", "key_path": "~/.ssh/k"}}
    monkeypatch.setattr("features.configure.service.ConfigureService._load_config",
                        lambda self: cfg)
    monkeypatch.setattr("features.configure.service.resolve_connection_params",
                        Mock(side_effect=SshPassphraseRequired("locked")))
    monkeypatch.setattr(product_telemetry, "enabled", lambda: True)
    monkeypatch.setattr(product_telemetry, "engine_of", lambda name: "postgresql")
    sent: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(product_telemetry, "capture",
                        lambda event, props=None, **kw: sent.append((event, props)))

    await _configure("POST", "/api/configure/targets/prod/test", via="url")

    assert sent == [("database_add_test_completed", {
        "engine": "postgresql", "via": "url", "ok": False, "error_class": "ssh_failed"})]


def test_refused_monitoring_enable_is_reported(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from features.monitor.api import routes
    from shared.monitor.monitor_store import MonitorStore

    store = MonitorStore(tmp_path / "monitor.db")
    monkeypatch.setattr(routes, "_store", lambda: store)
    monkeypatch.setattr(routes, "require_local_request", lambda request: None, raising=False)
    monkeypatch.setattr("shared.config.targets.TargetsConfig.load", lambda self: None)
    monkeypatch.setattr("shared.config.targets.TargetsConfig.get",
                        lambda self, name: {"tags": ["aurora", "role:reader"]})
    reasons: list[str] = []
    monkeypatch.setattr(product_telemetry, "monitoring_enable_failed", reasons.append)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/monitor")

    response = TestClient(app, client=("127.0.0.1", 50000)).put("/api/monitor/targets/reader/settings", json={"enabled": True})

    assert response.status_code == 409
    assert reasons == ["unavailable"]
