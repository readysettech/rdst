"""Unit tests for telemetry email enrichment + PostHog identify linkage."""

from __future__ import annotations

import shared.telemetry_manager as tm_mod
from shared.telemetry_manager import TelemetryManager


def _write_config(path, contents: str) -> None:
    path.write_text(contents)


def test_enrichment_adds_email_and_domain(tmp_path):
    _write_config(
        tmp_path / "config.toml",
        '[[emails]]\nemail = "mike@company.com"\nprimary = true\nverified = false\n',
    )
    tm = TelemetryManager()
    tm._rdst_dir = tmp_path
    props: dict = {}
    tm._add_stored_email_properties(props)
    assert props["email"] == "mike@company.com"
    assert props["email_domain"] == "company.com"


def test_primary_email_preferred_over_trial_email(tmp_path):
    _write_config(
        tmp_path / "config.toml",
        '[[emails]]\nemail = "gate@x.com"\nprimary = true\nverified = false\n\n'
        '[trial]\nemail = "trial@y.com"\nstatus = "active"\n',
    )
    tm = TelemetryManager()
    tm._rdst_dir = tmp_path
    props: dict = {}
    tm._add_stored_email_properties(props)
    assert props["email"] == "gate@x.com"


def test_trial_email_used_as_fallback_when_no_primary(tmp_path):
    _write_config(
        tmp_path / "config.toml",
        '[trial]\nemail = "trial@y.com"\nstatus = "active"\n',
    )
    tm = TelemetryManager()
    tm._rdst_dir = tmp_path
    props: dict = {}
    tm._add_stored_email_properties(props)
    assert props["email"] == "trial@y.com"
    assert props["email_domain"] == "y.com"


class _ImmediateThread:
    """Runs the target synchronously so the identify/capture path is observable."""

    def __init__(self, target=None, daemon=None, **_kwargs):
        self._target = target

    def start(self):
        if self._target:
            self._target()


class _FakePosthog:
    api_key = None
    host = None

    def __init__(self):
        self.identify_calls: list[tuple] = []
        self.capture_calls: list[tuple] = []

    def identify(self, distinct_id=None, properties=None):
        self.identify_calls.append((distinct_id, properties))

    def capture(self, distinct_id=None, event=None, properties=None):
        self.capture_calls.append((distinct_id, event, properties))


def test_track_identifies_device_with_email(tmp_path, monkeypatch):
    _write_config(
        tmp_path / "config.toml",
        '[[emails]]\nemail = "mike@company.com"\nprimary = true\nverified = false\n',
    )
    fake = _FakePosthog()
    monkeypatch.setattr(tm_mod, "_get_posthog", lambda: fake)
    monkeypatch.setattr(tm_mod.threading, "Thread", _ImmediateThread)

    tm = TelemetryManager()
    tm._rdst_dir = tmp_path
    tm._device_id = "dev-telemetry-123"
    tm._enabled = True
    tm._initialized = True

    tm.track("email_captured", {"display_name": "RDST Email Captured", "source": "gate"})

    assert fake.identify_calls, "expected posthog.identify to be called"
    distinct_id, properties = fake.identify_calls[0]
    assert distinct_id == "dev-telemetry-123"
    assert properties == {"email": "mike@company.com"}
    assert fake.capture_calls, "expected posthog.capture to be called"
    cap_distinct, cap_event, cap_props = fake.capture_calls[0]
    assert cap_distinct == "dev-telemetry-123"
    assert cap_event == "email_captured"
    assert cap_props["email"] == "mike@company.com"
    assert cap_props["email_domain"] == "company.com"
