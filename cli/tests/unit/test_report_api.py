"""Contract tests for attributable web feedback submissions."""

from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from shared.api.app import create_app
from shared.api.routes import report as report_mod


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(report_mod.router, prefix="/api")
    return TestClient(
        app,
        base_url="http://127.0.0.1",
        client=("127.0.0.1", 50000),
    )


def test_cors_allows_loopback_origins_only():
    client = TestClient(
        create_app(),
        base_url="http://127.0.0.1",
        client=("127.0.0.1", 50000),
    )
    headers = {"Access-Control-Request-Method": "POST"}

    allowed = client.options(
        "/api/report",
        headers={**headers, "Origin": "http://localhost:3001"},
    )
    blocked = client.options(
        "/api/report",
        headers={**headers, "Origin": "https://attacker.example"},
    )

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:3001"
    assert blocked.status_code == 400
    assert "access-control-allow-origin" not in blocked.headers


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
    with patch("shared.telemetry.telemetry.submit_feedback",
               return_value=True) as submit_feedback:
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


def test_report_says_when_feedback_did_not_leave_the_machine():
    with patch("shared.telemetry.telemetry.submit_feedback", return_value=False):
        response = _client().post(
            "/api/report", json={"reason": "Great tool", "email": "a@example.com"}
        )

    assert response.status_code == 200
    assert response.json() == {
        "success": False,
        "error": "Couldn't send your feedback. Check your connection and try again.",
    }


def test_feedback_is_sent_with_telemetry_off():
    with patch("shared.telemetry.telemetry.is_enabled", return_value=False), patch(
        "shared.telemetry.telemetry.submit_feedback", return_value=True
    ) as submit_feedback:
        response = _client().post(
            "/api/report", json={"reason": "Great tool", "email": "a@example.com"}
        )

    assert response.status_code == 200
    assert response.json()["success"] is True
    submit_feedback.assert_called_once()


def test_feature_request_is_labelled_and_carries_the_chat():
    with patch("shared.telemetry.telemetry.submit_feedback") as submit_feedback:
        response = _client().post(
            "/api/report",
            json={
                "reason": "Export slow queries to CSV",
                "email": "user@example.com",
                "kind": "feature_request",
                "transcript": "User: can I export?\n\nRDST: Not yet.",
            },
        )

    assert response.status_code == 200
    kwargs = submit_feedback.call_args.kwargs
    assert kwargs["kind"] == "feature_request"
    assert kwargs["reason"].startswith("[Feature request] Export slow queries to CSV")
    assert "RDST: Not yet." in kwargs["reason"]


def test_chat_returns_reply_and_drafted_suggestion():
    text = (
        '{"reply": "Noted.", "suggestion": '
        '{"kind": "feature_request", "summary": "Add CSV export."}}'
    )
    with patch("shared.llm_manager.LLMManager.query", return_value={"text": text}) as query:
        response = _client().post(
            "/api/report/chat",
            json={"messages": [
                {"role": "user", "content": "How do I export?"},
                {"role": "assistant", "content": "You can't yet."},
                {"role": "user", "content": "I want CSV export"},
            ]},
        )

    assert response.json() == {
        "reply": "Noted.",
        "suggestion": {"kind": "feature_request", "summary": "Add CSV export."},
        "ai": True,
    }
    assert query.call_args.kwargs["user_query"] == "I want CSV export"
    assert len(query.call_args.kwargs["history"]) == 2


def test_chat_falls_back_to_the_app_guide_without_ai():
    with patch("shared.llm_manager.LLMManager.query", side_effect=RuntimeError("no key")):
        response = _client().post(
            "/api/report/chat",
            json={"messages": [{"role": "user", "content": "How do I turn on monitoring?"}]},
        )

    body = response.json()
    assert body["ai"] is False
    assert "monitoring" in body["reply"].lower()
    assert "rdst " not in body["reply"]


def test_chat_prompt_is_about_the_app_not_the_cli():
    prompt = report_mod.help_chat_system_prompt()

    assert "rdst analyze" not in prompt
    assert "rdst top" not in prompt


def test_long_chat_sends_only_recent_turns():
    turns = []
    for i in range(60):
        turns.append({"role": "user", "content": f"question {i}"})
        turns.append({"role": "assistant", "content": f"answer {i}"})
    turns.append({"role": "user", "content": "one more"})
    with patch(
        "shared.llm_manager.LLMManager.query", return_value={"text": '{"reply": "ok"}'}
    ) as query:
        response = _client().post("/api/report/chat", json={"messages": turns})

    assert response.json()["reply"] == "ok"
    history = query.call_args.kwargs["history"]
    assert len(history) <= 20
    assert history[0]["role"] == "user"
    assert history[-1]["content"] == "answer 59"


def test_report_forwards_user_reviewed_ai_output():
    with patch("shared.telemetry.telemetry.submit_feedback") as submit_feedback:
        response = _client().post(
            "/api/report",
            json={
                "reason": "This output exposed sensitive data",
                "feedback_kind": "ai_output",
                "feature": "analyze",
                "ai_output": "Generated answer reviewed by the user",
            },
        )

    assert response.status_code == 200
    assert submit_feedback.call_args.kwargs["feedback_kind"] == "ai_output"
    assert submit_feedback.call_args.kwargs["feature"] == "analyze"
    assert submit_feedback.call_args.kwargs["email"] == ""
    assert (
        submit_feedback.call_args.kwargs["ai_output"]
        == "Generated answer reviewed by the user"
    )


def test_report_surfaces_posthog_delivery_failure():
    with patch(
        "shared.telemetry.telemetry.submit_feedback",
        side_effect=RuntimeError("delivery failed"),
    ):
        response = _client().post(
            "/api/report",
            json={"reason": "Great tool", "email": "feedback@example.com"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "success": False,
        "error": "PostHog did not accept the feedback report. Please try again.",
    }


def test_report_rejects_hostile_browser_origin():
    with patch("shared.telemetry.telemetry.submit_feedback") as submit_feedback:
        response = _client().post(
            "/api/report",
            headers={"Origin": "https://attacker.example"},
            json={"reason": "Great tool", "email": "feedback@example.com"},
        )

    assert response.status_code == 403
    submit_feedback.assert_not_called()


def test_report_bounds_feedback_message_size():
    response = _client().post(
        "/api/report",
        json={"reason": "x" * 10_001, "email": "feedback@example.com"},
    )

    assert response.status_code == 422


def test_report_limits_ai_output_attachment_size():
    response = _client().post(
        "/api/report",
        json={
            "reason": "Bad output",
            "email": "feedback@example.com",
            "feedback_kind": "ai_output",
            "ai_output": "x" * 50_001,
        },
    )

    assert response.status_code == 422


def test_bug_report_with_a_long_help_chat_is_sent():
    with patch("shared.telemetry_manager.TelemetryManager._capture_explicit_feedback") as capture, \
            patch("shared.telemetry.telemetry.is_enabled", return_value=True):
        response = _client().post(
            "/api/report",
            json={
                "reason": "x" * 10_000,
                "email": "user@example.com",
                "kind": "bug",
                "transcript": "User: why?\n" * 2_000,
            },
        )

    assert response.json() == {"success": True, "error": None}
    assert capture.call_args.args[0]["reason"].startswith("[Bug report] ")


def test_report_explains_a_rejected_email():
    with patch(
        "shared.telemetry.telemetry.submit_feedback",
        side_effect=ValueError("Invalid email address"),
    ):
        response = _client().post(
            "/api/report", json={"reason": "Great tool", "email": "a@example.com"}
        )

    assert response.json() == {"success": False, "error": "Invalid email address"}

