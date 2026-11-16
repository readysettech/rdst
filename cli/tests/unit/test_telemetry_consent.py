from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from shared.api.routes import settings as settings_routes
from shared.config.targets import TargetsConfig
from shared.telemetry_manager import TelemetryManager


@pytest.fixture(autouse=True)
def store_build(monkeypatch):
    monkeypatch.setenv("RDST_CONSENT", "opt_in")


async def _request(app: FastAPI, method: str, path: str, **kwargs):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        return await client.request(method, path, **kwargs)


def test_missing_consent_is_pending_and_does_not_initialize_telemetry(
    tmp_path, monkeypatch
):
    telemetry = TelemetryManager()
    telemetry._rdst_dir = tmp_path
    monkeypatch.delenv("RDST_TESTING", raising=False)
    monkeypatch.delenv("RDST_TELEMETRY", raising=False)

    with (
        patch("shared.telemetry_manager._get_posthog") as get_posthog,
        patch("shared.telemetry_manager._get_sentry") as get_sentry,
        patch.object(telemetry, "_detect_internal_user") as detect_internal_user,
    ):
        telemetry.track("web_started")
        telemetry.report_crash(RuntimeError("boom"))
        telemetry.flush()

    assert telemetry.telemetry_consent() == "pending"
    assert telemetry.is_enabled() is False
    assert not (tmp_path / "device_id").exists()
    get_posthog.assert_not_called()
    get_sentry.assert_not_called()
    detect_internal_user.assert_not_called()


def test_sentry_sanitizer_removes_sql_credentials_and_local_values():
    marker = "postgres://admin:secret@db/private?sql=SELECT_sensitive"  # trufflehog:ignore
    event = {
        "event_id": "event-1",
        "message": marker,
        "request": {"url": marker},
        "breadcrumbs": [{"message": marker}],
        "extra": {"query": marker},
        "exception": {
            "values": [
                {
                    "type": "DatabaseError",
                    "value": marker,
                    "stacktrace": {
                        "frames": [
                            {
                                "filename": "/home/customer/private-project/service.py",
                                "function": "run",
                                "lineno": 12,
                                "context_line": marker,
                                "vars": {"dsn": marker},
                            },
                            {
                                "filename": r"C:\\Users\\customer\\private-project\\service.py",
                                "function": "handle",
                                "module": "rdst.service",
                                "lineno": 24,
                            },
                        ]
                    },
                }
            ]
        },
    }

    sanitized = TelemetryManager._sanitize_sentry_event(event, {})
    serialized = str(sanitized)

    assert marker not in serialized
    assert sanitized["event_id"] == "event-1"
    exception = sanitized["exception"]["values"][0]
    assert exception["type"] == "DatabaseError"
    assert exception["value"] == "RDST captured an error"
    assert exception["stacktrace"]["frames"] == [
        {"function": "run", "lineno": 12},
        {"function": "handle", "module": "rdst.service", "lineno": 24},
    ]


def test_explicit_grant_and_withdrawal_are_persisted(tmp_path, monkeypatch):
    telemetry = TelemetryManager()
    telemetry._rdst_dir = tmp_path
    monkeypatch.delenv("RDST_TESTING", raising=False)
    monkeypatch.delenv("RDST_TELEMETRY", raising=False)

    telemetry.set_telemetry_consent("granted")
    assert telemetry.telemetry_consent() == "granted"
    assert telemetry.is_enabled() is True

    telemetry.set_telemetry_consent("denied")
    assert telemetry.telemetry_consent() == "denied"
    assert telemetry.is_enabled() is False

    reloaded = TargetsConfig(path=str(tmp_path / "config.toml"))
    reloaded.load()
    assert reloaded.get_telemetry_consent() == "denied"
    assert reloaded.get_telemetry_consent_version() == telemetry.CONSENT_VERSION


def test_builds_outside_the_store_send_until_telemetry_is_turned_off(tmp_path, monkeypatch):
    monkeypatch.delenv("RDST_CONSENT")
    monkeypatch.delenv("RDST_TESTING", raising=False)
    monkeypatch.delenv("RDST_TELEMETRY", raising=False)
    telemetry = TelemetryManager()
    telemetry._rdst_dir = tmp_path
    assert telemetry.telemetry_consent() == "pending"
    assert telemetry.is_enabled() is True

    telemetry.set_telemetry_consent("denied")
    assert telemetry.is_enabled() is False

    (tmp_path / "config.toml").write_text("telemetry_enabled = false\n", encoding="utf-8")
    telemetry._enabled = None
    assert telemetry.is_enabled() is False


def test_legacy_true_is_not_consent_and_legacy_false_remains_denied(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text("telemetry_enabled = true\n", encoding="utf-8")
    config = TargetsConfig(path=str(config_path))
    config.load()
    assert config.get_telemetry_consent() == "pending"

    config_path.write_text("telemetry_enabled = false\n", encoding="utf-8")
    config = TargetsConfig(path=str(config_path))
    config.load()
    assert config.get_telemetry_consent() == "denied"


def test_unversioned_or_stale_grant_requires_consent_again(tmp_path, monkeypatch):
    telemetry = TelemetryManager()
    telemetry._rdst_dir = tmp_path
    monkeypatch.delenv("RDST_TESTING", raising=False)
    monkeypatch.delenv("RDST_TELEMETRY", raising=False)
    config_path = tmp_path / "config.toml"

    config_path.write_text(
        '[privacy]\ntelemetry_consent = "granted"\n', encoding="utf-8"
    )
    assert telemetry.telemetry_consent() == "pending"

    config_path.write_text(
        '[privacy]\ntelemetry_consent = "granted"\ntelemetry_consent_version = 0\n',
        encoding="utf-8",
    )
    assert telemetry.telemetry_consent() == "pending"

    config_path.write_text(
        '[privacy]\ntelemetry_consent = "granted"\ntelemetry_consent_version = true\n',
        encoding="utf-8",
    )
    assert telemetry.telemetry_consent() == "pending"


def test_withdrawal_discards_queued_posthog_send(tmp_path, monkeypatch):
    telemetry = TelemetryManager()
    telemetry._rdst_dir = tmp_path
    telemetry._enabled = True
    telemetry._initialized = True
    telemetry._device_id = "device"
    monkeypatch.setattr(telemetry, "POSTHOG_API_KEY", "phc_test")
    posthog = MagicMock()

    with (
        patch("shared.telemetry_manager._get_posthog", return_value=posthog),
        patch.object(telemetry, "_add_stored_email_properties"),
        patch.object(telemetry, "_start_background"),
    ):
        telemetry.track("queued")
        assert len(telemetry._pending_posthog_sends) == 1
        telemetry.set_telemetry_consent("denied")
        telemetry.flush()

    assert telemetry._pending_posthog_sends == []
    posthog.capture.assert_not_called()


def test_withdrawal_stops_a_batch_the_sdk_already_holds(monkeypatch):
    """A real PostHog client: its sender has taken the event and waits out its flush interval
    when diagnostics are withdrawn. Nothing is uploaded afterwards."""
    import time

    pytest.importorskip("posthog")  # optional; installed with the desktop backend
    import posthog.consumer
    from posthog import Client

    from shared.telemetry_manager import _discard_queued_posthog

    sent = []
    monkeypatch.setattr(posthog.consumer, "batch_post", lambda *a, **kw: sent.append(kw))
    monkeypatch.setattr(posthog.consumer, "_send_v1_batch", lambda *a, **kw: sent.append(kw))
    client = Client("phc_test", host="http://127.0.0.1:9", flush_interval=0.5, flush_at=100,
                    sync_mode=False)
    client.capture("held", distinct_id="device")
    deadline = time.monotonic() + 2
    while any(not lane.queue.empty() for lane in client._lanes) and time.monotonic() < deadline:
        time.sleep(0.01)
    _discard_queued_posthog(client)
    time.sleep(1.0)
    assert sent == []


def test_explicit_feedback_sends_without_enabling_routine_telemetry(
    tmp_path, monkeypatch
):
    telemetry = TelemetryManager()
    telemetry._rdst_dir = tmp_path
    monkeypatch.delenv("RDST_TESTING", raising=False)
    monkeypatch.delenv("RDST_TELEMETRY", raising=False)
    telemetry.set_telemetry_consent("denied")
    monkeypatch.setattr(telemetry, "POSTHOG_API_KEY", "phc_test")
    requests = MagicMock()

    with patch("shared.telemetry_manager._get_requests", return_value=requests):
        telemetry.submit_feedback(reason="Useful", email="user@example.com")

    assert telemetry.is_enabled() is False
    assert not (tmp_path / "device_id").exists()
    requests.post.assert_called_once()
    payload = requests.post.call_args.kwargs["json"]
    event = payload["batch"][0]
    assert event["event"] == "feedback_submitted"
    assert event["distinct_id"].startswith("feedback-")
    assert event["properties"]["email"] == "user@example.com"
    assert event["properties"]["$process_person_profile"] is False
    assert event["properties"]["$geoip_disable"] is True
    assert "device_id" not in event["properties"]
    assert "device_stats" not in event["properties"]


def test_explicit_feedback_rejects_oversized_attachments(tmp_path):
    telemetry = TelemetryManager()
    telemetry._rdst_dir = tmp_path

    with (
        patch("shared.telemetry_manager._get_requests") as get_requests,
        pytest.raises(ValueError, match="SQL attachment exceeds"),
    ):
        telemetry.submit_feedback(
            reason="Useful",
            email="user@example.com",
            include_query=True,
            query_sql="x" * 100_001,
        )

    get_requests.assert_not_called()


def test_explicit_feedback_reports_posthog_rejection(tmp_path):
    telemetry = TelemetryManager()
    telemetry._rdst_dir = tmp_path
    requests = MagicMock()
    requests.post.side_effect = TimeoutError("PostHog unavailable")

    with (
        patch("shared.telemetry_manager._get_requests", return_value=requests),
        patch("shared.config.targets.create_targets_config") as config_factory,
    ):
        assert telemetry.submit_feedback(reason="Useful", email="user@example.com") is False

    config_factory.assert_not_called()


def test_anonymous_ai_report_has_no_persistent_identity(tmp_path, monkeypatch):
    telemetry = TelemetryManager()
    telemetry._rdst_dir = tmp_path
    telemetry._device_id = "persistent-device"
    monkeypatch.setattr(telemetry, "POSTHOG_API_KEY", "phc_test")
    requests = MagicMock()

    with patch("shared.telemetry_manager._get_requests", return_value=requests):
        telemetry.submit_feedback(
            reason="Unsafe output",
            email=None,
            feedback_kind="ai_output",
        )

    properties = requests.post.call_args.kwargs["json"]["batch"][0]["properties"]
    assert properties["email"] == ""
    assert properties["has_email"] is False
    assert properties["$process_person_profile"] is False
    assert "device_id" not in properties
    assert "internal_user" not in properties
    assert "trial_email" not in properties
    assert "provider" not in properties


def test_settings_api_exposes_and_updates_consent(tmp_path, monkeypatch):
    telemetry = TelemetryManager()
    telemetry._rdst_dir = tmp_path
    monkeypatch.setattr(telemetry, "forced_off_reason", lambda: None)
    monkeypatch.setattr(telemetry, "track", MagicMock())
    monkeypatch.setattr(settings_routes, "require_local_request", lambda request: None)

    import shared.telemetry as telemetry_module

    monkeypatch.setattr(telemetry_module, "telemetry", telemetry)
    app = FastAPI()
    app.include_router(settings_routes.router, prefix="/api")

    response = asyncio.run(_request(app, "GET", "/api/settings/telemetry"))
    assert response.status_code == 200
    assert response.json() == {
        "consent": "pending",
        "enabled": False,
        "consent_version": 1,
        "consent_required": True,
        "forced_off_reason": None,
    }

    response = asyncio.run(
        _request(
            app,
            "PUT",
            "/api/settings/telemetry",
            json={"consent": "granted"},
        )
    )
    assert response.status_code == 200
    assert response.json()["consent"] == "granted"
    assert response.json()["enabled"] is True
    telemetry.track.assert_called_once_with(
        "telemetry_consent_granted", {"consent_version": 1}
    )


def test_reset_runtime_state_forgets_deleted_identity(tmp_path):
    telemetry = TelemetryManager()
    telemetry._rdst_dir = tmp_path
    telemetry._enabled = True
    telemetry._initialized = True
    telemetry._device_id = "deleted-device"
    telemetry._stats = {"total_analyzes": 3}
    telemetry._pending_posthog_sends.append(lambda _: None)

    telemetry.reset_runtime_state()

    assert telemetry._enabled is None
    assert telemetry._initialized is False
    assert telemetry._device_id is None
    assert telemetry._stats is None
    assert telemetry._pending_posthog_sends == []
