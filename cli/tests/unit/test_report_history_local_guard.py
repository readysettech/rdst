from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from features.audit.api.routes import router as audit_router
from features.fleet.api.routes import router as fleet_router


def _client(host: str = "127.0.0.1") -> TestClient:
    app = FastAPI()
    app.include_router(audit_router, prefix="/api")
    app.include_router(fleet_router, prefix="/api")
    return TestClient(app, client=(host, 50_000))


def test_audit_history_rejects_non_loopback_clients():
    response = _client("203.0.113.10").get("/api/audit/runs")

    assert response.status_code == 403


def test_fleet_report_history_rejects_non_loopback_clients():
    response = _client("203.0.113.10").get("/api/fleet/snapshots")

    assert response.status_code == 403


def test_audit_detail_rejects_cross_site_browser_origin():
    response = _client().get(
        "/api/audit/runs/example",
        headers={"Origin": "https://attacker.example"},
    )

    assert response.status_code == 403


def test_fleet_delete_rejects_cross_site_browser_origin():
    response = _client().delete(
        "/api/fleet/snapshots/example",
        headers={"Origin": "https://attacker.example"},
    )

    assert response.status_code == 403


def test_fleet_diff_rejects_cross_site_browser_origin():
    response = _client().get(
        "/api/fleet/diff?baseline=before&current=after",
        headers={"Origin": "https://attacker.example"},
    )

    assert response.status_code == 403
