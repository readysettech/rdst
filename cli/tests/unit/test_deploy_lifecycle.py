"""Unit tests for shared.deploy.lifecycle and the local_docker mode.

Covers:
  - lifecycle.ProbeState enum values and dispatch by mode
  - lifecycle.{probe, start, stop, restart} routing for unimplemented modes
    returns helpful errors / UNKNOWN states
  - local_docker.{probe, start, stop, restart}_local_docker with mocked
    subprocess.run
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shared.deploy import lifecycle
from shared.deploy import local_docker


class TestDockerRuntimeStatus:
    @patch("shared.deploy.local_docker.subprocess.run")
    @patch("shared.deploy.local_docker.shutil.which", return_value=None)
    def test_reports_missing_cli_without_invoking_docker(self, _which, mock_run):
        assert local_docker.docker_runtime_status() == {
            "installed": False,
            "running": False,
        }
        mock_run.assert_not_called()

    @patch("shared.deploy.local_docker.subprocess.run")
    @patch(
        "shared.deploy.local_docker.shutil.which",
        return_value="/usr/bin/docker",
    )
    def test_reports_daemon_state_from_docker_info(self, _which, mock_run):
        mock_run.return_value = MagicMock(returncode=0)

        assert local_docker.docker_runtime_status() == {
            "installed": True,
            "running": True,
        }
        mock_run.assert_called_once_with(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=5,
        )


class TestLifecycleProbeState:

    def test_probe_state_values(self):
        assert lifecycle.ProbeState.NOT_DEPLOYED.value == "not_deployed"
        assert lifecycle.ProbeState.DEPLOYED_RUNNING.value == "deployed_running"
        assert lifecycle.ProbeState.DEPLOYED_STOPPED.value == "deployed_stopped"
        assert lifecycle.ProbeState.DEPLOYED_UNREACHABLE.value == "deployed_unreachable"
        assert lifecycle.ProbeState.UNKNOWN.value == "unknown"


class TestLifecycleDispatch:

    def test_probe_unknown_mode_returns_unknown(self):
        result = lifecycle.probe("anytarget", mode="quantum")
        assert result.state == lifecycle.ProbeState.UNKNOWN
        assert "Unknown deploy mode" in (result.detail or "")

    def test_probe_systemd_returns_unknown_stub(self):
        result = lifecycle.probe("test", mode="systemd")
        assert result.state == lifecycle.ProbeState.UNKNOWN
        assert "systemd" in (result.detail or "").lower() or "systemctl" in (result.detail or "").lower()

    def test_probe_kubernetes_returns_unknown_stub_with_namespace(self):
        result = lifecycle.probe("test", mode="kubernetes", namespace="custom-ns")
        assert result.state == lifecycle.ProbeState.UNKNOWN
        assert "custom-ns" in (result.detail or "")

    def test_probe_remote_returns_unknown_stub(self):
        result = lifecycle.probe("test", mode="remote_docker")
        assert result.state == lifecycle.ProbeState.UNKNOWN

    def test_start_unknown_mode_fails(self):
        result = lifecycle.start("test", mode="quantum")
        assert result.success is False
        assert "Unknown deploy mode" in (result.error or "")

    def test_start_systemd_stub_fails_with_helpful_message(self):
        result = lifecycle.start("test", mode="systemd")
        assert result.success is False
        assert "not yet implemented" in (result.error or "")

    def test_start_kubernetes_stub_includes_kubectl_hint(self):
        result = lifecycle.start("test", mode="kubernetes")
        assert result.success is False
        assert "kubectl" in (result.error or "")
        assert "scale" in (result.error or "")

    def test_stop_kubernetes_stub_includes_kubectl_hint(self):
        result = lifecycle.stop("test", mode="kubernetes")
        assert result.success is False
        assert "kubectl" in (result.error or "")
        assert "replicas=0" in (result.error or "")

    def test_restart_kubernetes_stub_includes_rollout_hint(self):
        result = lifecycle.restart("test", mode="kubernetes")
        assert result.success is False
        assert "kubectl" in (result.error or "")
        assert "rollout" in (result.error or "")


class TestLocalDockerProbe:

    @patch("shared.deploy.local_docker.subprocess.run")
    def test_probe_not_deployed(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="No such container")
        result = local_docker.probe_local_docker("missing")
        assert result.state == lifecycle.ProbeState.NOT_DEPLOYED
        assert result.mode == "docker"

    @patch("shared.deploy.local_docker.subprocess.run")
    def test_probe_stopped_container(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="exited|false|0\n", stderr="")
        result = local_docker.probe_local_docker("test")
        assert result.state == lifecycle.ProbeState.DEPLOYED_STOPPED
        assert "exited" in (result.detail or "")

    @patch("shared.deploy.local_docker._tcp_reachable", return_value=True)
    @patch("shared.deploy.local_docker.subprocess.run")
    def test_probe_running_and_reachable(self, mock_run, _mock_tcp):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="running|true|0\n", stderr=""),
            MagicMock(returncode=0, stdout="0.0.0.0:5433\n", stderr=""),
        ]
        result = local_docker.probe_local_docker("test")
        assert result.state == lifecycle.ProbeState.DEPLOYED_RUNNING

    @patch("shared.deploy.local_docker._tcp_reachable", return_value=False)
    @patch("shared.deploy.local_docker.subprocess.run")
    def test_probe_running_with_tcp_inconclusive_returns_running(self, mock_run, _mock_tcp):
        # Container running per docker is the authoritative signal; TCP probe is
        # best-effort. The detail field flags inconclusive TCP.
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="running|true|0\n", stderr=""),
            MagicMock(returncode=0, stdout="5436/tcp -> 0.0.0.0:5436\n", stderr=""),
        ]
        result = local_docker.probe_local_docker("test")
        assert result.state == lifecycle.ProbeState.DEPLOYED_RUNNING
        assert "inconclusive" in (result.detail or "").lower()

    @patch("shared.deploy.local_docker._tcp_reachable", return_value=True)
    @patch("shared.deploy.local_docker.subprocess.run")
    def test_probe_running_with_custom_port(self, mock_run, _mock_tcp):
        # Probe must not hardcode 5433/3307. Custom port (5436) should be
        # detected via `docker port`.
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="running|true|0\n", stderr=""),
            MagicMock(returncode=0, stdout="5436/tcp -> 0.0.0.0:5436\n6037/tcp -> 0.0.0.0:6037\n", stderr=""),
        ]
        result = local_docker.probe_local_docker("test")
        assert result.state == lifecycle.ProbeState.DEPLOYED_RUNNING
        assert "5436" in (result.detail or "")


class TestLocalDockerLifecycleOps:

    @patch("shared.deploy.local_docker.subprocess.run")
    def test_start_no_container_errors(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")
        result = local_docker.start_local_docker("missing")
        assert result.success is False
        assert "No container" in (result.error or "")

    @patch("shared.deploy.local_docker.subprocess.run")
    def test_start_already_running_idempotent(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="running|true|0\n", stderr="")
        result = local_docker.start_local_docker("test")
        assert result.success is True
        assert result.state_after == lifecycle.ProbeState.DEPLOYED_RUNNING

    @patch("shared.deploy.local_docker.subprocess.run")
    def test_start_stopped_container(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="exited|false|0\n", stderr=""),
            MagicMock(returncode=0, stdout="test\n", stderr=""),
        ]
        result = local_docker.start_local_docker("test")
        assert result.success is True
        assert result.state_after == lifecycle.ProbeState.DEPLOYED_RUNNING

    @patch("shared.deploy.local_docker.subprocess.run")
    def test_stop_no_container_errors(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")
        result = local_docker.stop_local_docker("missing")
        assert result.success is False

    @patch("shared.deploy.local_docker.subprocess.run")
    def test_stop_already_stopped_idempotent(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="exited|false|0\n", stderr="")
        result = local_docker.stop_local_docker("test")
        assert result.success is True
        assert result.state_after == lifecycle.ProbeState.DEPLOYED_STOPPED

    @patch("shared.deploy.local_docker.subprocess.run")
    def test_stop_running_container(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="running|true|0\n", stderr=""),
            MagicMock(returncode=0, stdout="test\n", stderr=""),
        ]
        result = local_docker.stop_local_docker("test")
        assert result.success is True
        assert result.state_after == lifecycle.ProbeState.DEPLOYED_STOPPED

    @patch("shared.deploy.local_docker.subprocess.run")
    def test_restart_no_container_errors(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")
        result = local_docker.restart_local_docker("missing")
        assert result.success is False

    @patch("shared.deploy.local_docker.subprocess.run")
    def test_restart_running_container(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="running|true|0\n", stderr=""),
            MagicMock(returncode=0, stdout="test\n", stderr=""),
        ]
        result = local_docker.restart_local_docker("test")
        assert result.success is True
        assert result.state_after == lifecycle.ProbeState.DEPLOYED_RUNNING


class TestManagedSandboxDocker:
    @staticmethod
    def variables():
        return {
            "db_engine": "postgresql",
            "db_host": "localhost",
            "db_port": "5432",
            "db_user": "app",
            "db_name": "app",
            "readyset_port": "5433",
            "metrics_port": "6034",
            "container_name": "ignored",
            "readyset_image": "readyset:test",
        }

    @patch("shared.deploy.local_docker.subprocess.run")
    @patch(
        "shared.deploy.local_docker._inspect_exact_container_checked",
        return_value=(None, None),
    )
    def test_managed_deploy_has_stable_identity_and_no_restart_policy(
        self, _inspect, mock_run
    ):
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=0, stdout="container-id", stderr=""),
        ]

        result = local_docker.deploy_managed_sandbox(
            "origin", self.variables(), "secret", "fingerprint"
        )

        assert result["success"] is True
        command = mock_run.call_args_list[1].args[0]
        assert command[0:3] == ["docker", "run", "-d"]
        assert "--restart=unless-stopped" not in command
        assert command[command.index("--name") + 1] == "rdst-readyset-sandbox"
        assert "QUERY_CACHING=explicit" in command
        assert "io.readyset.rdst.sandbox=true" in command
        assert "io.readyset.rdst.target=origin" in command
        assert "io.readyset.rdst.fingerprint=fingerprint" in command

    @patch("shared.deploy.local_docker.subprocess.run")
    @patch(
        "shared.deploy.local_docker._inspect_exact_container_checked",
        return_value=({"managed": "", "running": True}, None),
    )
    def test_managed_deploy_refuses_foreign_stable_name(self, _inspect, mock_run):
        result = local_docker.deploy_managed_sandbox(
            "origin", self.variables(), "secret", "fingerprint"
        )

        assert result["success"] is False
        assert "not owned by RDST" in result["error"]
        mock_run.assert_not_called()

    @patch(
        "shared.deploy.local_docker._inspect_exact_container_checked",
        return_value=({"managed": "", "running": True}, None),
    )
    def test_managed_remove_refuses_foreign_stable_name(self, _inspect):
        result = local_docker.remove_managed_sandbox()

        assert result["success"] is False
        assert "Refusing to remove foreign container" in result["error"]

    @patch(
        "shared.deploy.local_docker._inspect_exact_container_checked",
        return_value=(None, "docker inspect timed out"),
    )
    def test_managed_remove_does_not_claim_absence_after_inspection_failure(
        self, _inspect
    ):
        result = local_docker.remove_managed_sandbox()

        assert result["success"] is False
        assert result["removed"] is False
        assert "timed out" in result["error"]

    @patch(
        "shared.deploy.local_docker._inspect_exact_container_checked",
        return_value=(None, "Docker daemon unavailable"),
    )
    def test_managed_deploy_fails_closed_after_inspection_failure(self, _inspect):
        result = local_docker.deploy_managed_sandbox(
            "origin", self.variables(), "secret", "fingerprint"
        )

        assert result["success"] is False
        assert "Docker daemon unavailable" in result["error"]

    @patch(
        "shared.deploy.local_docker._inspect_exact_container_checked",
        return_value=(None, "docker inspect timed out"),
    )
    def test_managed_inspect_reports_inspection_failure(self, _inspect):
        with pytest.raises(RuntimeError, match="docker inspect timed out"):
            local_docker.inspect_managed_sandbox()

    @patch(
        "shared.deploy.local_docker.docker_runtime_status",
        return_value={"installed": True, "running": False},
    )
    @patch(
        "shared.deploy.local_docker._docker_inspect_state",
        return_value=None,
    )
    def test_legacy_remove_does_not_claim_absence_when_daemon_is_down(
        self, _inspect, _runtime
    ):
        result = local_docker.remove_configured_legacy_container(
            "rdst-readyset-production"
        )

        assert result["success"] is False
        assert result["removed"] is False
        assert "Docker" in result["error"]
