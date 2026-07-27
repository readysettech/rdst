"""Unit tests for the /api/settings/email route and its email hardening.

Covers the gate contract: loopback guard, format validation + normalization,
best-effort MX rejection that fails open, and client/server regex parity.
"""

from __future__ import annotations

import json
from pathlib import Path

import dns.exception
import dns.resolver
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from shared.api.routes import settings as settings_mod
from shared.config.targets import TargetsConfig

FIXTURE = (
    Path(__file__).parent / "fixtures" / "email_validation_cases.json"
)


class _StubEmailService:
    """Keyservice stand-in: scripted register/status responses so no test
    ever reaches the network."""

    register_result: dict = {"success": True, "verified": False}
    status_result: dict = {"verified": False}
    last_register: dict = {}

    def register_email(self, email, **kw):
        type(self).last_register = {"email": email, **kw}
        return dict(type(self).register_result)

    def check_status(self, email):
        return dict(type(self).status_result)


@pytest.fixture(autouse=True)
def stub_keyservice(monkeypatch):
    import features.audit.email_service as email_service_mod

    monkeypatch.setattr(email_service_mod, "EmailService", _StubEmailService)
    _StubEmailService.register_result = {"success": True, "verified": False}
    _StubEmailService.status_result = {"verified": False}
    _StubEmailService.last_register = {}
    return _StubEmailService


@pytest.fixture
def config_path(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    monkeypatch.setattr(
        settings_mod, "TargetsConfig", lambda: TargetsConfig(path=str(path))
    )
    return path


@pytest.fixture
def client(config_path, monkeypatch):
    # Treat every test request as loopback + same-host; the 403 guard paths are
    # exercised separately with the real guards.
    monkeypatch.setattr(settings_mod, "is_loopback_request", lambda request: True)
    monkeypatch.setattr(settings_mod, "require_local_request", lambda request: None)
    # Default: MX check passes. Individual tests override.
    monkeypatch.setattr(settings_mod, "_domain_has_mx", lambda domain, resolver=None: True)
    app = FastAPI()
    app.include_router(settings_mod.router, prefix="/api")
    return TestClient(app)


def _stored_email(config_path: Path) -> str | None:
    cfg = TargetsConfig(path=str(config_path))
    cfg.load()
    return cfg.get_email()


def test_get_email_empty(client, config_path):
    resp = client.get("/api/settings/email")
    assert resp.status_code == 200
    assert resp.json() == {"email": None, "first_name": None, "last_name": None, "verified": False}


def test_post_then_get_roundtrip(client, config_path):
    resp = client.post("/api/settings/email", json={"email": "mike@example.com"})
    assert resp.status_code == 200
    assert resp.json() == {
        "success": True, "email": "mike@example.com",
        "verified": False, "verification_started": True,
    }
    assert client.get("/api/settings/email").json() == {
        "email": "mike@example.com", "first_name": None, "last_name": None,
        "verified": False,
    }


def test_post_normalizes_trim_and_lowercase(client, config_path):
    resp = client.post("/api/settings/email", json={"email": "  Mike@Example.COM "})
    assert resp.status_code == 200
    assert resp.json()["email"] == "mike@example.com"
    assert _stored_email(config_path) == "mike@example.com"


def test_post_invalid_format_400(client, config_path):
    resp = client.post("/api/settings/email", json={"email": "not-an-email"})
    assert resp.status_code == 400
    assert _stored_email(config_path) is None


def test_post_rejects_nxdomain_400(client, config_path, monkeypatch):
    monkeypatch.setattr(settings_mod, "_domain_has_mx", lambda domain, resolver=None: False)
    resp = client.post("/api/settings/email", json={"email": "user@nope.invalid"})
    assert resp.status_code == 400
    assert "mail server" in resp.json()["detail"].lower()
    assert _stored_email(config_path) is None


def test_post_allows_when_mx_undeterminable(client, config_path, monkeypatch):
    # None == resolver unavailable / timeout / offline: never strand the user.
    monkeypatch.setattr(settings_mod, "_domain_has_mx", lambda domain, resolver=None: None)
    resp = client.post("/api/settings/email", json={"email": "user@example.com"})
    assert resp.status_code == 200
    assert _stored_email(config_path) == "user@example.com"


def test_get_email_forbidden_off_loopback(config_path, monkeypatch):
    monkeypatch.setattr(settings_mod, "is_loopback_request", lambda request: False)
    app = FastAPI()
    app.include_router(settings_mod.router, prefix="/api")
    resp = TestClient(app).get("/api/settings/email")
    assert resp.status_code == 403


def test_post_email_forbidden_off_loopback(config_path, monkeypatch):
    monkeypatch.setattr(settings_mod, "is_loopback_request", lambda request: False)
    app = FastAPI()
    app.include_router(settings_mod.router, prefix="/api")
    resp = TestClient(app).post("/api/settings/email", json={"email": "a@b.com"})
    assert resp.status_code == 403


# --- client/server regex parity ------------------------------------------------

def test_email_regex_parity_against_shared_fixture():
    cases = json.loads(FIXTURE.read_text())["cases"]
    assert cases, "fixture must not be empty"
    for case in cases:
        matched = bool(settings_mod._EMAIL_RE.match(case["email"]))
        assert matched == case["valid"], f"server regex disagrees on {case['email']!r}"


# --- gate/trial identity coherence ---------------------------------------------

def test_gate_email_is_primary_over_trial_email(tmp_path):
    cfg = TargetsConfig(path=str(tmp_path / "config.toml"))
    cfg.load()
    cfg.set_email("gate@x.com")
    cfg.set_trial_config({"email": "trial@y.com", "status": "active"})
    cfg.save()

    reloaded = TargetsConfig(path=str(tmp_path / "config.toml"))
    reloaded.load()
    # The [[emails]] primary is the identity; [trial].email is only a fallback.
    assert reloaded.get_email() == "gate@x.com"
    assert reloaded.get_trial_config().get("email") == "trial@y.com"


# --- _domain_has_mx behavior matrix (fake resolver, no network) -----------------

class _FakeResolver:
    def __init__(self, mx=None, a=None):
        self._mx = mx
        self._a = a

    def resolve(self, domain, rtype):
        value = self._mx if rtype == "MX" else self._a
        if isinstance(value, Exception):
            raise value
        if value is None:
            raise dns.resolver.NoAnswer()
        return value


def test_domain_has_mx_true_when_mx_present():
    assert settings_mod._domain_has_mx("x.com", _FakeResolver(mx=["mx1.x.com"])) is True


def test_domain_has_mx_false_on_nxdomain():
    assert (
        settings_mod._domain_has_mx("x.com", _FakeResolver(mx=dns.resolver.NXDOMAIN()))
        is False
    )


def test_domain_has_mx_allows_implicit_mx_via_a_record():
    r = _FakeResolver(mx=dns.resolver.NoAnswer(), a=["1.2.3.4"])
    assert settings_mod._domain_has_mx("x.com", r) is True


def test_domain_has_mx_false_when_no_mx_and_no_a():
    r = _FakeResolver(mx=dns.resolver.NoAnswer(), a=dns.resolver.NXDOMAIN())
    assert settings_mod._domain_has_mx("x.com", r) is False


def test_domain_has_mx_none_on_timeout():
    r = _FakeResolver(mx=dns.exception.Timeout())
    assert settings_mod._domain_has_mx("x.com", r) is None


class TestSignupFlow:

    def test_post_stores_machine_level_names_and_starts_verification(self, client, config_path):
        r = client.post("/api/settings/email", json={
            "email": "Ada@Example.com", "first_name": " Ada ", "last_name": "Lovelace",
        })
        assert r.status_code == 200
        assert r.json() == {
            "success": True, "email": "ada@example.com",
            "verified": False, "verification_started": True,
        }
        # Names never leave the machine: the keyservice payload is email-only.
        assert _StubEmailService.last_register == {"email": "ada@example.com"}
        cfg = TargetsConfig(path=str(config_path))
        cfg.load()
        identity = cfg.get_identity()
        assert identity["first_name"] == "Ada"
        assert identity["last_name"] == "Lovelace"

    def test_already_verified_email_passes_instantly(self, client, config_path):
        _StubEmailService.register_result = {
            "success": True, "verified": True, "report_token": "tok-1",
        }
        r = client.post("/api/settings/email", json={
            "email": "ada@example.com", "first_name": "Ada", "last_name": "L",
        })
        assert r.json()["verified"] is True
        cfg = TargetsConfig(path=str(config_path))
        cfg.load()
        assert cfg.get_identity()["verified"] is True
        assert cfg.get_token_for_email("ada@example.com") == "tok-1"

    def test_keyservice_unreachable_returns_service_error(self, client, config_path):
        _StubEmailService.register_result = {"success": False, "error": "boom"}
        r = client.post("/api/settings/email", json={
            "email": "ada@example.com", "first_name": "Ada", "last_name": "L",
        })
        body = r.json()
        assert r.status_code == 502
        assert "temporarily unavailable" in body["detail"]
        # The identity is still stored so a later retry needs no re-typing.
        cfg = TargetsConfig(path=str(config_path))
        cfg.load()
        assert cfg.get_identity()["email"] == "ada@example.com"

    def test_verify_poll_persists_token(self, client, config_path):
        client.post("/api/settings/email", json={
            "email": "ada@example.com", "first_name": "Ada", "last_name": "L",
        })
        _StubEmailService.status_result = {"verified": True, "report_token": "tok-9"}
        assert client.post("/api/settings/email/verify-poll").json() == {"verified": True}
        cfg = TargetsConfig(path=str(config_path))
        cfg.load()
        assert cfg.get_identity()["verified"] is True
        assert cfg.get_token_for_email("ada@example.com") == "tok-9"

    def test_verify_poll_without_email(self, client):
        assert client.post("/api/settings/email/verify-poll").json() == {"verified": False}

    def test_verify_poll_distinguishes_service_failure_from_pending(self, client):
        client.post("/api/settings/email", json={
            "email": "ada@example.com", "first_name": "Ada", "last_name": "L",
        })
        _StubEmailService.status_result = {
            "verified": False,
            "error": "HTTP 500",
        }

        response = client.post("/api/settings/email/verify-poll")

        assert response.status_code == 502
        assert "temporarily unavailable" in response.json()["detail"]

    def test_names_are_per_machine_across_email_swaps(self, client, config_path):
        client.post("/api/settings/email", json={
            "email": "one@example.com", "first_name": "Ada", "last_name": "Lovelace",
        })
        client.post("/api/settings/email", json={"email": "two@example.com"})
        cfg = TargetsConfig(path=str(config_path))
        cfg.load()
        identity = cfg.get_identity()
        assert identity["email"] == "two@example.com"
        assert identity["first_name"] == "Ada"
        assert identity["last_name"] == "Lovelace"
