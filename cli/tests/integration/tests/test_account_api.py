"""Integration tests for the local Readyset account sign-in routes."""

from __future__ import annotations

import pytest

from features.account.api import routes as account_routes
from features.account.service import AccountService


@pytest.fixture
def service(monkeypatch):
    service = AccountService()
    service._logins["login-1"] = {
        "login_id": "login-1",
        "state": "state-1",
        "auth_url": "https://example.supabase.co/auth/v1",
        "publishable_key": "publishable",
        "callback_url": "https://keyservice.example/account-auth/callback",
        "expires_in": 600,
        "pickup_key": "pickup",
        "created_at": 0.0,
        "status_state": "running",
        "detail": "Waiting",
        "browser_callback": {"state": "pending"},
    }
    monkeypatch.setattr("features.account.service.time.monotonic", lambda: 1.0)
    monkeypatch.setattr(account_routes, "account_service", service)
    signed_in: list[bool] = []
    monkeypatch.setattr(account_routes, "_signed_in", lambda: signed_in.append(True))
    service.signed_in_calls = signed_in
    return service


async def test_link_finishes_sign_in_after_the_app_lost_track_of_it(
    client, tmp_rdst_home, service, monkeypatch
):
    verifier = await client.post(
        "/api/account/login/login-1/verifier",
        json={"state": "state-1", "code_verifier": "verifier-1"},
    )
    assert verifier.status_code == 204, verifier.text
    monkeypatch.setattr(
        service, "finish_browser_login",
        lambda login_id: {"state": "success", "detail": "Signed in to Readyset"},
    )

    page = await client.get(
        "/api/account/oauth/callback", params={"login_id": "login-1", "code": "oauth-code"}
    )

    assert page.status_code == 200
    assert "You're signed in" in page.text
    assert service.signed_in_calls == [True]


async def test_link_page_reports_a_sign_in_that_did_not_finish(
    client, tmp_rdst_home, service, monkeypatch
):
    monkeypatch.setattr(
        service, "finish_browser_login",
        lambda login_id: {"state": "failed", "detail": "This sign-in expired. Start again."},
    )

    page = await client.get(
        "/api/account/oauth/callback", params={"login_id": "login-1", "code": "oauth-code"}
    )

    assert "You're signed in" not in page.text
    assert "Sign-in failed" in page.text
    assert "This sign-in expired. Start again. Return to RDST to try again." in page.text
    assert service.signed_in_calls == []


async def test_link_page_hands_back_to_the_app_without_a_verifier(
    client, tmp_rdst_home, service
):
    page = await client.get(
        "/api/account/oauth/callback", params={"login_id": "login-1", "code": "oauth-code"}
    )

    assert "You're signed in" not in page.text
    assert "Return to RDST to finish signing in" in page.text
    status = await client.get("/api/account/login/login-1/browser-callback")
    assert status.json()["state"] == "ready"
