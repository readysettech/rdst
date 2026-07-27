"""Unit tests for EmailService.queue_report.

The API server calls this from a request handler, so it must never block on
verification the way the CLI's `send_report_with_verification` does.
"""

from __future__ import annotations

import pytest

from features.audit.email_service import EmailService


class _RecordingService(EmailService):
    """EmailService with the network calls replaced by scripted results."""

    def __init__(self, send_results=None, register_result=None):
        super().__init__(keyservice_url="http://keyservice.invalid")
        self._send_results = list(send_results or [])
        self._register_result = register_result or {"success": True, "verified": False}
        self.sends: list[str] = []
        self.registers: list[dict] = []

    def send_report(self, report_token, html_body, subject, **kwargs):
        self.sends.append(report_token)
        return self._send_results.pop(0)

    def register_email(self, email, **kwargs):
        self.registers.append({"email": email, **kwargs})
        return dict(self._register_result)

    def poll_verification(self, *args, **kwargs):  # pragma: no cover - guard
        raise AssertionError("queue_report must not block on verification")


def test_verified_token_sends_immediately():
    service = _RecordingService(send_results=[{"success": True}])

    result = service.queue_report("a@b.com", "<html/>", "Subject", report_token="tok")

    assert result["success"] is True
    assert result["queued"] is False
    assert service.sends == ["tok"]
    assert service.registers == []


def test_unverified_email_registers_with_the_report_payload():
    service = _RecordingService(
        register_result={"success": True, "verified": False, "report_queued": True},
    )

    result = service.queue_report("a@b.com", "<html/>", "Subject")

    assert result == {
        "success": True, "queued": True, "stale_token": False, "report_queued": True,
    }
    assert service.registers[0]["html"] == "<html/>"
    assert service.registers[0]["subject"] == "Subject"
    assert service.registers[0]["mode"] == "link"


def test_stale_token_falls_back_to_registration():
    service = _RecordingService(
        send_results=[{"success": False, "error": "Invalid report token"}],
        register_result={"success": True, "verified": False, "report_queued": True},
    )

    result = service.queue_report("a@b.com", "<html/>", "Subject", report_token="stale")

    assert result["stale_token"] is True
    assert result["queued"] is True
    assert service.sends == ["stale"]
    assert service.registers[0]["email"] == "a@b.com"


def test_registration_that_verifies_instantly_sends_with_the_new_token():
    service = _RecordingService(
        send_results=[{"success": True}],
        register_result={"success": True, "verified": True, "report_token": "fresh"},
    )

    result = service.queue_report("a@b.com", "<html/>", "Subject")

    assert result["success"] is True
    assert result["report_token"] == "fresh"
    assert service.sends == ["fresh"]


def test_keyservice_failure_surfaces_as_unsuccessful():
    service = _RecordingService(register_result={"success": False, "error": "boom"})

    result = service.queue_report("a@b.com", "<html/>", "Subject")

    assert result["success"] is False
    assert result["error"] == "boom"


def test_send_failure_that_is_not_a_stale_token_does_not_re_register():
    service = _RecordingService(send_results=[{"success": False, "error": "HTTP 500"}])

    result = service.queue_report("a@b.com", "<html/>", "Subject", report_token="tok")

    assert result["success"] is False
    assert service.registers == []


def test_queue_report_never_polls():
    service = _RecordingService(register_result={"success": True, "verified": False})

    # _RecordingService.poll_verification raises; a blocking implementation
    # would trip it here.
    service.queue_report("a@b.com", "<html/>", "Subject")

    with pytest.raises(AssertionError):
        service.poll_verification("a@b.com")
