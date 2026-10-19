"""Dev builds, CL builds and test runs never report to the production PostHog project."""

from __future__ import annotations

import pytest

from shared import keyservice
from shared import telemetry_manager as tm

PROD = tm.PRODUCTION_POSTHOG_KEY
RELEASE = "0.1.4321"


@pytest.fixture(autouse=True)
def _production_keyservice(monkeypatch):
    monkeypatch.setattr(keyservice, "keyservice_base_url", lambda: keyservice.DEFAULT_KEYSERVICE_URL)


@pytest.fixture
def outside_pytest(monkeypatch):
    """Resolve as a real process would: pytest itself is a test run."""
    monkeypatch.setattr(tm, "_test_run", lambda env: env.get("RDST_TESTING") == "1")


@pytest.mark.parametrize("version,release", [
    ("0.1.4321", True),
    ("1.2.3", True),
    ("0.0.0.dev0", False),
    ("0.1.0.dev20261019120000", False),
    ("0.1.0", False),  # a local desktop build without a build number
    ("0.0.7", False),
    ("0.1", False),
    ("", False),
    ("unknown", False),
])
def test_release_versions(version, release):
    assert tm.is_release_version(version) is release


def test_released_build_keeps_the_production_key(outside_pytest):
    assert tm.resolve_posthog_key({}, RELEASE) == PROD
    assert tm.posthog_project(PROD) == "production"


@pytest.mark.parametrize("version", ["0.0.0.dev0", "0.1.0.dev20261019120000", "0.1.0"])
def test_dev_build_reports_to_preview(outside_pytest, version):
    preview = tm.DEFAULT_PREVIEW_POSTHOG_KEY
    assert tm.resolve_posthog_key({}, version) == preview
    assert tm.resolve_posthog_key({"RDST_POSTHOG_KEY": PROD}, version) == preview
    assert tm.resolve_posthog_key({tm.PREVIEW_POSTHOG_KEY_ENV: "phc_preview"}, version) == "phc_preview"
    # An empty preview key turns it off.
    assert tm.resolve_posthog_key({tm.PREVIEW_POSTHOG_KEY_ENV: ""}, version) == ""


def test_testing_flag_routes_a_release_to_preview_or_nothing(outside_pytest):
    env = {"RDST_TESTING": "1"}
    assert tm.resolve_posthog_key(env, RELEASE) == ""
    assert tm.resolve_posthog_key({**env, tm.PREVIEW_POSTHOG_KEY_ENV: "phc_preview"}, RELEASE) == "phc_preview"


def test_pytest_never_uses_the_production_key():
    assert tm.resolve_posthog_key({}, RELEASE) == ""
    assert tm.resolve_posthog_key({"RDST_POSTHOG_KEY": PROD}, RELEASE) == ""
    assert tm.TelemetryManager.POSTHOG_API_KEY != PROD


def test_non_production_keyservice_is_not_production(outside_pytest, monkeypatch):
    # CL builds and flavors bake or point at another keyservice.
    monkeypatch.setattr(keyservice, "keyservice_base_url", lambda: "https://keyservice-pr-1.readyset.io")
    assert tm.resolve_posthog_key({}, RELEASE) == tm.DEFAULT_PREVIEW_POSTHOG_KEY


def test_explicit_keys_are_honored(outside_pytest):
    assert tm.resolve_posthog_key({"RDST_POSTHOG_KEY": "phc_preview"}, "0.0.0.dev0") == "phc_preview"
    assert tm.resolve_posthog_key({"RDST_POSTHOG_KEY": ""}, RELEASE) == ""
    assert tm.posthog_project("phc_preview") == "preview"
    assert tm.posthog_project("") == "none"


# Readyset's own people


@pytest.mark.parametrize("env,email,version,internal", [
    ({}, "", RELEASE, False),
    ({}, "person@example.com", RELEASE, False),
    ({}, "Dev@Readyset.io", RELEASE, True),
    ({"RDST_INTERNAL": "1"}, "", RELEASE, True),
    ({}, "", "0.0.0.dev0", True),
])
def test_internal_install(env, email, version, internal):
    assert tm.internal_install(email, env, version) is internal


def test_internal_people_route_to_preview(outside_pytest, monkeypatch):
    monkeypatch.delenv(tm.PREVIEW_POSTHOG_KEY_ENV, raising=False)
    assert tm.routed_posthog_key(PROD, False) == PROD
    assert tm.routed_posthog_key(PROD, True) == tm.DEFAULT_PREVIEW_POSTHOG_KEY
    assert tm.routed_posthog_key("phc_preview", True) == "phc_preview"
    # Without a preview key they stay in production, tagged internal_user.
    monkeypatch.setenv(tm.PREVIEW_POSTHOG_KEY_ENV, "")
    assert tm.routed_posthog_key(PROD, True) == PROD


def test_internal_events_go_to_the_preview_client(outside_pytest, monkeypatch):
    from shared import product_telemetry

    monkeypatch.delenv(tm.PREVIEW_POSTHOG_KEY_ENV, raising=False)
    sent: list[tuple[str, str, dict]] = []

    class PreviewClient:
        def __init__(self, key, **_):
            self.key = key

        def capture(self, distinct_id, event, properties):
            sent.append((self.key, event, properties))
            return "ok"

    class Module:
        api_key = PROD
        Client = PreviewClient

        @staticmethod
        def capture(distinct_id, event, properties):
            sent.append((PROD, event, properties))
            return "ok"

    monkeypatch.setattr(tm, "_get_posthog", lambda: Module)
    manager = tm.TelemetryManager()
    manager.POSTHOG_API_KEY = PROD
    manager._enabled = True
    manager._initialized = True
    manager._device_id = "install-1"
    monkeypatch.setattr(product_telemetry, "_telemetry", lambda: manager)
    monkeypatch.setattr(manager, "_enqueue_posthog_send", lambda send: send(False))

    for email, expected in (("person@example.com", PROD),
                            ("dev@readyset.io", tm.DEFAULT_PREVIEW_POSTHOG_KEY)):
        monkeypatch.setattr(product_telemetry, "identity", lambda _id, email=email: {
            "distinct_id": "acct", "device_id": "install-1", "account_kind": "signed_in",
            "email": email})
        monkeypatch.setattr(tm, "build_version", lambda: RELEASE)
        sent.clear()
        manager.track("monitoring_enabled", {})
        named = [item for item in sent if item[1] == "monitoring_enabled"]
        assert [key for key, _, _ in named] == [expected]
        assert named[0][2]["internal_user"] is (expected != PROD)
