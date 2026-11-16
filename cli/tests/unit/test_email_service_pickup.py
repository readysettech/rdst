"""EmailService's half of report verification: the pickup secret it registers with
is the only thing that collects the report credential afterwards."""

from __future__ import annotations

import json
import multiprocessing
import os
import stat
import time

import httpx2 as httpx
import pytest

from features.audit import email_service
from features.audit.email_service import EmailService
from shared.constants import rdst_data_dir


@pytest.fixture(autouse=True)
def enable_hosted_reports(monkeypatch):
    monkeypatch.setenv("RDST_HOSTED_REPORTS", "enabled")


@pytest.fixture
def keyservice(monkeypatch):
    calls: list[httpx.Request] = []
    replies: dict[str, dict] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        reply = replies[request.url.path]
        return httpx.Response(200, json=reply(request) if callable(reply) else reply)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    return calls, replies


def test_status_presents_the_pickup_the_registration_sent(keyservice):
    calls, replies = keyservice
    replies["/register-report"] = {"success": True, "verified": False}
    replies["/report-status"] = {"verified": True, "report_token": "rt-1"}
    service = EmailService(keyservice_url="https://keyservice.test")

    service.register_email("a@corp.com")
    status = service.check_status("a@corp.com")

    pickup = json.loads(calls[0].content)["pickup"]
    assert len(pickup) >= 32
    assert calls[1].headers["x-rdst-report-pickup"] == pickup
    assert "pickup" not in str(calls[1].url)
    assert status["report_token"] == "rt-1"
    # Collected once, so the secret is forgotten.
    service.check_status("a@corp.com")
    assert "x-rdst-report-pickup" not in calls[2].headers


def test_waiting_pickup_is_kept_until_the_click(keyservice):
    calls, replies = keyservice
    replies["/register-report"] = {"success": True, "verified": False}
    replies["/report-status"] = {"verified": False, "report_token": None, "expired": False}
    service = EmailService(keyservice_url="https://keyservice.test")

    service.register_email("a@corp.com")
    service.check_status("a@corp.com")
    service.check_status("a@corp.com")

    assert calls[1].headers["x-rdst-report-pickup"] == calls[2].headers["x-rdst-report-pickup"]


def test_delivered_report_leaves_no_pickup_behind(keyservice):
    calls, replies = keyservice
    replies["/register-report"] = {"success": True, "verified": True, "report_sent": True}
    replies["/report-status"] = {"verified": False, "report_token": None}
    service = EmailService(keyservice_url="https://keyservice.test")

    result = service.queue_report("a@corp.com", "<html/>", "Subject")
    service.check_status("a@corp.com")

    assert result == {"success": True, "report_token": None, "queued": False,
                      "stale_token": False}
    assert "x-rdst-report-pickup" not in calls[1].headers


def test_pickup_survives_a_restart_before_the_click(keyservice):
    calls, replies = keyservice
    replies["/register-report"] = {"success": True, "verified": False}
    replies["/report-status"] = {"verified": True, "report_token": "rt-1"}
    EmailService(keyservice_url="https://keyservice.test").register_email("a@corp.com")
    pickup = json.loads(calls[0].content)["pickup"]

    # A restarted process starts with nothing in memory; only the data dir carries over.
    status = EmailService(keyservice_url="https://keyservice.test").check_status("a@corp.com")

    assert calls[1].headers["x-rdst-report-pickup"] == pickup
    assert status["report_token"] == "rt-1"
    assert not (rdst_data_dir() / "report-pickups.json").exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
def test_pickup_file_is_private(keyservice):
    _, replies = keyservice
    replies["/register-report"] = {"success": True, "verified": False}

    EmailService(keyservice_url="https://keyservice.test").register_email("a@corp.com")

    path = rdst_data_dir() / "report-pickups.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
def test_expired_or_exposed_pickups_are_not_presented(keyservice):
    calls, replies = keyservice
    replies["/report-status"] = {"verified": False, "report_token": None}
    path = rdst_data_dir() / "report-pickups.json"
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    service = EmailService(keyservice_url="https://keyservice.test")

    path.write_text(json.dumps({"a@corp.com": [{"pickup": "p" * 43,
                                                "expires_at": time.time() - 1}]}))
    os.chmod(path, 0o600)
    service.check_status("a@corp.com")
    path.write_text(json.dumps({"a@corp.com": [{"pickup": "p" * 43,
                                                "expires_at": time.time() + 60}]}))
    os.chmod(path, 0o644)
    service.check_status("a@corp.com")

    assert all("x-rdst-report-pickup" not in call.headers for call in calls)


def test_clicking_an_earlier_email_still_completes_after_registering_again(keyservice):
    calls, replies = keyservice
    replies["/register-report"] = {"success": True, "verified": False}
    service = EmailService(keyservice_url="https://keyservice.test")
    service.register_email("a@corp.com")
    service.register_email("a@corp.com")
    first, second = (json.loads(call.content)["pickup"] for call in calls)
    # Only the first email's link was clicked, so only its pickup is approved.
    replies["/report-status"] = lambda request: (
        {"verified": True, "report_token": "rt-1"}
        if request.headers.get("x-rdst-report-pickup") == first
        else {"verified": False, "report_token": None, "expired": False})

    status = service.check_status("a@corp.com")

    assert status["report_token"] == "rt-1"
    presented = [call.headers["x-rdst-report-pickup"] for call in calls[2:]]
    assert presented == [second, first]
    # The account's credential is collected, so neither pickup is kept.
    assert not (rdst_data_dir() / "report-pickups.json").exists()


def _register_pickups(prefix: str, count: int, start) -> None:
    start.wait()
    for n in range(count):
        email_service._remember_pickup("a@corp.com", f"{prefix}-{n}".ljust(43, "x"))


def test_two_processes_registering_at_once_keep_every_pickup():
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    workers = [context.Process(target=_register_pickups, args=(prefix, 5, start))
               for prefix in ("web", "cli")]
    for worker in workers:
        worker.start()
    start.set()
    for worker in workers:
        worker.join(30)
        assert worker.exitcode == 0

    pickups = email_service._pickups_for("a@corp.com")

    assert len(pickups) == 10
    assert {pickup.split("-")[0] for pickup in pickups} == {"web", "cli"}


def test_status_check_presents_only_the_newest_pickups(keyservice):
    calls, replies = keyservice
    replies["/report-status"] = {"verified": False, "report_token": None, "expired": False}
    for n in range(5):
        email_service._remember_pickup("a@corp.com", f"pickup-{n}".ljust(43, "x"))

    EmailService(keyservice_url="https://keyservice.test").check_status("a@corp.com")

    presented = [call.headers["x-rdst-report-pickup"][:8] for call in calls]
    assert presented == ["pickup-4", "pickup-3", "pickup-2"]


def test_status_check_past_its_deadline_reads_as_pending(monkeypatch):
    def too_slow(self, coro):
        coro.close()
        raise TimeoutError

    monkeypatch.setattr(EmailService, "_sync", too_slow)

    status = EmailService(keyservice_url="https://keyservice.test").check_status("a@corp.com")

    assert status == {"verified": False, "pending": True}


def test_report_released_through_another_install_reads_as_delivered(keyservice):
    calls, replies = keyservice
    replies["/report-status"] = {"verified": False, "report_token": None, "expired": False,
                                 "report_released": True}
    email_service._remember_pickup("a@corp.com", "p" * 43)

    status = EmailService(keyservice_url="https://keyservice.test").check_status("a@corp.com")

    assert status["verified"] is True and status["report_token"] is None
    assert status["report_released"] is True


def test_pickup_is_stored_before_the_keyservice_is_asked(keyservice):
    calls, replies = keyservice
    stored_at_send = []

    def register(request):
        stored_at_send.append(email_service._pickups_for("a@corp.com"))
        return {"success": True, "verified": False}

    replies["/register-report"] = register

    EmailService(keyservice_url="https://keyservice.test").register_email("a@corp.com")

    assert stored_at_send == [[json.loads(calls[0].content)["pickup"]]]


def test_unwritable_pickup_store_fails_before_anything_is_sent(keyservice, monkeypatch):
    calls, _ = keyservice

    def unwritable(email, pickup):
        raise OSError("Read-only file system")

    monkeypatch.setattr(email_service, "_remember_pickup", unwritable)

    result = EmailService(keyservice_url="https://keyservice.test").register_email("a@corp.com")

    assert result["success"] is False
    assert "No email was sent" in result["error"] and "Read-only" in result["error"]
    assert calls == []
