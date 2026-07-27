"""Tests for AWS credential status reporting and profile selection (mocked botocore)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("botocore")

from features.fleet.auth import get_aws_status, get_botocore_session


pytestmark = pytest.mark.usefixtures("run_blocking_inline")


def _make_session(
    profiles=("default", "dev"),
    region="us-east-1",
    credential_method="shared-credentials-file",
    identity=None,
    identity_error=None,
):
    session = MagicMock()
    session.available_profiles = list(profiles)
    session.get_config_variable.return_value = region
    if credential_method is None:
        session.get_credentials.return_value = None
    else:
        creds = MagicMock()
        creds.method = credential_method
        session.get_credentials.return_value = creds
    sts = session.create_client.return_value
    if identity_error is not None:
        sts.get_caller_identity.side_effect = identity_error
    else:
        sts.get_caller_identity.return_value = identity or {
            "Arn": "arn:aws:iam::123456789012:user/mike",
            "Account": "123456789012",
        }
    return session


class TestGetAwsStatus:
    def test_working_credentials(self, monkeypatch):
        monkeypatch.delenv("AWS_PROFILE", raising=False)
        session = _make_session()
        with patch("botocore.session.get_session", return_value=session):
            status = get_aws_status()
        assert status["has_credentials"] is True
        assert status["method"] == "profile"
        assert status["identity_arn"] == "arn:aws:iam::123456789012:user/mike"
        assert status["account"] == "123456789012"
        assert status["available_profiles"] == ["default", "dev"]
        assert status["region"] == "us-east-1"
        assert status["active_profile"] is None

    def test_sts_failure_keeps_profiles(self, monkeypatch):
        monkeypatch.delenv("AWS_PROFILE", raising=False)
        session = _make_session(identity_error=Exception("connect timeout"))
        with patch("botocore.session.get_session", return_value=session):
            status = get_aws_status()
        assert status["has_credentials"] is False
        assert status["identity_arn"] is None
        assert status["account"] is None
        # Profiles and method are still reported so the UI can offer a picker.
        assert status["available_profiles"] == ["default", "dev"]
        assert status["method"] == "profile"

    def test_no_credentials(self, monkeypatch):
        monkeypatch.delenv("AWS_PROFILE", raising=False)
        session = _make_session(credential_method=None)
        with patch("botocore.session.get_session", return_value=session):
            status = get_aws_status()
        assert status["has_credentials"] is False
        assert status["method"] is None
        assert status["available_profiles"] == ["default", "dev"]

    def test_method_mapping(self, monkeypatch):
        monkeypatch.delenv("AWS_PROFILE", raising=False)
        for botocore_method, expected in [
            ("env", "env"),
            ("sso", "sso"),
            ("iam-role", "instance"),
            ("container-role", "instance"),
            ("shared-credentials-file", "profile"),
            ("config-file", "profile"),
        ]:
            session = _make_session(credential_method=botocore_method)
            with patch("botocore.session.get_session", return_value=session):
                status = get_aws_status()
            assert status["method"] == expected, botocore_method

    def test_active_profile_from_env(self, monkeypatch):
        monkeypatch.setenv("AWS_PROFILE", "dev")
        session = _make_session()
        with patch("botocore.session.get_session", return_value=session):
            status = get_aws_status()
        assert status["active_profile"] == "dev"
        session.set_config_variable.assert_any_call("profile", "dev")


class TestProfileSelection:
    def test_explicit_profile_overrides_env(self, monkeypatch):
        monkeypatch.setenv("AWS_PROFILE", "from-env")
        session = MagicMock()
        with patch("botocore.session.get_session", return_value=session):
            get_botocore_session(profile="dev")
        session.set_config_variable.assert_called_once_with("profile", "dev")

    def test_env_profile_used_when_no_explicit(self, monkeypatch):
        monkeypatch.setenv("AWS_PROFILE", "from-env")
        session = MagicMock()
        with patch("botocore.session.get_session", return_value=session):
            get_botocore_session()
        session.set_config_variable.assert_called_once_with("profile", "from-env")

    async def test_discover_passes_profile_through(self):
        from features.fleet.service import FleetService

        seen: dict = {}

        def fake_discover(**kwargs):
            seen.update(kwargs)
            return iter([])

        config = MagicMock()
        config.list_targets.return_value = []
        service = FleetService(config=config)
        with (
            patch(
                "features.fleet.auth.detect_aws_credentials",
                return_value=(True, "ok"),
            ) as detect,
            patch(
                "features.fleet.discovery.discover_rds_instances",
                side_effect=fake_discover,
            ),
        ):
            events = [
                e
                async for e in service.discover(
                    ["us-east-1"], dry_run=True, profile="dev",
                )
            ]

        assert seen["profile"] == "dev"
        detect.assert_called_once_with("dev")
        assert events[-1].type == "import_complete"
