"""Unit tests for shared.deploy.lifecycle and the local_docker mode.

Covers:
  - lifecycle.ProbeState enum values and dispatch by mode
  - lifecycle.{probe, start, stop, restart} routing for unimplemented modes
    returns helpful errors / UNKNOWN states
  - local_docker.{probe, start, stop, restart}_local_docker with mocked
    subprocess.run
"""

from __future__ import annotations

import socket
import subprocess
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


class TestManagedSandboxPortAllocation:
    @patch("shared.deploy.local_docker.subprocess.run")
    def test_reads_loopback_wildcard_and_ipv6_docker_ports(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "127.0.0.1:5433->5433/tcp, "
                "0.0.0.0:6034->6034/tcp, "
                "[::1]:6432->6432/tcp, "
                "0.0.0.0:7000-7002->7000-7002/tcp, "
                "0.0.0.0:8000->8000/udp\n"
            ),
        )

        assert local_docker._docker_published_ports() == {
            5433,
            6034,
            6432,
            7000,
            7001,
            7002,
        }

    def test_detects_real_host_listener(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]

            assert local_docker._local_tcp_port_free(port) is False

    def test_skips_ordinary_host_listeners(self, monkeypatch):
        topology = MagicMock(remote=False)
        monkeypatch.setattr(
            local_docker.DockerTopology,
            "from_environment",
            lambda: topology,
        )
        monkeypatch.setattr(local_docker, "_docker_published_ports", set)
        monkeypatch.setattr(
            local_docker,
            "_local_tcp_port_free",
            lambda port: port not in {5433, 6034},
        )

        allocated = local_docker._allocate_managed_sandbox_ports(
            {"readyset_port": "5433", "metrics_port": "6034"}
        )

        assert allocated["readyset_port"] == "5434"
        assert allocated["metrics_port"] == "6034"

    def test_remote_daemon_uses_published_ports_without_local_probe(
        self, monkeypatch
    ):
        topology = MagicMock(remote=True)
        monkeypatch.setattr(
            local_docker.DockerTopology,
            "from_environment",
            lambda: topology,
        )
        monkeypatch.setattr(
            local_docker, "_docker_published_ports", lambda: {5433, 6034}
        )
        local_probe = MagicMock()
        monkeypatch.setattr(local_docker, "_local_tcp_port_free", local_probe)

        allocated = local_docker._allocate_managed_sandbox_ports(
            {"readyset_port": "5433", "metrics_port": "6034"}
        )

        assert allocated["readyset_port"] == "5434"
        assert allocated["metrics_port"] == "6034"
        local_probe.assert_not_called()


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
        mock_run.return_value = MagicMock(returncode=0, stdout="container-id|exited|false|0\n", stderr="")
        result = local_docker.probe_local_docker("test")
        assert result.state == lifecycle.ProbeState.DEPLOYED_STOPPED
        assert "exited" in (result.detail or "")

    @patch("shared.deploy.local_docker._tcp_reachable", return_value=True)
    @patch("shared.deploy.local_docker.subprocess.run")
    def test_probe_running_and_reachable(self, mock_run, _mock_tcp):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="container-id|running|true|0\n", stderr=""),
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
            MagicMock(returncode=0, stdout="container-id|running|true|0\n", stderr=""),
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
            MagicMock(returncode=0, stdout="container-id|running|true|0\n", stderr=""),
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
        mock_run.return_value = MagicMock(returncode=0, stdout="container-id|running|true|0\n", stderr="")
        result = local_docker.start_local_docker("test")
        assert result.success is True
        assert result.state_after == lifecycle.ProbeState.DEPLOYED_RUNNING

    @patch("shared.deploy.local_docker.subprocess.run")
    def test_start_stopped_container(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="container-id|exited|false|0\n", stderr=""),
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
        mock_run.return_value = MagicMock(returncode=0, stdout="container-id|exited|false|0\n", stderr="")
        result = local_docker.stop_local_docker("test")
        assert result.success is True
        assert result.state_after == lifecycle.ProbeState.DEPLOYED_STOPPED

    @patch("shared.deploy.local_docker.subprocess.run")
    def test_stop_running_container(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="container-id|running|true|0\n", stderr=""),
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
            MagicMock(returncode=0, stdout="container-id|running|true|0\n", stderr=""),
            MagicMock(returncode=0, stdout="test\n", stderr=""),
        ]
        result = local_docker.restart_local_docker("test")
        assert result.success is True
        assert result.state_after == lifecycle.ProbeState.DEPLOYED_RUNNING


class TestManagedSandboxDocker:

    @pytest.mark.parametrize(
        "message",
        [
            "Ports are not available: exposing port TCP 0.0.0.0:5433",
            "Only one usage of each socket address is normally permitted",
        ],
    )
    def test_windows_port_conflicts_are_retryable(self, message):
        assert local_docker._is_port_conflict(message)
    @pytest.fixture(autouse=True)
    def _stable_test_ports(self, monkeypatch):
        monkeypatch.setattr(
            local_docker,
            "_allocate_managed_sandbox_ports",
            lambda variables, *, exclude=None: dict(variables),
        )

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

    def test_docker_info_timeout_does_not_run_create_reconciliation(
        self, monkeypatch
    ):
        monkeypatch.setattr(
            local_docker.subprocess,
            "run",
            MagicMock(side_effect=subprocess.TimeoutExpired("docker info", 5)),
        )
        reconcile = MagicMock()
        monkeypatch.setattr(
            local_docker, "_reconcile_ambiguous_managed_create", reconcile
        )

        result = local_docker._create_container_command(
            self.variables(), "secret", restart_policy=False
        )

        assert result["success"] is False
        assert "daemon status check timed out" in result["error"]
        reconcile.assert_not_called()

    def test_image_pull_failure_happens_before_container_create(
        self, monkeypatch
    ):
        run = MagicMock(
            side_effect=[
                MagicMock(returncode=0),
                MagicMock(returncode=1),
                MagicMock(returncode=1, stderr="authentication required"),
            ]
        )
        monkeypatch.setattr(local_docker.subprocess, "run", run)
        reconcile = MagicMock()
        monkeypatch.setattr(
            local_docker, "_reconcile_ambiguous_managed_create", reconcile
        )

        result = local_docker._create_container_command(
            self.variables(), "secret", restart_policy=False
        )

        assert result["success"] is False
        assert "authentication required" in result["error"]
        assert run.call_args_list[2].args[0][0:2] == ["docker", "pull"]
        reconcile.assert_not_called()

    def test_only_create_timeout_runs_ambiguous_reconciliation(
        self, monkeypatch
    ):
        run = MagicMock(
            side_effect=[
                MagicMock(returncode=0),
                MagicMock(returncode=0),
                subprocess.TimeoutExpired("docker create", 300),
            ]
        )
        monkeypatch.setattr(local_docker.subprocess, "run", run)
        reconcile = MagicMock(return_value={"success": True, "removed": True})
        monkeypatch.setattr(
            local_docker, "_reconcile_ambiguous_managed_create", reconcile
        )

        result = local_docker._create_container_command(
            self.variables(), "secret", restart_policy=False
        )

        assert result == {
            "success": False,
            "error": "Container creation timed out (5 min).",
        }
        reconcile.assert_called_once_with()

    def test_ambiguous_create_reconciles_container_that_appears_late(
        self, monkeypatch
    ):
        inspections = iter(
            [(None, None)] * 6
            + [({"managed": "true", "id": "late-container"}, None)]
        )
        monkeypatch.setattr(
            local_docker,
            "MANAGED_CREATE_RECONCILE_INTERVAL_SECONDS",
            0,
        )
        monkeypatch.setattr(
            local_docker,
            "_inspect_exact_container_checked",
            lambda _name: next(inspections),
        )
        remove = MagicMock(return_value={"success": True, "removed": True})
        monkeypatch.setattr(local_docker, "remove_managed_sandbox", remove)

        result = local_docker._reconcile_ambiguous_managed_create()

        assert result == {"success": True, "removed": True}
        remove.assert_called_once_with()

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
            MagicMock(returncode=0),
            MagicMock(returncode=0, stdout="container-id", stderr=""),
            MagicMock(returncode=0, stdout="container-id", stderr=""),
        ]

        result = local_docker.deploy_managed_sandbox(
            "origin", self.variables(), "secret", "fingerprint"
        )

        assert result["success"] is True
        command = mock_run.call_args_list[2].args[0]
        assert command[0:2] == ["docker", "create"]
        assert "--pull=never" in command
        assert "--restart=unless-stopped" not in command
        assert command[command.index("--name") + 1] == "rdst-readyset-sandbox"
        assert "QUERY_CACHING=explicit" in command
        assert "io.readyset.rdst.sandbox=true" in command
        assert "io.readyset.rdst.target=origin" in command
        assert "io.readyset.rdst.fingerprint=fingerprint" in command
        assert any(
            value.startswith("io.readyset.rdst.instance=") for value in command
        )

    def test_managed_deploy_retries_a_raced_port_conflict(
        self, monkeypatch
    ):
        allocations: list[set[int]] = []

        def allocate(variables, *, exclude=None):
            allocations.append(set(exclude or ()))
            candidate = dict(variables)
            offset = len(allocations) - 1
            candidate["readyset_port"] = str(5433 + offset)
            return candidate

        create_results = iter(
            [
                {
                    "success": False,
                    "error": "Docker error: port is already allocated",
                },
                {
                    "success": True,
                    "container_name": local_docker.MANAGED_SANDBOX_NAME,
                    "port": "5434",
                },
            ]
        )
        monkeypatch.setattr(
            local_docker,
            "_inspect_exact_container_checked",
            lambda _name: (None, None),
        )
        monkeypatch.setattr(
            local_docker, "_allocate_managed_sandbox_ports", allocate
        )
        monkeypatch.setattr(
            local_docker,
            "_create_container_command",
            lambda *_args, **_kwargs: next(create_results),
        )
        removed: list[bool] = []
        monkeypatch.setattr(
            local_docker,
            "remove_managed_sandbox",
            lambda: removed.append(True) or {"success": True, "removed": True},
        )

        result = local_docker.deploy_managed_sandbox(
            "origin", self.variables(), "secret", "fingerprint"
        )

        assert result["success"] is True
        assert result["port"] == "5434"
        assert allocations == [set(), {5433}]
        assert removed == [True]

    def test_managed_deploy_does_not_retry_non_port_error(
        self, monkeypatch
    ):
        monkeypatch.setattr(
            local_docker,
            "_inspect_exact_container_checked",
            lambda _name: (None, None),
        )
        create = MagicMock(
            return_value={"success": False, "error": "authentication required"}
        )
        monkeypatch.setattr(local_docker, "_create_container_command", create)
        remove = MagicMock()
        monkeypatch.setattr(local_docker, "remove_managed_sandbox", remove)

        result = local_docker.deploy_managed_sandbox(
            "origin", self.variables(), "secret", "fingerprint"
        )

        assert result["success"] is False
        assert create.call_count == 1
        remove.assert_not_called()

    def test_managed_deploy_cleans_created_container_after_start_error(
        self, monkeypatch
    ):
        monkeypatch.setattr(
            local_docker,
            "_inspect_exact_container_checked",
            lambda _name: (None, None),
        )
        monkeypatch.setattr(
            local_docker,
            "_create_container_command",
            lambda *_args, **_kwargs: {
                "success": False,
                "error": "Readyset exited during startup",
                "resource_may_exist": True,
            },
        )
        remove = MagicMock(return_value={"success": True, "removed": True})
        monkeypatch.setattr(local_docker, "remove_managed_sandbox", remove)

        result = local_docker.deploy_managed_sandbox(
            "origin", self.variables(), "secret", "fingerprint"
        )

        assert result["success"] is False
        remove.assert_called_once_with()

    def test_managed_deploy_reports_exhausted_port_retries(
        self, monkeypatch
    ):
        allocations: list[set[int]] = []

        def allocate(variables, *, exclude=None):
            allocations.append(set(exclude or ()))
            candidate = dict(variables)
            candidate["readyset_port"] = str(5433 + len(allocations) - 1)
            return candidate

        monkeypatch.setattr(
            local_docker,
            "_inspect_exact_container_checked",
            lambda _name: (None, None),
        )
        monkeypatch.setattr(
            local_docker, "_allocate_managed_sandbox_ports", allocate
        )
        monkeypatch.setattr(
            local_docker,
            "_create_container_command",
            lambda *_args, **_kwargs: {
                "success": False,
                "error": "bind: address already in use",
            },
        )
        removed: list[bool] = []
        monkeypatch.setattr(
            local_docker,
            "remove_managed_sandbox",
            lambda: removed.append(True) or {"success": True, "removed": True},
        )

        result = local_docker.deploy_managed_sandbox(
            "origin", self.variables(), "secret", "fingerprint"
        )

        assert result["success"] is False
        assert "3 attempts" in result["error"]
        assert "--port" not in result["error"]
        assert allocations == [set(), {5433}, {5433, 5434}]
        assert removed == [True, True, True]

    @patch("shared.deploy.docker_topology.sys.platform", "linux")
    @patch("shared.deploy.local_docker.subprocess.run")
    @patch(
        "shared.deploy.local_docker._inspect_exact_container_checked",
        return_value=(None, None),
    )
    def test_managed_localhost_uses_linux_host_network(self, _inspect, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=0),
            MagicMock(returncode=0, stdout="container-id", stderr=""),
            MagicMock(returncode=0, stdout="container-id", stderr=""),
        ]

        result = local_docker.deploy_managed_sandbox(
            "origin", self.variables(), "secret", "fingerprint"
        )

        assert result["success"] is True
        command = mock_run.call_args_list[2].args[0]
        assert "--network=host" in command
        assert "-p" not in command
        assert "--add-host=host.docker.internal:host-gateway" not in command
        assert "LISTEN_ADDRESS=127.0.0.1:5433" in command
        assert "PROMETHEUS_METRICS=false" in command
        assert "SHALLOW_MEMORY_PERCENT=80" in command
        assert not any(value.startswith("METRICS_ADDRESS=") for value in command)
        assert any(
            value.startswith("UPSTREAM_DB_URL=postgresql://app:secret@localhost:5432/")
            for value in command
        )

    @patch("shared.deploy.docker_topology.sys.platform", "linux")
    @patch("shared.deploy.local_docker.subprocess.run")
    @patch(
        "shared.deploy.local_docker._inspect_exact_container_checked",
        return_value=(None, None),
    )
    def test_managed_remote_daemon_keeps_bridge_network(
        self, _inspect, mock_run, monkeypatch
    ):
        monkeypatch.setenv("RDST_DOCKER_REMOTE", "true")
        monkeypatch.setenv("RDST_DOCKER_PUBLISHED_HOST", "10.0.0.5")
        monkeypatch.setenv("RDST_DOCKER_UPSTREAM_HOST", "10.0.0.6")
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=0),
            MagicMock(returncode=0, stdout="container-id", stderr=""),
            MagicMock(returncode=0, stdout="container-id", stderr=""),
        ]

        result = local_docker.deploy_managed_sandbox(
            "origin", self.variables(), "secret", "fingerprint"
        )

        assert result["success"] is True
        command = mock_run.call_args_list[2].args[0]
        assert "--network=host" not in command
        assert "5433:5433" in command
        assert "6034:6034" not in command
        assert "--add-host=host.docker.internal:host-gateway" in command
        assert "LISTEN_ADDRESS=0.0.0.0:5433" in command
        assert any(
            value.startswith("UPSTREAM_DB_URL=postgresql://app:secret@10.0.0.6:5432/")  # trufflehog:ignore
            for value in command
        )

    @patch("shared.deploy.docker_topology.sys.platform", "linux")
    @patch("shared.deploy.local_docker.subprocess.run")
    @patch(
        "shared.deploy.local_docker._inspect_exact_container_checked",
        return_value=(None, None),
    )
    def test_managed_explicit_docker_network_avoids_host_namespace(
        self, _inspect, mock_run, monkeypatch
    ):
        monkeypatch.setenv("RDST_DOCKER_NETWORK", "rdst-e2e_default")
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=0),
            MagicMock(returncode=0, stdout="container-id", stderr=""),
            MagicMock(returncode=0, stdout="container-id", stderr=""),
        ]
        variables = self.variables()
        variables["db_host"] = "postgres"

        result = local_docker.deploy_managed_sandbox(
            "origin", variables, "secret", "fingerprint"
        )

        assert result["success"] is True
        command = mock_run.call_args_list[2].args[0]
        assert "--network=host" not in command
        network_index = command.index("--network")
        assert command[network_index + 1] == "rdst-e2e_default"
        assert "127.0.0.1:5433:5433" in command
        assert "LISTEN_ADDRESS=0.0.0.0:5433" in command
        assert any(
            value.startswith(
                "UPSTREAM_DB_URL=postgresql://app:secret@postgres:5432/"  # trufflehog:ignore
            )
            for value in command
        )

    @patch("shared.deploy.docker_topology.sys.platform", "linux")
    @patch("shared.deploy.local_docker.subprocess.run")
    @patch(
        "shared.deploy.local_docker._inspect_exact_container_checked",
        return_value=(None, None),
    )
    def test_managed_userns_daemon_falls_back_to_bridge_network(
        self, _inspect, mock_run
    ):
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=0),
            MagicMock(
                returncode=125,
                stdout="",
                stderr=(
                    "Error response from daemon: cannot share the host's "
                    "network namespace when user namespaces are enabled"
                ),
            ),
            MagicMock(returncode=0, stdout="container-id", stderr=""),
            MagicMock(returncode=0, stdout="container-id", stderr=""),
        ]

        result = local_docker.deploy_managed_sandbox(
            "origin", self.variables(), "secret", "fingerprint"
        )

        assert result["success"] is True
        assert "--network=host" in mock_run.call_args_list[2].args[0]
        retried = mock_run.call_args_list[3].args[0]
        assert "--network=host" not in retried
        assert "127.0.0.1:5433:5433" in retried
        assert "--add-host=host.docker.internal:host-gateway" in retried
        assert "LISTEN_ADDRESS=0.0.0.0:5433" in retried
        assert any(
            value.startswith(
                "UPSTREAM_DB_URL=postgresql://app:secret@host.docker.internal:5432/"  # trufflehog:ignore
            )
            for value in retried
        )

    @patch("shared.deploy.docker_topology.sys.platform", "linux")
    @patch("shared.deploy.local_docker.subprocess.run")
    @patch(
        "shared.deploy.local_docker._inspect_exact_container_checked",
        return_value=(None, None),
    )
    def test_managed_unrelated_create_failure_keeps_host_network(
        self, _inspect, mock_run
    ):
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=0),
            MagicMock(
                returncode=125,
                stdout="",
                stderr="Error response from daemon: no such image",
            ),
        ]

        result = local_docker.deploy_managed_sandbox(
            "origin", self.variables(), "secret", "fingerprint"
        )

        assert result["success"] is False
        assert mock_run.call_count == 3

    @patch("shared.deploy.docker_topology.sys.platform", "linux")
    @patch("shared.deploy.local_docker.subprocess.run")
    @patch(
        "shared.deploy.local_docker._find_existing_container", return_value=None
    )
    def test_legacy_deploy_falls_back_to_bridge_network(
        self, _existing, mock_run
    ):
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(
                returncode=125,
                stdout="",
                stderr=(
                    "Error response from daemon: cannot share the host's "
                    "network namespace when user namespaces are enabled"
                ),
            ),
            MagicMock(returncode=0, stdout="container-id", stderr=""),
        ]

        result = local_docker.deploy_local_docker(
            "origin", self.variables(), "secret"
        )

        assert result["success"] is True
        assert "--network=host" in mock_run.call_args_list[1].args[0]
        retried = mock_run.call_args_list[2].args[0]
        assert "--network=host" not in retried
        assert "127.0.0.1:5433:5433" in retried
        assert "127.0.0.1:6034:6034" in retried
        assert "--add-host=host.docker.internal:host-gateway" in retried
        assert "METRICS_ADDRESS=0.0.0.0:6034" in retried

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

    @patch("shared.deploy.local_docker.subprocess.run")
    @patch("shared.deploy.local_docker._inspect_exact_container_checked")
    def test_managed_remove_uses_immutable_id_and_spares_replacement(
        self, inspect, run
    ):
        inspect.side_effect = [
            ({"id": "old-id", "managed": "true"}, None),
            ({"id": "replacement-id", "managed": ""}, None),
        ]
        run.return_value = MagicMock(returncode=0, stdout="old-id", stderr="")

        result = local_docker.remove_managed_sandbox()

        assert result == {"success": True, "removed": True}
        assert run.call_args.args[0] == ["docker", "rm", "-f", "old-id"]

    @patch("shared.deploy.local_docker.subprocess.run")
    @patch("shared.deploy.local_docker._inspect_exact_container_checked")
    def test_managed_remove_requires_verified_postcondition(self, inspect, run):
        existing = {"id": "same-id", "managed": "true"}
        inspect.side_effect = [(existing, None), (existing, None)]
        run.return_value = MagicMock(returncode=0, stdout="same-id", stderr="")

        result = local_docker.remove_managed_sandbox()

        assert result["success"] is False
        assert "did not remove" in result["error"]

    @patch("shared.deploy.local_docker.subprocess.run")
    @patch("shared.deploy.local_docker._inspect_exact_container_checked")
    def test_startup_failure_classifies_oom_without_exposing_logs(
        self, inspect, run
    ):
        inspect.return_value = (
            {
                "id": "container-id",
                "running": False,
                "oom_killed": True,
                "exit_code": "137",
            },
            None,
        )
        run.return_value = MagicMock(
            returncode=0,
            stdout="password=hunter2 internal details",
            stderr="",
        )

        result = local_docker.managed_sandbox_startup_failure()

        assert result["kind"] == "oom"
        assert "hunter2" not in result["error"]

    @patch("shared.deploy.local_docker.subprocess.run")
    @patch("shared.deploy.local_docker._inspect_exact_container_checked")
    def test_startup_failure_classifies_async_port_bind_race(
        self, inspect, run
    ):
        inspect.return_value = (
            {
                "id": "container-id",
                "running": False,
                "oom_killed": False,
                "exit_code": "1",
            },
            None,
        )
        run.return_value = MagicMock(
            returncode=0,
            stdout="listen tcp 127.0.0.1:5433: bind: address already in use",
            stderr="",
        )

        result = local_docker.managed_sandbox_startup_failure()

        assert result == {
            "kind": "port_conflict",
            "error": "Readyset could not bind the selected SQL port",
        }

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
        "shared.deploy.local_docker._docker_inspect_state_checked",
        return_value=(None, "Docker daemon unavailable"),
    )
    def test_legacy_remove_does_not_claim_absence_when_daemon_is_down(
        self, _inspect
    ):
        result = local_docker.remove_configured_legacy_container(
            "rdst-readyset-production"
        )

        assert result["success"] is False
        assert result["removed"] is False
        assert "Docker" in result["error"]

    @patch(
        "shared.deploy.local_docker._docker_inspect_state_checked",
        return_value=(None, "docker inspect timed out"),
    )
    def test_legacy_remove_preserves_config_after_inspection_timeout(
        self, _inspect
    ):
        result = local_docker.remove_configured_legacy_container(
            "rdst-readyset-production"
        )

        assert result["success"] is False
        assert result["removed"] is False
        assert "timed out" in result["error"]
