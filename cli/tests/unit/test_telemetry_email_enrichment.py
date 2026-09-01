"""Unit tests for telemetry identity and privacy behavior."""

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


def test_track_does_not_identify_or_attach_stored_email(tmp_path, monkeypatch):
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
    # Only a release build carries an ingest key; without one track() sends nothing.
    tm.POSTHOG_API_KEY = "phc_test_key"

    tm.track("email_captured", {"display_name": "RDST Email Captured", "source": "gate"})

    assert fake.identify_calls == []
    assert fake.capture_calls, "expected posthog.capture to be called"
    cap_distinct, cap_event, cap_props = fake.capture_calls[0]
    assert cap_distinct.startswith("rdst_anonymous_")
    assert cap_event == "email_captured"
    assert "email" not in cap_props
    assert "email_domain" not in cap_props
    assert cap_props["installation_id"] == "dev-telemetry-123"
    assert cap_props["$process_person_profile"] is False


def test_track_uses_pseudonymous_account_id_when_signed_in(tmp_path, monkeypatch):
    fake = _FakePosthog()
    monkeypatch.setattr(tm_mod, "_get_posthog", lambda: fake)
    monkeypatch.setattr(tm_mod.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(
        "shared.account_session.account_metadata",
        lambda: {"analytics_account_id": "rdst_account_hash"},
    )
    monkeypatch.setattr(
        "shared.account_session.is_signed_in_locally", lambda: True
    )

    tm = TelemetryManager()
    tm._rdst_dir = tmp_path
    tm._device_id = "dev-telemetry-123"
    tm._enabled = True
    tm._initialized = True
    tm.POSTHOG_API_KEY = "phc_test_key"

    tm.track("analyze_run", {"email": "person@example.com"})

    assert fake.identify_calls == []
    distinct_id, _, properties = fake.capture_calls[0]
    assert distinct_id == "rdst_account_hash"
    assert properties["analytics_account_id"] == "rdst_account_hash"
    assert properties["installation_id"] == "dev-telemetry-123"
    assert "email" not in properties
    assert "$process_person_profile" not in properties


def test_stale_account_metadata_is_not_used_after_session_is_cleared(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "shared.account_session.account_metadata",
        lambda: {"analytics_account_id": "stale_account_hash"},
    )
    monkeypatch.setattr(
        "shared.account_session.is_signed_in_locally", lambda: False
    )

    tm = TelemetryManager()
    tm._rdst_dir = tmp_path
    tm._device_id = "dev-telemetry-123"

    properties = tm._get_base_properties()

    assert "analytics_account_id" not in properties


def test_auth_type_reports_readyset_account(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("RDST_TRIAL_TOKEN", raising=False)
    monkeypatch.setattr(
        "shared.secret_store_service.SecretStoreService.get_secret",
        lambda _self, _name: None,
    )
    monkeypatch.setattr(
        "shared.account_session.is_signed_in_locally",
        lambda: True,
    )

    tm = TelemetryManager()
    tm._rdst_dir = tmp_path

    assert tm._get_auth_type() == "readyset_account"


def test_auth_type_ignores_retired_trial_state(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("RDST_TRIAL_TOKEN", "retired-token")
    monkeypatch.setattr(
        "shared.secret_store_service.SecretStoreService.get_secret",
        lambda _self, name: "retired-token" if name == "RDST_TRIAL_TOKEN" else None,
    )
    monkeypatch.setattr(
        "shared.account_session.is_signed_in_locally",
        lambda: True,
    )
    (tmp_path / "config.toml").write_text(
        '[trial]\ntoken = "retired-token"\nstatus = "active"\n',
        encoding="utf-8",
    )

    tm = TelemetryManager()
    tm._rdst_dir = tmp_path

    assert tm._get_auth_type() == "readyset_account"
