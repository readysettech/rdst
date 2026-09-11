from __future__ import annotations

import hashlib
from unittest.mock import MagicMock

import pytest

from features.account.api.routes import _browser_callback_page
from features.account.service import AccountService, AccountServiceError
from shared.cli.rdst_cli import RdstCLI


CONTEXT = {
    "login_id": "login-1",
    "state": "state-1",
    "auth_url": "https://example.supabase.co/auth/v1",
    "publishable_key": "publishable",
    "callback_url": "https://keyservice.example/account-auth/callback",
    "expires_in": 600,
}


def test_start_login_keeps_pickup_key_in_rdst(monkeypatch):
    service = AccountService()
    calls: list[tuple[str, dict[str, object]]] = []

    def fake_post(path: str, body: dict[str, object]):
        calls.append((path, body))
        return CONTEXT

    monkeypatch.setattr(service, "_post", fake_post)
    monkeypatch.setattr("features.account.service.secrets.token_urlsafe", lambda _: "pickup")

    result = service.start_login("http://127.0.0.1:9000/account-login")

    assert result == CONTEXT
    assert calls == [
        (
            "/account-auth/start",
            {
                "pickup_key_hash": hashlib.sha256(b"pickup").hexdigest(),
                "return_url": "http://127.0.0.1:9000/account-login",
            },
        )
    ]
    assert service._logins["login-1"]["pickup_key"] == "pickup"
    assert "pickup_key" not in result


def test_keyservice_error_preserves_rate_limit_status(monkeypatch):
    response = MagicMock()
    response.status_code = 429
    response.json.return_value = {
        "code": "rate_limited",
        "detail": "Too many sign-in attempts from this address. Try again later.",
    }
    monkeypatch.setattr(
        "features.account.service.requests.post", lambda *args, **kwargs: response
    )

    with pytest.raises(AccountServiceError) as raised:
        AccountService._post("/account-auth/start", {})

    assert raised.value.status_code == 429
    assert "Too many sign-in attempts" in str(raised.value)


@pytest.mark.parametrize("enabled", [True, False])
def test_complete_login_parks_then_picks_up_and_saves_session(monkeypatch, enabled):
    monkeypatch.setattr("shared.telemetry.telemetry.is_enabled", lambda: enabled)
    service = AccountService()
    service._logins["login-1"] = {
        **CONTEXT,
        "pickup_key": "pickup",
        "created_at": 0.0,
        "status_state": "running",
        "detail": "Waiting",
    }
    monkeypatch.setattr("features.account.service.time.monotonic", lambda: 1.0)
    calls: list[tuple[str, dict[str, object]]] = []
    saved: list[tuple[dict[str, object], dict[str, object]]] = []

    def fake_post(path: str, body: dict[str, object]):
        calls.append((path, body))
        if path == "/account-auth/complete":
            return {"status": "ready"}
        return {
            "status": "ready",
            "tokens": {"access_token": "access", "refresh_token": "refresh"},
            "account": {"user_id": "user-1", "email": "person@example.com"},
        }

    monkeypatch.setattr(service, "_post", fake_post)
    monkeypatch.setattr(
        "features.account.service.account_session.save_session",
        lambda tokens, account: saved.append((tokens, account)),
    )

    result = service.complete_login(
        "login-1", "state-1", "access", "refresh", 3600
    )

    assert calls == [
        (
            "/account-auth/complete",
            {
                "login_id": "login-1",
                "state": "state-1",
                "access_token": "access",
                "refresh_token": "refresh",
                "expires_in": 3600,
                "analytics_disabled": not enabled,
            },
        ),
        (
            "/account-auth/result",
            {"login_id": "login-1", "pickup_key": "pickup"},
        ),
        (
            "/account-auth/ack",
            {"login_id": "login-1", "pickup_key": "pickup"},
        ),
    ]
    assert saved == [
        (
            {"access_token": "access", "refresh_token": "refresh"},
            {"user_id": "user-1", "email": "person@example.com"},
        )
    ]
    assert result == {"state": "success", "detail": "Signed in to Readyset"}


def test_complete_login_rejects_mismatched_state(monkeypatch):
    service = AccountService()
    service._logins["login-1"] = {
        **CONTEXT,
        "pickup_key": "pickup",
        "created_at": 0.0,
        "status_state": "running",
        "detail": "Waiting",
    }
    monkeypatch.setattr("features.account.service.time.monotonic", lambda: 1.0)

    with pytest.raises(AccountServiceError, match="state does not match"):
        service.complete_login("login-1", "wrong", "access", "refresh", 3600)


def test_browser_callback_is_captured_for_the_initiating_login(monkeypatch):
    service = AccountService()
    service._logins["login-1"] = {
        **CONTEXT,
        "pickup_key": "pickup",
        "created_at": 0.0,
        "status_state": "running",
        "detail": "Waiting",
        "browser_callback": {"state": "pending"},
    }
    monkeypatch.setattr("features.account.service.time.monotonic", lambda: 1.0)

    assert service.browser_callback_status("login-1") == {"state": "pending"}

    result = service.record_browser_callback("login-1", code="oauth-code")

    assert result == {
        "state": "ready",
        "code": "oauth-code",
        "error": None,
        "error_description": None,
    }
    assert service.browser_callback_status("login-1") == result


def test_browser_callback_rejects_an_empty_result(monkeypatch):
    service = AccountService()
    service._logins["login-1"] = {
        **CONTEXT,
        "pickup_key": "pickup",
        "created_at": 0.0,
        "status_state": "running",
        "detail": "Waiting",
    }
    monkeypatch.setattr("features.account.service.time.monotonic", lambda: 1.0)

    with pytest.raises(AccountServiceError, match="neither an authorization code"):
        service.record_browser_callback("login-1")


def test_browser_callback_page_is_branded_and_actionable():
    page = _browser_callback_page(True)

    assert "Readyset" in page
    assert "You're signed in" in page
    assert "Return to the app and close this tab" in page
    assert "radial-gradient" in page


def test_account_status_prints_only_the_identity(monkeypatch):
    calls: list[bool] = []

    def fake_status(include_quota: bool = True):
        calls.append(include_quota)
        return {
            "signed_in": True,
            "email": "person@example.com",
            "model": "z-ai/glm-5.3-flash",
            "quota": {
                "remaining_microusd": 250_000,
                "limit_microusd": 250_000,
            },
        }

    monkeypatch.setattr(
        "features.account.service.account_service.status", fake_status
    )

    result = RdstCLI(client=MagicMock()).account("status")

    assert calls == [False]
    assert result.ok is True
    assert result.message == "Signed in as person@example.com."
