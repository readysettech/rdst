"""API tests for the local browser-driven AWS SSO login flow."""

from __future__ import annotations

import io
import subprocess
import time
from unittest.mock import MagicMock, patch

import pytest

from features.fleet import aws_login


pytestmark = pytest.mark.usefixtures("run_blocking_inline")


@pytest.fixture
def client(fleet_router_client):
    return fleet_router_client


@pytest.fixture(autouse=True)
def clear_login_registry():
    aws_login.LOGIN_REGISTRY.clear()
    yield
    aws_login.LOGIN_REGISTRY.clear()


def _process(return_code=None, output: str = "") -> MagicMock:
    process = MagicMock()
    process.poll.return_value = return_code
    process.stdout = io.StringIO(output)
    return process


def _register_login(
    login_id: str,
    process: MagicMock,
    *,
    output: str = "",
    age_seconds: float = 0,
) -> None:
    buffer = aws_login.OutputBuffer()
    buffer.append(output)
    aws_login.LOGIN_REGISTRY[login_id] = {
        "process": process,
        "profile": "dev",
        "started_at": time.monotonic() - age_seconds,
        "buffer": buffer,
    }


class TestAwsLoginStart:
    def test_unknown_profile(self, client):
        with patch("features.fleet.aws_login.available_profiles", return_value=["prod"]):
            response = client.post("/api/fleet/aws-login", json={"profile": "dev"})

        assert response.status_code == 400
        assert response.json()["code"] == "unknown_profile"

    def test_already_signed_in(self, client):
        with (
            patch("features.fleet.aws_login.available_profiles", return_value=["dev"]),
            patch(
                "features.fleet.aws_login.verify_profile",
                return_value=(True, "Signed in as arn:aws:iam::123:user/test"),
            ),
            patch("features.fleet.aws_login.subprocess.Popen") as popen,
        ):
            response = client.post("/api/fleet/aws-login", json={"profile": "dev"})

        assert response.status_code == 200
        assert response.json() == {
            "login_id": None,
            "state": "already_signed_in",
            "detail": "Signed in as arn:aws:iam::123:user/test",
        }
        popen.assert_not_called()

    def test_spawn_started(self, client):
        process = _process()
        with (
            patch("features.fleet.aws_login.available_profiles", return_value=["dev"]),
            patch(
                "features.fleet.aws_login.verify_profile",
                return_value=(False, "expired"),
            ),
            patch("features.fleet.aws_login.subprocess.Popen", return_value=process) as popen,
        ):
            response = client.post("/api/fleet/aws-login", json={"profile": "dev"})

        assert response.status_code == 200
        body = response.json()
        assert body["state"] == "started"
        assert body["login_id"] in aws_login.LOGIN_REGISTRY
        popen.assert_called_once_with(
            ["aws", "sso", "login", "--profile", "dev"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

    def test_missing_cli_returns_fallback(self, client):
        with (
            patch("features.fleet.aws_login.available_profiles", return_value=["dev"]),
            patch("features.fleet.aws_login.verify_profile", return_value=(False, "expired")),
            patch(
                "features.fleet.aws_login.subprocess.Popen",
                side_effect=FileNotFoundError,
            ),
        ):
            response = client.post("/api/fleet/aws-login", json={"profile": "dev"})

        assert response.status_code == 409
        assert response.json() == {
            "code": "aws_cli_missing",
            "detail": "AWS CLI is not installed or is not on PATH",
            "fallback_command": "aws sso login --profile dev",
        }


class TestAwsLoginStatus:
    def test_running_surfaces_verification_url(self, client):
        process = _process()
        output = (
            "Open the following URL:\n"
            "https://device.sso.us-east-1.amazonaws.com/\n"
            "Then enter code ABCD-EFGH\n"
        )
        _register_login("running", process, output=output)

        with patch(
            "features.fleet.aws_login.verify_profile", return_value=(False, "not yet")
        ):
            response = client.get("/api/fleet/aws-login/running")

        assert response.status_code == 200
        body = response.json()
        assert body["state"] == "running"
        assert body["verification_url"] == (
            "https://device.sso.us-east-1.amazonaws.com/"
        )
        assert "ABCD-EFGH" in body["detail"]
        assert body["fallback_command"] == "aws sso login --profile dev"
        assert "running" in aws_login.LOGIN_REGISTRY

    def test_running_succeeds_as_soon_as_sts_works(self, client):
        process = _process()
        _register_login("success", process)

        with patch(
            "features.fleet.aws_login.verify_profile",
            return_value=(True, "Signed in as arn:aws:sts::123:assumed-role/dev/user"),
        ):
            response = client.get("/api/fleet/aws-login/success")

        assert response.status_code == 200
        assert response.json()["state"] == "success"
        assert "success" in aws_login.LOGIN_REGISTRY

        repeated = client.get("/api/fleet/aws-login/success")
        assert repeated.status_code == 200
        assert repeated.json() == response.json()

    def test_nonzero_exit_uses_last_output_lines(self, client):
        process = _process(return_code=255)
        _register_login(
            "failed",
            process,
            output="first line\nThe SSO session has expired or is invalid\n",
        )

        response = client.get("/api/fleet/aws-login/failed")

        assert response.status_code == 200
        body = response.json()
        assert body["state"] == "failed"
        assert "session has expired" in body["detail"]
        assert "failed" in aws_login.LOGIN_REGISTRY

        repeated = client.get("/api/fleet/aws-login/failed")
        assert repeated.status_code == 200
        assert repeated.json() == body

    def test_timeout_kills_process(self, client):
        process = _process()
        _register_login("timeout", process, age_seconds=301)

        response = client.get("/api/fleet/aws-login/timeout")

        assert response.status_code == 200
        assert response.json()["state"] == "timeout"
        process.kill.assert_called_once_with()
        assert "timeout" in aws_login.LOGIN_REGISTRY

        repeated = client.get("/api/fleet/aws-login/timeout")
        assert repeated.status_code == 200
        assert repeated.json() == response.json()

    def test_terminal_status_expires_after_retention_window(self, client):
        process = _process(return_code=255)
        _register_login("expired", process, output="login failed")

        response = client.get("/api/fleet/aws-login/expired")
        assert response.status_code == 200
        completed_at = aws_login.LOGIN_REGISTRY["expired"]["completed_at"]

        # A full second past the window: landing exactly on the boundary is
        # subject to float cancellation ((t + 60.0) - t can be 59.999...),
        # which made this flake on hosts with large monotonic clocks.
        with patch(
            "features.fleet.aws_login.time.monotonic",
            return_value=completed_at + aws_login.TERMINAL_RETENTION_SECONDS + 1.0,
        ):
            expired = client.get("/api/fleet/aws-login/expired")

        assert expired.status_code == 404
        assert expired.json()["code"] == "unknown_login"
        assert "expired" not in aws_login.LOGIN_REGISTRY
