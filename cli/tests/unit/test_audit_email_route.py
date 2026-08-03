"""Unit tests for POST /api/audit/runs/{run_id}/email.

Covers the delivery contract the web "email me this report" button relies
on: a verified recipient gets the report now, an unverified one gets a
verification link carrying the report, and a run with no report is refused
rather than emailed empty.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response

from features.audit.api import routes as audit_routes
from features.audit.report import delivery as delivery_mod
from shared.api import guards
from shared.config.targets import TargetsConfig

pytestmark = pytest.mark.usefixtures("run_blocking_inline")


class _StubEmailService:
    """Keyservice stand-in: scripted queue_report responses so no test ever
    reaches the network."""

    queue_result: dict = {"success": True, "queued": False, "report_token": "tok-1"}
    calls: list[dict] = []

    def queue_report(self, email, html, subject, report_token=None, **kwargs):
        type(self).calls.append(
            {
                "email": email,
                "html": html,
                "subject": subject,
                "report_token": report_token,
            }
        )
        return dict(type(self).queue_result)


@pytest.fixture(autouse=True)
def stub_keyservice(monkeypatch):
    import features.audit.email_service as email_service_mod

    monkeypatch.setattr(email_service_mod, "EmailService", _StubEmailService)
    _StubEmailService.queue_result = {
        "success": True, "queued": False, "report_token": "tok-1",
    }
    _StubEmailService.calls = []
    return _StubEmailService


@pytest.fixture
def config_path(tmp_rdst_home, monkeypatch) -> Path:
    path = tmp_rdst_home / "config.toml"
    monkeypatch.setattr(
        delivery_mod, "TargetsConfig", lambda: TargetsConfig(path=str(path))
    )
    return path


def _config(config_path: Path) -> TargetsConfig:
    cfg = TargetsConfig(path=str(config_path))
    cfg.load()
    return cfg


def _seed_snapshot(tmp_rdst_home: Path, snapshot_id: str = "audit_prod_1") -> str:
    directory = tmp_rdst_home / "fleet" / "snapshots"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{snapshot_id}.json").write_text(
        json.dumps(
            {
                "target_name": "prod",
                "engine": "postgresql",
                "audited_at": "2026-07-20T10:00:00+00:00",
                "health_analysis": {"health_score": 72, "health_label": "fair"},
            }
        )
    )
    return snapshot_id


def _seed_capture(tmp_rdst_home: Path, run_id: str = "20260720_100000_ab") -> str:
    directory = tmp_rdst_home / "audits" / "prod"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{run_id}.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "target_name": "prod",
                "started_at": "2026-07-20T10:00:00+00:00",
                "queries": [],
                "analysis": None,
            }
        )
    )
    return run_id


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(audit_routes.router, prefix="/api")
    return app


class _Client:
    """Small synchronous facade over HTTPX's in-process ASGI transport."""

    def __init__(self, app: FastAPI):
        self.app = app

    def post(self, path: str, **kwargs) -> Response:
        async def request() -> Response:
            transport = ASGITransport(app=self.app)
            async with AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                return await client.post(path, **kwargs)

        return asyncio.run(request())


@pytest.fixture
def client(config_path, monkeypatch) -> _Client:
    # Treat every test request as loopback + same-host; the 403 guard paths
    # are exercised separately with the real guards.
    monkeypatch.setattr(guards, "is_loopback_request", lambda request: True)
    monkeypatch.setattr(guards, "same_host_from_headers", lambda request: True)
    return _Client(_app())


def test_verified_email_sends_immediately(client, tmp_rdst_home, config_path):
    run_id = _seed_snapshot(tmp_rdst_home)
    cfg = _config(config_path)
    cfg.set_email("ada@example.com")
    cfg.add_verified_email("ada@example.com", "tok-1")
    cfg.save()

    response = client.post(f"/api/audit/runs/{run_id}/email", json={})

    assert response.status_code == 200
    assert response.json() == {
        "status": "sent", "email": "ada@example.com", "verified": True,
    }
    call = _StubEmailService.calls[0]
    assert call["report_token"] == "tok-1"
    assert call["subject"] == "RDST Audit Report: prod"
    assert ">72</text>" in call["html"]


def test_unverified_email_registers_with_the_report(client, tmp_rdst_home, config_path):
    run_id = _seed_snapshot(tmp_rdst_home)
    _StubEmailService.queue_result = {"success": True, "queued": True}
    cfg = _config(config_path)
    cfg.set_email("ada@example.com")
    cfg.save()

    response = client.post(f"/api/audit/runs/{run_id}/email", json={})

    assert response.json() == {
        "status": "verification_sent", "email": "ada@example.com", "verified": False,
    }
    call = _StubEmailService.calls[0]
    assert call["report_token"] is None
    assert call["subject"] == "RDST Audit Report: prod"
    assert call["html"].startswith("<!DOCTYPE html>")


def test_body_email_overrides_the_stored_identity(client, tmp_rdst_home, config_path):
    run_id = _seed_snapshot(tmp_rdst_home)
    cfg = _config(config_path)
    cfg.set_email("ada@example.com")
    cfg.save()

    response = client.post(
        f"/api/audit/runs/{run_id}/email", json={"email": "  Grace@Example.COM "}
    )

    assert response.json()["email"] == "grace@example.com"
    assert _StubEmailService.calls[0]["email"] == "grace@example.com"


def test_stale_token_is_dropped_from_local_config(client, tmp_rdst_home, config_path):
    run_id = _seed_snapshot(tmp_rdst_home)
    _StubEmailService.queue_result = {
        "success": True, "queued": True, "stale_token": True,
    }
    cfg = _config(config_path)
    cfg.set_email("ada@example.com")
    cfg.add_verified_email("ada@example.com", "stale-tok")
    cfg.save()

    response = client.post(f"/api/audit/runs/{run_id}/email", json={})

    assert response.json()["status"] == "verification_sent"
    assert _config(config_path).get_token_for_email("ada@example.com") is None


def test_successful_send_persists_the_report_token(client, tmp_rdst_home, config_path):
    run_id = _seed_snapshot(tmp_rdst_home)
    _StubEmailService.queue_result = {
        "success": True, "queued": False, "report_token": "tok-fresh",
    }

    response = client.post(
        f"/api/audit/runs/{run_id}/email", json={"email": "ada@example.com"}
    )

    assert response.json()["verified"] is True
    cfg = _config(config_path)
    assert cfg.get_token_for_email("ada@example.com") == "tok-fresh"
    # First email on the machine also becomes the stored identity.
    assert cfg.get_email() == "ada@example.com"


def test_keyservice_failure_reports_unavailable(client, tmp_rdst_home, config_path):
    run_id = _seed_snapshot(tmp_rdst_home)
    _StubEmailService.queue_result = {"success": False, "error": "boom"}
    cfg = _config(config_path)
    cfg.set_email("ada@example.com")
    cfg.save()

    response = client.post(f"/api/audit/runs/{run_id}/email", json={})

    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"


@pytest.mark.parametrize(
    "body", [{}, {"email": "not-an-email"}], ids=["missing", "malformed"]
)
def test_unusable_recipient_is_rejected(client, tmp_rdst_home, config_path, body):
    run_id = _seed_snapshot(tmp_rdst_home)

    response = client.post(f"/api/audit/runs/{run_id}/email", json=body)

    assert response.status_code == 400
    assert _StubEmailService.calls == []


def test_run_without_a_report_is_refused(client, tmp_rdst_home, config_path):
    run_id = _seed_capture(tmp_rdst_home)
    cfg = _config(config_path)
    cfg.set_email("ada@example.com")
    cfg.add_verified_email("ada@example.com", "tok-1")
    cfg.save()

    response = client.post(f"/api/audit/runs/{run_id}/email", json={})

    assert response.status_code == 409
    assert _StubEmailService.calls == []


def test_unknown_run_is_refused(client, tmp_rdst_home, config_path):
    response = client.post("/api/audit/runs/nope/email", json={})

    assert response.status_code == 409


@pytest.mark.parametrize(
    ("loopback", "same_host"),
    [(False, True), (True, False)],
    ids=["off-loopback", "cross-origin"],
)
def test_forbidden_for_non_local_callers(
    config_path, tmp_rdst_home, monkeypatch, loopback, same_host
):
    monkeypatch.setattr(guards, "is_loopback_request", lambda request: loopback)
    monkeypatch.setattr(
        guards, "same_host_from_headers", lambda request: same_host
    )
    run_id = _seed_snapshot(tmp_rdst_home)

    response = _Client(_app()).post(f"/api/audit/runs/{run_id}/email", json={})

    assert response.status_code == 403
    assert _StubEmailService.calls == []
