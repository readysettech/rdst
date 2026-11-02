"""`provider_oauth_step` events: the browser's return from the authorize screen and the
sign-in's end, each sent once per login."""

from __future__ import annotations

import pytest

from features.providers import identity
from features.providers.provider_oauth import ProviderBrokerError, ProviderOAuth
from shared import product_telemetry


class AuthError(RuntimeError):
    pass


@pytest.fixture
def events(monkeypatch):
    sent: list[tuple[str, str, bool, str | None]] = []

    def record(provider, stage, *, ok, error_class=None):
        sent.append((provider, stage, ok, error_class))

    monkeypatch.setattr(product_telemetry, "provider_oauth_step", record)
    monkeypatch.setattr(identity, "capture_provider_identity_async", lambda *_: None)
    return sent


def oauth(monkeypatch, replies: list[dict]) -> ProviderOAuth:
    client = ProviderOAuth("digitalocean", "DigitalOcean", ProviderBrokerError, AuthError)
    monkeypatch.setattr(client, "broker_post", lambda action, payload: replies.pop(0))
    client.register_login("login-1", {"mode": "broker", "pickup_key": "pk", "completion": ""})
    return client


def test_callback_then_signed_in_report_once(monkeypatch, events):
    ready = {"status": "ready", "tokens": {"access_token": "at", "expires_in": 3600}}
    client = oauth(monkeypatch, [ready, {"status": "pending"}])

    assert client.complete_login("login-1", "c-1")["state"] == "success"
    # A second visit to the return page and later polls add nothing.
    client.complete_login("login-1", "c-1")
    client.login_status("login-1")

    assert events == [
        ("digitalocean", "callback", True, None),
        ("digitalocean", "signed_in", True, None),
    ]


def test_expired_login_reports_failed_with_class(monkeypatch, events):
    client = oauth(monkeypatch, [{"status": "expired"}, {"status": "expired"}])

    client.login_status("login-1")
    client.mark_login("login-1", "failed", "again", "refused")

    assert events == [("digitalocean", "failed", False, "expired")]


def test_rejected_completion_reports_callback_failure(monkeypatch, events):
    client = ProviderOAuth("supabase", "Supabase", ProviderBrokerError, AuthError)

    def refuse(action, payload):
        raise ProviderBrokerError("bad completion")

    monkeypatch.setattr(client, "broker_post", refuse)
    client.register_login("login-2", {"mode": "broker", "pickup_key": "pk", "completion": ""})

    client.complete_login("login-2", "forged")

    assert events == [("supabase", "callback", False, "completion_rejected")]


def test_error_class_must_be_a_slug(monkeypatch):
    sent = []
    monkeypatch.setattr(product_telemetry, "enabled", lambda: True)
    monkeypatch.setattr(product_telemetry, "capture", lambda event, props: sent.append(props))

    product_telemetry.provider_oauth_step("supabase", "callback", ok=False,
                                          error_class="access_denied")
    product_telemetry.provider_oauth_step("supabase", "callback", ok=False,
                                          error_class="https://evil.example/?x=1")

    assert [props["error_class"] for props in sent] == ["access_denied", "unknown"]
