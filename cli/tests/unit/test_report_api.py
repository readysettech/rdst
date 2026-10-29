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


def test_report_with_telemetry_off_says_how_to_send_it():
    with patch("shared.telemetry.telemetry.is_enabled", return_value=False):
        response = _client().post(
            "/api/report", json={"reason": "Great tool", "email": "a@example.com"}
        )

    assert response.status_code == 200
    assert response.json() == {
        "success": False,
        "error": "Feedback isn't sent while telemetry is turned off. "
        "To send it, start RDST without RDST_TELEMETRY=off.",
    }


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
