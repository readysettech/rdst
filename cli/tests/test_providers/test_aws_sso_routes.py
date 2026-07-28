"""API tests for the discover-accounts-and-roles SSO profile flow."""

from __future__ import annotations

import configparser
from unittest.mock import patch

import pytest

from features.providers import aws_login
from features.providers.aws_sso import stable_session_name

from .conftest import make_fleet_router_client as _client


pytestmark = pytest.mark.usefixtures("run_blocking_inline")

START_URL = "https://example.awsapps.com/start"


@pytest.fixture(autouse=True)
def isolated_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / ".aws" / "config"))


class TestAwsSsoLogin:
    def test_started(self):
        with patch.object(
            aws_login,
            "start_sso_session_login",
            return_value={"login_id": "abc", "state": "started", "detail": "go"},
        ) as start:
            response = _client().post(
                "/api/providers/aws-sso-login",
                json={"start_url": START_URL, "region": "us-east-1"},
            )

        assert response.status_code == 200
        assert response.json()["state"] == "started"
        start.assert_called_once_with(START_URL, "us-east-1")

    def test_missing_cli_returns_fallback(self):
        with patch.object(
            aws_login,
            "start_sso_session_login",
            side_effect=aws_login.AwsCliMissing("AWS CLI is not installed"),
        ):
            response = _client().post(
                "/api/providers/aws-sso-login",
                json={"start_url": START_URL, "region": "us-east-1"},
            )

        assert response.status_code == 409
        body = response.json()
        assert body["code"] == "aws_cli_missing"
        assert body["fallback_command"] == (
            f"aws sso login --sso-session {stable_session_name(START_URL)}"
        )


class TestAwsSsoAccounts:
    def test_happy_path(self):
        with patch(
            "features.providers.aws_sso.list_accounts",
            return_value=([{"account_id": "111", "account_name": "Prod"}], None),
        ):
            response = _client().get(
                "/api/providers/aws-sso-accounts",
                params={"start_url": "https://example.awsapps.com/start"},
            )

        assert response.status_code == 200
        assert response.json() == {
            "accounts": [{"account_id": "111", "account_name": "Prod"}],
            "error": None,
        }


class TestAwsSsoRoles:
    def test_happy_path(self):
        with patch(
            "features.providers.aws_sso.list_account_roles",
            return_value=(["Admin", "ReadOnly"], None),
        ):
            response = _client().get(
                "/api/providers/aws-sso-roles",
                params={
                    "start_url": "https://example.awsapps.com/start",
                    "account_id": "111",
                },
            )

        assert response.status_code == 200
        assert response.json() == {"roles": ["Admin", "ReadOnly"], "error": None}


def _finalize_body() -> dict[str, str]:
    return {
        "name": "prod",
        "start_url": START_URL,
        "region": "us-east-1",
        "account_id": "111111111111",
        "role_name": "AdministratorAccess",
    }


class TestAwsSsoFinalize:
    def test_creates_and_references_the_stable_session(self, tmp_path):
        with patch(
            "features.providers.aws_login.verify_profile",
            return_value=(True, "Signed in as arn:aws:sts::111:assumed-role/prod/u"),
        ):
            response = _client().post(
                "/api/providers/aws-sso-finalize", json=_finalize_body()
            )

        assert response.status_code == 200
        body = response.json()
        assert body["created"] is True
        assert body["profile"] == "prod"

        session = stable_session_name(START_URL)
        parser = configparser.RawConfigParser()
        parser.read(tmp_path / ".aws" / "config")
        # The profile references the shared session, not a session named after
        # itself, and that session block carries the start URL.
        assert parser["profile prod"]["sso_session"] == session
        assert parser["profile prod"]["sso_account_id"] == "111111111111"
        assert parser[f"sso-session {session}"]["sso_start_url"] == START_URL
        assert not parser.has_section("sso-session prod")

    def test_forbidden_role_returns_actionable_error(self):
        with patch(
            "features.providers.aws_login.verify_profile",
            return_value=(False, "ForbiddenException: no access to role"),
        ):
            response = _client().post(
                "/api/providers/aws-sso-finalize", json=_finalize_body()
            )

        assert response.status_code == 400
        body = response.json()
        assert body["code"] == "role_verification_failed"
        assert body["created"] is True
        assert "AdministratorAccess" in body["detail"]
        assert "111111111111" in body["detail"]
