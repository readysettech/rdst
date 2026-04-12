"""PostHog -> Slack alert tests. Catches regressions where events
stop reaching Slack (like the wizard not tracking trial events)."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


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
    ):
        mock_prompt.ask.side_effect = [email, token]
        wizard._run_trial_registration(mock_cfg)

    return mock_cfg


class TestInstallationDisplayName:
    def test_includes_display_name(self, temp_rdst_dir):
        with patch("shared.telemetry_manager.RDST_DATA_DIR", temp_rdst_dir):
            from shared.telemetry_manager import TelemetryManager

            tm = TelemetryManager()
            tm._rdst_dir = temp_rdst_dir
            tm._enabled = True
            tm._initialized = True
            tm._device_id = "test-device-id"

            with patch.object(tm, "track_with_stats") as mock_track:
                tm.track_installation(install_method="pip")

                mock_track.assert_called_once()
                event_name, props = mock_track.call_args[0]
                assert event_name == "installation"
                assert props["display_name"] == "RDST Installed"
                assert props["install_method"] == "pip"


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

        with patch("shared.telemetry_manager.RDST_DATA_DIR", temp_rdst_dir):
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

        with patch("shared.telemetry_manager.RDST_DATA_DIR", temp_rdst_dir):
            tm = self._make_tm(temp_rdst_dir)

            with patch.object(tm, "track") as mock_track:
                tm.track_analyze(query_hash="abc123", success=True, target_engine="postgresql", duration_ms=500)

                calls = [c for c in mock_track.call_args_list if c[0][0] == "first_analyze"]
                assert len(calls) == 1
                assert "email" not in calls[0][0][1]

    def test_does_not_fire_on_subsequent_runs(self, temp_rdst_dir):
        with patch("shared.telemetry_manager.RDST_DATA_DIR", temp_rdst_dir):
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

    def test_does_not_overwrite_existing(self):
        from shared.config.targets import TargetsConfig
        cfg = TargetsConfig.__new__(TargetsConfig)
        cfg._data = {"email": "first@example.com"}
        cfg._path = None

        cfg.set_email("second@example.com")
        assert cfg.get_email() == "first@example.com"

    def test_ignores_empty_string(self):
        from shared.config.targets import TargetsConfig
        cfg = TargetsConfig.__new__(TargetsConfig)
        cfg._data = {}
        cfg._path = None

        cfg.set_email("")
        assert cfg.get_email() is None

    def test_feedback_saves_email(self, temp_rdst_dir):
        with patch("shared.telemetry_manager.RDST_DATA_DIR", temp_rdst_dir):
            from shared.telemetry_manager import TelemetryManager

            tm = TelemetryManager()
            tm._rdst_dir = temp_rdst_dir
            tm._enabled = True
            tm._initialized = True
            tm._device_id = "test-device-id"

            mock_cfg = MagicMock()
            mock_cfg.get_email.return_value = None  # no existing email

            with (
                patch.object(tm, "track_with_stats"),
                patch.object(tm, "_slack_notify_feedback"),
                patch("shared.config.targets.create_targets_config") as mock_cfg_factory,
            ):
                mock_cfg_factory.return_value = mock_cfg
                tm.submit_feedback(reason="Great tool!", sentiment="positive", email="feedback@example.com")

                mock_cfg.set_email.assert_called_once_with("feedback@example.com")
                mock_cfg.save.assert_called_once()


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

            svc = TrialService()
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

        svc = TrialService()
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
