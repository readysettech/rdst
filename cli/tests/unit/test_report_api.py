"""Contract tests for attributable web feedback submissions."""

from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from shared.api.routes import report as report_mod


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(report_mod.router, prefix="/api")
    return TestClient(app, client=("127.0.0.1", 54321))


def test_report_requires_email():
    response = _client().post("/api/report", json={"reason": "Great tool"})

    assert response.status_code == 422


def test_report_rejects_invalid_email():
    response = _client().post(
        "/api/report",
        json={"reason": "Great tool", "email": "not-an-email"},
    )

    assert response.status_code == 400


def test_report_normalizes_email_before_posthog_and_slack_workflow():
    with patch("shared.telemetry.telemetry.submit_feedback") as submit_feedback:
        response = _client().post(
            "/api/report",
            json={
                "reason": "Great tool",
                "email": "  Feedback@Example.COM ",
            },
        )

    assert response.status_code == 200
    assert response.json() == {"success": True, "error": None}
    submit_feedback.assert_called_once()
    assert submit_feedback.call_args.kwargs["email"] == "feedback@example.com"
    assert submit_feedback.call_args.kwargs["flags_used"] == ["web"]
