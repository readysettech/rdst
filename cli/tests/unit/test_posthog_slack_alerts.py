"""PostHog -> Slack alert tests. Catches regressions where events
stop reaching Slack (like the wizard not tracking trial events)."""

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


PROBE_URL = "http://internal-probe.example/"


@pytest.fixture(autouse=True)
def _no_trial_token_leak():
    """The registration flows under test export RDST_TRIAL_TOKEN; restore the
    environment afterwards so the fake token can't bleed into other tests."""
    before = os.environ.get("RDST_TRIAL_TOKEN")
    yield
    if before is None:
        os.environ.pop("RDST_TRIAL_TOKEN", None)
    else:
        os.environ["RDST_TRIAL_TOKEN"] = before


@pytest.fixture
def temp_rdst_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_telemetry():
    with patch("shared.telemetry.telemetry") as mock_tel:
        with patch("features.configure.cli.wizard.telemetry", mock_tel):
            with patch("features.trial.service.telemetry", mock_tel):
                mock_tel.track = MagicMock()
                mock_tel.track_with_stats = MagicMock()
                yield mock_tel


def _run_wizard_registration(mock_telemetry, email="user@example.com", token="valid-trial-token-12345"):
    """Run the wizard trial flow with mocked HTTP + prompts. Returns the cfg mock."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "email_tier": "business",
        "limit_display": "$5.00",
    }

    mock_cfg = MagicMock()
    mock_cfg.is_trial_active.return_value = False

    # requests is lazy-imported inside _run_trial_registration
    mock_requests = MagicMock()
    mock_requests.post.return_value = mock_response

    from features.configure.cli.wizard import ConfigurationWizard

    wizard = ConfigurationWizard.__new__(ConfigurationWizard)
    wizard.console = MagicMock()

    with (
        patch("features.configure.cli.wizard.Prompt") as mock_prompt,
        patch.dict("sys.modules", {"requests": mock_requests}),
        # Keep the fake token out of os.environ and the real OS keyring.
        patch.object(
            ConfigurationWizard, "_persist_secret_to_keyring", return_value=True
        ),
    ):
        mock_prompt.ask.side_effect = [email, token]
        wizard._run_trial_registration(mock_cfg)

    return mock_cfg


class TestInstallationTracking:
    def _make_tm(self, temp_rdst_dir):
        from shared.telemetry_manager import TelemetryManager

        tm = TelemetryManager()
        tm._rdst_dir = temp_rdst_dir
        tm._enabled = True
        tm._initialized = True
        tm._device_id = "test-device-id"
        tm._get_auth_type = MagicMock(return_value="none")
        # The ingest key and probe host come from the environment, which only a
        # release build sets; stand in for one so the send paths stay exercised.
        tm.POSTHOG_API_KEY = "phc_test_key"
        tm.INTERNAL_INSTALL_PROBE_URL = PROBE_URL
        return tm

    @staticmethod
    def _mock_requests(*, status_code=200, side_effect=None):
        requests = MagicMock()
        session = requests.Session.return_value
        if side_effect is None:
            session.head.return_value = MagicMock(status_code=status_code)
        else:
            session.head.side_effect = side_effect
        return requests, session

    def test_installation_event_includes_display_name(self, temp_rdst_dir):
        tm = self._make_tm(temp_rdst_dir)

        with patch.object(tm, "track_with_stats") as mock_track:
            tm.track_installation(install_method="pip")

        mock_track.assert_called_once()
        event_name, props = mock_track.call_args[0]
        assert event_name == "installation"
        assert props["display_name"] == "RDST Installed"
        assert props["install_method"] == "pip"

    @pytest.mark.parametrize(
        "event",
        [
            "installation",
            "first_demo",
            "demo_provisioned",
            "first_analyze",
            "first_analyze_feedback",
            "feedback_submitted",
            "nps_response",
        ],
    )
    def test_every_posthog_event_includes_internal_user(
        self, temp_rdst_dir, event
    ):
        tm = self._make_tm(temp_rdst_dir)
        posthog = MagicMock()

        with (
            patch.object(tm, "_detect_internal_user", return_value=True),
            patch.object(tm, "_add_stored_email_properties"),
            patch("shared.telemetry_manager._get_posthog", return_value=posthog),
        ):
            tm.track(event, {"source": "cli"})
            tm.flush()

        properties = posthog.capture.call_args.kwargs["properties"]
        assert properties["internal_user"] is True

    def test_tailnet_probe_requires_canary_success(self, temp_rdst_dir):
        tm = self._make_tm(temp_rdst_dir)
        requests, session = self._mock_requests()

        with patch("shared.telemetry_manager._get_requests", return_value=requests):
            assert tm._detect_internal_user() is True

        assert session.trust_env is False
        session.head.assert_called_once_with(
            PROBE_URL,
            allow_redirects=False,
            timeout=(1, 1),
        )
        session.close.assert_called_once()

    def test_unset_probe_url_skips_the_network_entirely(self, temp_rdst_dir):
        tm = self._make_tm(temp_rdst_dir)
        tm.INTERNAL_INSTALL_PROBE_URL = ""
        requests, session = self._mock_requests()

        with patch("shared.telemetry_manager._get_requests", return_value=requests):
            assert tm._detect_internal_user() is False

        session.head.assert_not_called()

    def test_unset_ingest_key_sends_nothing(self, temp_rdst_dir):
        tm = self._make_tm(temp_rdst_dir)
        tm.POSTHOG_API_KEY = ""
        posthog = MagicMock()

        with patch("shared.telemetry_manager._get_posthog", return_value=posthog):
            tm.track("installation", {"source": "cli"})
            tm.flush()

        posthog.capture.assert_not_called()
        posthog.identify.assert_not_called()

    @pytest.mark.parametrize("status_code", [302, 503])
    def test_tailnet_probe_rejects_unexpected_response(
        self, temp_rdst_dir, status_code
    ):
        tm = self._make_tm(temp_rdst_dir)
        requests, _ = self._mock_requests(status_code=status_code)

        with patch("shared.telemetry_manager._get_requests", return_value=requests):
            assert tm._detect_internal_user() is False

    def test_tailnet_probe_failure_is_external(self, temp_rdst_dir):
        tm = self._make_tm(temp_rdst_dir)
        requests, _ = self._mock_requests(
            side_effect=OSError("network unavailable")
        )

        with patch("shared.telemetry_manager._get_requests", return_value=requests):
            assert tm._detect_internal_user() is False

    def test_tailnet_probe_deadline_includes_requests_import(self, temp_rdst_dir):
        tm = self._make_tm(temp_rdst_dir)
        tm.INTERNAL_INSTALL_TOTAL_TIMEOUT_SECONDS = 0.02
        import_started = threading.Event()
        release_import = threading.Event()
        worker_finished = threading.Event()

        def load_requests():
            import_started.set()
            release_import.wait(timeout=1)
            worker_finished.set()

        try:
            with patch(
                "shared.telemetry_manager._get_requests", side_effect=load_requests
            ):
                started_at = time.monotonic()
                assert tm._detect_internal_user() is False
                elapsed = time.monotonic() - started_at
                assert import_started.wait(timeout=1)
                assert elapsed < 0.2
        finally:
            release_import.set()
            assert worker_finished.wait(timeout=1)

    def test_events_wait_in_memory_until_internal_user_is_resolved(
        self, temp_rdst_dir
    ):
        tm = self._make_tm(temp_rdst_dir)
        resolution_started = threading.Event()
        release_resolution = threading.Event()
        posthog = MagicMock()

        def detect():
            resolution_started.set()
            release_resolution.wait(timeout=1)
            return True

        with (
            patch.object(tm, "_detect_internal_user", side_effect=detect) as mock_detect,
            patch.object(tm, "_add_stored_email_properties"),
            patch("shared.telemetry_manager._get_posthog", return_value=posthog),
        ):
            started_at = time.monotonic()
            tm.track("first_demo", {"source": "web"})
            tm.track("feedback_submitted", {"source": "cli"})
            assert time.monotonic() - started_at < 0.2
            assert resolution_started.wait(timeout=1)
            posthog.capture.assert_not_called()
            release_resolution.set()
            tm.flush()

        mock_detect.assert_called_once()
        assert posthog.capture.call_count == 2
        assert all(
            call.kwargs["properties"]["internal_user"] is True
            for call in posthog.capture.call_args_list
        )

    def test_late_canary_success_cannot_change_timed_out_result(
        self, temp_rdst_dir
    ):
        tm = self._make_tm(temp_rdst_dir)
        tm.INTERNAL_INSTALL_TOTAL_TIMEOUT_SECONDS = 0.02
        import_started = threading.Event()
        release_import = threading.Event()
        late_response_finished = threading.Event()
        requests, session = self._mock_requests()
        session.close.side_effect = late_response_finished.set
        posthog = MagicMock()

        def load_requests():
            import_started.set()
            release_import.wait(timeout=1)
            return requests

        try:
            with (
                patch(
                    "shared.telemetry_manager._get_requests",
                    side_effect=load_requests,
                ) as mock_get_requests,
                patch.object(tm, "_add_stored_email_properties"),
                patch("shared.telemetry_manager._get_posthog", return_value=posthog),
            ):
                tm.track("first_demo")
                assert import_started.wait(timeout=1)
                tm.flush()
                release_import.set()
                assert late_response_finished.wait(timeout=1)
                tm.track("feedback_submitted")
                tm.flush()

            mock_get_requests.assert_called_once()
            assert posthog.capture.call_count == 2
            assert all(
                call.kwargs["properties"]["internal_user"] is False
                for call in posthog.capture.call_args_list
            )
        finally:
            release_import.set()

    def test_flush_waits_for_resolver_registration(self, temp_rdst_dir):
        tm = self._make_tm(temp_rdst_dir)
        start_entered = threading.Event()
        allow_start = threading.Event()
        flush_finished = threading.Event()
        posthog = MagicMock()
        original_start_background = tm._start_background

        def delayed_start(target):
            start_entered.set()
            allow_start.wait(timeout=1)
            original_start_background(target)

        def flush():
            tm.flush()
            flush_finished.set()

        try:
            with (
                patch.object(tm, "_start_background", side_effect=delayed_start),
                patch.object(tm, "_detect_internal_user", return_value=True),
                patch.object(tm, "_add_stored_email_properties"),
                patch("shared.telemetry_manager._get_posthog", return_value=posthog),
            ):
                track_thread = threading.Thread(target=lambda: tm.track("first_demo"))
                track_thread.start()
                assert start_entered.wait(timeout=1)
                flush_thread = threading.Thread(target=flush)
                flush_thread.start()
                assert not flush_finished.wait(timeout=0.05)
                allow_start.set()
                track_thread.join(timeout=1)
                flush_thread.join(timeout=1)
        finally:
            allow_start.set()

        assert flush_finished.is_set()
        posthog.capture.assert_called_once()

    def test_resolver_failure_drains_pending_events_as_external(
        self, temp_rdst_dir
    ):
        tm = self._make_tm(temp_rdst_dir)
        posthog = MagicMock()

        with (
            patch.object(
                tm, "_detect_internal_user", side_effect=RuntimeError("probe failed")
            ),
            patch.object(tm, "_add_stored_email_properties"),
            patch("shared.telemetry_manager._get_posthog", return_value=posthog),
        ):
            tm.track("first_demo")
            tm.flush()
            tm.track("feedback_submitted")
            tm.flush()

        assert posthog.capture.call_count == 2
        assert all(
            call.kwargs["properties"]["internal_user"] is False
            for call in posthog.capture.call_args_list
        )
        assert tm._internal_user_result is False
        assert not tm._pending_posthog_sends

    def test_flush_has_overall_background_deadline(self, temp_rdst_dir):
        tm = self._make_tm(temp_rdst_dir)
        tm.BACKGROUND_FLUSH_TIMEOUT_SECONDS = 0.02
        release = threading.Event()

        try:
            tm._start_background(release.wait)
            with patch("shared.telemetry_manager._get_posthog", return_value=None):
                started_at = time.monotonic()
                tm.flush()
                assert time.monotonic() - started_at < 0.2
        finally:
            release.set()
            tm._wait_for_background_threads()

    def test_flush_waits_for_posthog_capture(self, temp_rdst_dir):
        tm = self._make_tm(temp_rdst_dir)
        capture_started = threading.Event()
        release_capture = threading.Event()
        order = []

        def capture(*args, **kwargs):
            capture_started.set()
            release_capture.wait(timeout=1)
            order.append("capture")

        posthog = MagicMock()
        posthog.capture.side_effect = capture
        posthog.flush.side_effect = lambda: order.append("flush")

        with (
            patch.object(tm, "_detect_internal_user", return_value=False),
            patch.object(tm, "_add_stored_email_properties"),
            patch("shared.telemetry_manager._get_posthog", return_value=posthog),
        ):
            tm._schedule_installation_tracking()
            assert capture_started.wait(timeout=1)
            timer = threading.Timer(0.05, release_capture.set)
            timer.start()
            tm.flush()
            timer.join(timeout=1)

        assert order == ["capture", "flush"]
        with tm._background_threads_lock:
            assert not tm._background_threads

    def test_background_start_failure_runs_tracked_work_synchronously(
        self, temp_rdst_dir
    ):
        tm = self._make_tm(temp_rdst_dir)
        target = MagicMock()

        with patch.object(threading.Thread, "start", side_effect=RuntimeError):
            tm._start_background(target)

        target.assert_called_once()
        with tm._background_threads_lock:
            assert not tm._background_threads

    def test_failed_device_id_persistence_does_not_repeat_install_tracking(
        self, temp_rdst_dir
    ):
        from shared.telemetry_manager import TelemetryManager

        tm = TelemetryManager()
        tm._rdst_dir = temp_rdst_dir

        with (
            patch.object(Path, "write_text", side_effect=OSError("read-only home")),
            patch.object(tm, "_schedule_installation_tracking") as mock_schedule,
        ):
            assert tm.device_id

        mock_schedule.assert_not_called()


class TestWizardTrialEvents:
    """The wizard has its own registration flow separate from TrialService.
    These tests catch the bug where the wizard wasn't tracking events at all."""

    def test_registration_tracks_to_posthog(self, mock_telemetry):
        _run_wizard_registration(mock_telemetry)

        mock_telemetry.track.assert_any_call(
            "trial_registration",
            {
                "display_name": "RDST Token Requested",
                "email": "user@example.com",
                "email_domain": "example.com",
                "email_tier": "business",
                "limit_display": "$5.00",
                "source": "cli",
            },
        )

    def test_activation_tracks_to_posthog(self, mock_telemetry):
        _run_wizard_registration(mock_telemetry)

        mock_telemetry.track.assert_any_call(
            "trial_activated",
            {
                "display_name": "RDST Token Confirmed",
                "email": "user@example.com",
                "email_domain": "example.com",
                "email_tier": "business",
                "source": "cli",
            },
        )

    def test_persists_email_to_config(self, mock_telemetry):
        mock_cfg = _run_wizard_registration(mock_telemetry)
        mock_cfg.set_email.assert_called_once_with("user@example.com")


class TestFirstAnalyze:
    def _make_tm(self, temp_rdst_dir, successful_analyzes=0):
        from shared.telemetry_manager import TelemetryManager

        stats_file = temp_rdst_dir / "stats.json"
        stats_file.write_text(json.dumps({"successful_analyzes": successful_analyzes}))

        tm = TelemetryManager()
        tm._rdst_dir = temp_rdst_dir
        tm._enabled = True
        tm._initialized = True
        tm._device_id = "test-device-id"
        tm._stats = {"successful_analyzes": successful_analyzes}
        return tm

    def test_includes_email_when_available(self, temp_rdst_dir):
        mock_cfg = MagicMock()
        mock_cfg.get_email.return_value = "john@xyzcompany.com"
        mock_cfg.get_trial_config.return_value = {"email": "john@xyzcompany.com"}

        tm = self._make_tm(temp_rdst_dir)

        with (
            patch.object(tm, "track") as mock_track,
            patch("shared.config.targets.create_targets_config") as mock_cfg_factory,
        ):
            mock_cfg_factory.return_value = mock_cfg
            tm.track_analyze(query_hash="abc123", success=True, target_engine="postgresql", duration_ms=500)

            calls = [c for c in mock_track.call_args_list if c[0][0] == "first_analyze"]
            assert len(calls) == 1
            props = calls[0][0][1]
            assert props["display_name"] == "RDST First Analyze"
            assert props["email"] == "john@xyzcompany.com"
            assert props["email_domain"] == "xyzcompany.com"

    def test_omits_email_when_not_in_config(self, temp_rdst_dir):
        config_file = temp_rdst_dir / "config.toml"
        config_file.write_text("")

        tm = self._make_tm(temp_rdst_dir)

        with patch.object(tm, "track") as mock_track:
            tm.track_analyze(query_hash="abc123", success=True, target_engine="postgresql", duration_ms=500)

            calls = [c for c in mock_track.call_args_list if c[0][0] == "first_analyze"]
            assert len(calls) == 1
            assert "email" not in calls[0][0][1]

    def test_does_not_fire_on_subsequent_runs(self, temp_rdst_dir):
        tm = self._make_tm(temp_rdst_dir, successful_analyzes=5)

        with patch.object(tm, "track") as mock_track:
            tm.track_analyze(query_hash="abc123", success=True, target_engine="postgresql", duration_ms=500)

            calls = [c for c in mock_track.call_args_list if c[0][0] == "first_analyze"]
            assert len(calls) == 0


class TestEmailPersistence:
    def test_stores_on_first_call(self):
        from shared.config.targets import TargetsConfig
        cfg = TargetsConfig.__new__(TargetsConfig)
        cfg._data = {}
        cfg._path = None

        cfg.set_email("user@example.com")
        assert cfg.get_email() == "user@example.com"

    def test_overwrites_existing(self):
        """set_email always overwrites — users can change their report email."""
        from shared.config.targets import TargetsConfig
        cfg = TargetsConfig.__new__(TargetsConfig)
        cfg._data = {"email": "first@example.com"}
        cfg._path = None

        cfg.set_email("second@example.com")
        assert cfg.get_email() == "second@example.com"

    def test_ignores_empty_string(self):
        from shared.config.targets import TargetsConfig
        cfg = TargetsConfig.__new__(TargetsConfig)
        cfg._data = {}
        cfg._path = None

        cfg.set_email("")
        assert cfg.get_email() is None

    def test_feedback_saves_email_and_tracks_posthog_event(self, temp_rdst_dir):
        from shared.telemetry_manager import TelemetryManager

        tm = TelemetryManager()
        tm._rdst_dir = temp_rdst_dir
        tm._enabled = True
        tm._initialized = True
        tm._device_id = "test-device-id"

        mock_cfg = MagicMock()
        mock_cfg.get_email.return_value = None  # no existing email

        with (
            patch.object(tm, "track_with_stats") as mock_track,
            patch("shared.config.targets.create_targets_config") as mock_cfg_factory,
        ):
            mock_cfg_factory.return_value = mock_cfg
            tm.submit_feedback(
                reason="Great tool!",
                query_hash="abc123",
                query_sql="SELECT 1",
                suggestion_text="Keep it up",
                sentiment="positive",
                email="feedback@example.com",
                include_query=True,
            )

        mock_cfg.set_email.assert_called_once_with("feedback@example.com")
        mock_cfg.save.assert_called_once()
        event, properties = mock_track.call_args.args
        assert event == "feedback_submitted"
        assert properties["reason"] == "Great tool!"
        assert properties["query_hash"] == "abc123"
        assert properties["query_sql"] == "SELECT 1"
        assert properties["suggestion_text"] == "Keep it up"
        assert properties["email"] == "feedback@example.com"

    def test_feedback_without_email_is_not_sent(self, temp_rdst_dir):
        from shared.telemetry_manager import TelemetryManager

        tm = TelemetryManager()
        tm._rdst_dir = temp_rdst_dir

        with patch.object(tm, "track_with_stats") as mock_track:
            with pytest.raises(ValueError, match="Email is required"):
                tm.submit_feedback(reason="Anonymous feedback", email=None)

        mock_track.assert_not_called()


class TestTrialServiceDisplayName:
    """TrialService is used by the web API path (not the wizard).
    Verify display_name is set there too."""

    def test_registration_has_display_name(self, mock_telemetry):
        import asyncio as _asyncio

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"email_tier": "business", "limit_display": "$5.00"}

        class MockAsyncClient:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def post(self, *args, **kwargs):
                return mock_response

        mock_httpx = MagicMock()
        mock_httpx.AsyncClient.return_value = MockAsyncClient()

        with patch.dict("sys.modules", {"httpx": mock_httpx}):
            import importlib
            import features.trial.service as ts_mod
            importlib.reload(ts_mod)
            from features.trial.service import TrialService

            svc = TrialService(secret_store=MagicMock())
            with patch.object(svc, "_load_config") as mock_load:
                mock_load.return_value = MagicMock()
                result = _asyncio.new_event_loop().run_until_complete(svc.register("user@example.com"))

                assert result.success
                mock_telemetry.track.assert_any_call(
                    "trial_registration",
                    {
                        "display_name": "RDST Token Requested",
                        "email": "user@example.com",
                        "email_domain": "example.com",
                        "email_tier": "business",
                        "limit_display": "$5.00",
                        "source": "cli",
                    },
                )

    def test_activation_has_display_name(self, mock_telemetry):
        import asyncio as _asyncio

        from features.trial.service import TrialService

        svc = TrialService(secret_store=MagicMock())
        with patch.object(svc, "_load_config", return_value=MagicMock()):
            result = _asyncio.new_event_loop().run_until_complete(
                svc.activate(token="valid-trial-token-12345", email="user@example.com", email_tier="business")
            )

            assert result.success
            mock_telemetry.track.assert_any_call(
                "trial_activated",
                {
                    "display_name": "RDST Token Confirmed",
                    "email": "user@example.com",
                    "email_domain": "example.com",
                    "email_tier": "business",
                    "source": "cli",
                },
            )


class TestTrackFirstEventOnce:
    """Helper used by audit/fleet to gate PostHog alerts on a stats flag."""

    def _make_tm(self, temp_rdst_dir, stats=None):
        from shared.telemetry_manager import TelemetryManager

        stats = stats or {}
        (temp_rdst_dir / "stats.json").write_text(json.dumps(stats))

        tm = TelemetryManager()
        tm._rdst_dir = temp_rdst_dir
        tm._enabled = True
        tm._initialized = True
        tm._device_id = "test-device-id"
        tm._stats = dict(stats)
        return tm

    def test_fires_when_flag_unset(self, temp_rdst_dir):
        tm = self._make_tm(temp_rdst_dir)

        with patch.object(tm, "track") as mock_track:
            fired = tm.track_first_event_once(
                "first_audit",
                flag="first_audit_fired",
                properties={"display_name": "First Audit: tpch"},
            )

        assert fired is True
        mock_track.assert_called_once_with(
            "first_audit", {"display_name": "First Audit: tpch"}
        )
        assert tm._get_stats().get("first_audit_fired") == 1

    def test_does_not_fire_when_flag_set(self, temp_rdst_dir):
        tm = self._make_tm(temp_rdst_dir, stats={"first_audit_fired": 1})

        with patch.object(tm, "track") as mock_track:
            fired = tm.track_first_event_once(
                "first_audit",
                flag="first_audit_fired",
                properties={"display_name": "First Audit: tpch"},
            )

        assert fired is False
        mock_track.assert_not_called()

    def test_increments_only_on_first_fire(self, temp_rdst_dir):
        tm = self._make_tm(temp_rdst_dir)

        with patch.object(tm, "track"):
            tm.track_first_event_once(
                "first_fleet_audit",
                flag="first_fleet_audit_fired",
                properties={"foo": "bar"},
            )
            tm.track_first_event_once(
                "first_fleet_audit",
                flag="first_fleet_audit_fired",
                properties={"foo": "bar"},
            )

        assert tm._get_stats().get("first_fleet_audit_fired") == 1
