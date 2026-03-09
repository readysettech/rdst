"""Tests for shallow mode container functions."""

import json
import subprocess
import pytest
from unittest.mock import Mock, patch

from lib.functions.readyset_container import (
    start_readyset_container_direct,
    wait_for_readyset_ready_shallow,
    check_readyset_container_status,
)


class TestStartReadysetContainerDirect:

    @pytest.fixture
    def base_config(self):
        return {
            "engine": "postgresql",
            "host": "localhost",
            "port": 5432,
            "database": "testdb",
            "user": "testuser",
            "password": "testpass",
        }

    def test_returns_dict(self, base_config):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
            result = start_readyset_container_direct(target_config=base_config)
            assert isinstance(result, dict)

    def test_recreates_existing_container(self, base_config):
        """Existing containers are removed and recreated to ensure latest image."""
        call_count = 0

        def mock_subprocess(*args, **_):
            nonlocal call_count
            call_count += 1
            cmd = args[0]
            if cmd[0:2] == ['docker', 'ps'] and '-a' in cmd:
                return Mock(returncode=0, stdout="rdst-readyset\tUp 5 minutes", stderr="")
            elif cmd[0:3] == ['docker', 'rm', '-f']:
                return Mock(returncode=0, stdout="", stderr="")
            elif cmd[0:2] == ['docker', 'run']:
                return Mock(returncode=0, stdout="container_id", stderr="")
            elif cmd[0:2] == ['docker', 'ps']:
                return Mock(returncode=0, stdout="rdst-readyset", stderr="")
            return Mock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=mock_subprocess):
            result = start_readyset_container_direct(
                target_config=base_config,
                readyset_container_name="rdst-readyset",
            )

            assert result["success"] is True
            assert result["shallow_mode"] is True
            # Should have called: ps -a, rm -f, run, ps (verify)
            assert call_count >= 3

    def test_removes_stopped_container(self, base_config):
        """Stopped containers should be removed and recreated."""
        def mock_subprocess(*args, **_):
            cmd = args[0]
            if cmd[0:2] == ['docker', 'ps'] and '-a' in cmd:
                return Mock(returncode=0, stdout="rdst-readyset\tExited (1) 1 hour ago", stderr="")
            elif cmd[0:3] == ['docker', 'rm', '-f']:
                return Mock(returncode=0, stdout="", stderr="")
            elif cmd[0:2] == ['docker', 'run']:
                return Mock(returncode=0, stdout="container_id", stderr="")
            elif cmd[0:2] == ['docker', 'ps']:
                return Mock(returncode=0, stdout="rdst-readyset", stderr="")
            return Mock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=mock_subprocess):
            result = start_readyset_container_direct(
                target_config=base_config,
                readyset_container_name="rdst-readyset",
            )
            assert result["success"] is True
            assert "created" in result

    def test_postgresql_upstream_url(self, base_config):
        """PostgreSQL config should produce postgresql:// UPSTREAM_DB_URL."""
        docker_run_cmd = None

        def capture_cmd(*args, **_):
            nonlocal docker_run_cmd
            cmd = args[0]
            if cmd[0:2] == ['docker', 'run']:
                docker_run_cmd = cmd
                return Mock(returncode=0, stdout="container_id", stderr="")
            elif cmd[0:2] == ['docker', 'ps'] and '-a' in cmd:
                return Mock(returncode=0, stdout="", stderr="")
            elif cmd[0:2] == ['docker', 'ps']:
                return Mock(returncode=0, stdout="rdst-readyset", stderr="")
            return Mock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=capture_cmd):
            result = start_readyset_container_direct(target_config=base_config, readyset_port=5433)

            assert result["success"] is True
            assert any("UPSTREAM_DB_URL=postgresql://" in arg for arg in docker_run_cmd)

    def test_mysql_upstream_url(self):
        """MySQL config should produce mysql:// UPSTREAM_DB_URL."""
        mysql_config = {
            "engine": "mysql",
            "host": "localhost",
            "port": 3306,
            "database": "testdb",
            "user": "testuser",
            "password": "testpass",
        }
        docker_run_cmd = None

        def capture_cmd(*args, **_):
            nonlocal docker_run_cmd
            cmd = args[0]
            if cmd[0:2] == ['docker', 'run']:
                docker_run_cmd = cmd
                return Mock(returncode=0, stdout="container_id", stderr="")
            elif cmd[0:2] == ['docker', 'ps'] and '-a' in cmd:
                return Mock(returncode=0, stdout="", stderr="")
            elif cmd[0:2] == ['docker', 'ps']:
                return Mock(returncode=0, stdout="rdst-readyset", stderr="")
            return Mock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=capture_cmd):
            result = start_readyset_container_direct(target_config=mysql_config, readyset_port=3307)

            assert result["success"] is True
            assert any("UPSTREAM_DB_URL=mysql://" in arg for arg in docker_run_cmd)

    def test_localhost_becomes_docker_internal(self, base_config):
        """localhost should be converted to host.docker.internal for docker."""
        docker_run_cmd = None

        def capture_cmd(*args, **_):
            nonlocal docker_run_cmd
            cmd = args[0]
            if cmd[0:2] == ['docker', 'run']:
                docker_run_cmd = cmd
                return Mock(returncode=0, stdout="container_id", stderr="")
            elif cmd[0:2] == ['docker', 'ps'] and '-a' in cmd:
                return Mock(returncode=0, stdout="", stderr="")
            elif cmd[0:2] == ['docker', 'ps']:
                return Mock(returncode=0, stdout="rdst-readyset", stderr="")
            return Mock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=capture_cmd):
            result = start_readyset_container_direct(target_config=base_config)

            assert result["success"] is True
            assert any("host.docker.internal" in arg for arg in docker_run_cmd if "UPSTREAM_DB_URL=" in arg)

    def test_password_from_env(self, monkeypatch):
        monkeypatch.setenv("MY_DB_PASSWORD", "env_secret")

        config = {
            "engine": "postgresql",
            "host": "remotehost.example.com",
            "port": 5432,
            "database": "testdb",
            "user": "testuser",
            "password_env": "MY_DB_PASSWORD",
        }
        docker_run_cmd = None

        def capture_cmd(*args, **_):
            nonlocal docker_run_cmd
            cmd = args[0]
            if cmd[0:2] == ['docker', 'run']:
                docker_run_cmd = cmd
                return Mock(returncode=0, stdout="container_id", stderr="")
            elif cmd[0:2] == ['docker', 'ps'] and '-a' in cmd:
                return Mock(returncode=0, stdout="", stderr="")
            elif cmd[0:2] == ['docker', 'ps']:
                return Mock(returncode=0, stdout="rdst-readyset", stderr="")
            return Mock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=capture_cmd):
            result = start_readyset_container_direct(target_config=config)

            assert result["success"] is True
            assert any("env_secret@" in arg for arg in docker_run_cmd if "UPSTREAM_DB_URL=" in arg)

    def test_docker_run_error(self, base_config):
        """Docker run failures should return error result."""
        def mock_subprocess(*args, **_):
            cmd = args[0]
            if cmd[0:2] == ['docker', 'ps'] and '-a' in cmd:
                return Mock(returncode=0, stdout="", stderr="")
            elif cmd[0:2] == ['docker', 'run']:
                return Mock(returncode=1, stdout="", stderr="Failed to pull image")
            return Mock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=mock_subprocess):
            result = start_readyset_container_direct(target_config=base_config)

            assert result["success"] is False
            assert "Failed" in result["error"]

    def test_container_crash(self, base_config):
        def mock_subprocess(*args, **_):
            cmd = args[0]
            if cmd[0:2] == ['docker', 'ps'] and '-a' in cmd:
                return Mock(returncode=0, stdout="", stderr="")
            elif cmd[0:2] == ['docker', 'run']:
                return Mock(returncode=0, stdout="container_id", stderr="")
            elif cmd[0:2] == ['docker', 'ps']:
                return Mock(returncode=0, stdout="", stderr="")
            elif cmd[0:2] == ['docker', 'logs']:
                return Mock(returncode=0, stdout="Fatal error: connection refused", stderr="")
            return Mock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=mock_subprocess):
            with patch("time.sleep"):
                result = start_readyset_container_direct(target_config=base_config)

            assert result["success"] is False
            # Clean error handling returns user-friendly message about upstream connection
            assert "upstream" in result["error"].lower() or "connect" in result["error"].lower()

    def test_timeout(self, base_config):
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="docker", timeout=5)

            result = start_readyset_container_direct(target_config=base_config)

            assert result["success"] is False
            # Clean error handling returns user-friendly timeout message
            assert "timeout" in result["error"].lower() or "not responding" in result["error"].lower()

    def test_json_string_config(self):
        config = {
            "engine": "postgresql",
            "host": "localhost",
            "port": 5432,
            "database": "testdb",
            "user": "testuser",
            "password": "testpass",
        }

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout="rdst-readyset\tUp 5 minutes", stderr="")

            result = start_readyset_container_direct(target_config=json.dumps(config))

            assert result["success"] is True


class TestWaitForReadysetReadyShallow:

    def test_success(self):
        def mock_subprocess(*args, **_):
            cmd = args[0]
            if cmd[0:2] == ['docker', 'ps']:
                return Mock(returncode=0, stdout="rdst-readyset", stderr="")
            elif cmd[0:2] == ['docker', 'logs']:
                return Mock(returncode=0, stdout="INFO Streaming replication started\n", stderr="")
            return Mock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=mock_subprocess):
            with patch("time.sleep"):
                result = wait_for_readyset_ready_shallow(timeout=120)

                assert isinstance(result, dict)
                assert result["success"] is True

    def test_detects_streaming_replication(self):
        def mock_subprocess(*args, **_):
            cmd = args[0]
            if cmd[0:2] == ['docker', 'ps']:
                return Mock(returncode=0, stdout="rdst-readyset", stderr="")
            elif cmd[0:2] == ['docker', 'logs']:
                return Mock(returncode=0, stdout="INFO Starting up\nINFO Streaming replication started\n", stderr="")
            return Mock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=mock_subprocess):
            with patch("time.time") as mock_time:
                mock_time.side_effect = [0, 1, 2]
            with patch("time.sleep"):
                result = wait_for_readyset_ready_shallow(timeout=120)

                assert result["success"] is True
                assert result["ready"] is True
                assert result["shallow_mode"] is True

    def test_container_not_running(self):
        def mock_subprocess(*args, **_):
            cmd = args[0]
            if cmd[0:2] == ['docker', 'ps']:
                return Mock(returncode=0, stdout="", stderr="")
            elif cmd[0:3] == ['docker', 'inspect']:
                return Mock(returncode=0, stdout="exited 1", stderr="")
            elif cmd[0:2] == ['docker', 'logs']:
                return Mock(returncode=0, stdout="Error: connection refused", stderr="")
            return Mock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=mock_subprocess):
            with patch("time.time") as mock_time:
                mock_time.return_value = 0
            with patch("time.sleep"):
                with pytest.raises(Exception) as exc_info:
                    wait_for_readyset_ready_shallow(timeout=120)

                assert "not running" in str(exc_info.value).lower()

    def test_fatal_error_in_logs(self):
        def mock_subprocess(*args, **_):
            cmd = args[0]
            if cmd[0:2] == ['docker', 'ps']:
                return Mock(returncode=0, stdout="rdst-readyset", stderr="")
            elif cmd[0:2] == ['docker', 'logs']:
                return Mock(returncode=0, stdout="INFO Starting\nFATAL: database connection failed\n", stderr="")
            return Mock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=mock_subprocess):
            with patch("time.time") as mock_time:
                mock_time.side_effect = [0, 1]
            with patch("time.sleep"):
                with pytest.raises(Exception) as exc_info:
                    wait_for_readyset_ready_shallow(timeout=120)

                assert "fatal" in str(exc_info.value).lower()

    def test_timeout(self):
        def mock_subprocess(*args, **_):
            cmd = args[0]
            if cmd[0:2] == ['docker', 'ps']:
                return Mock(returncode=0, stdout="rdst-readyset", stderr="")
            elif cmd[0:2] == ['docker', 'logs']:
                return Mock(returncode=0, stdout="INFO Still snapshotting table users...\n", stderr="")
            return Mock(returncode=0, stdout="", stderr="")

        times = [0, 5, 10, 15, 130]
        time_idx = 0

        def mock_time():
            nonlocal time_idx
            t = times[time_idx] if time_idx < len(times) else 200
            time_idx += 1
            return t

        with patch("subprocess.run", side_effect=mock_subprocess):
            with patch("time.time", side_effect=mock_time):
                with patch("time.sleep"):
                    with pytest.raises(Exception) as exc_info:
                        wait_for_readyset_ready_shallow(timeout=120)

                    assert "did not become ready" in str(exc_info.value).lower()


class TestCheckReadysetContainerStatus:

    def test_no_container(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout="", stderr="")

            result = check_readyset_container_status("rdst-readyset")

            assert result["success"] is True
            assert result["exists"] is False
            assert result["running"] is False

    def test_running(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout="rdst-readyset\tUp 5 minutes\tabc123", stderr="")

            result = check_readyset_container_status("rdst-readyset")

            assert result["success"] is True
            assert result["exists"] is True
            assert result["running"] is True
            assert result["container_name"] == "rdst-readyset"

    def test_stopped(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout="rdst-readyset\tExited (1) 1 hour ago\tabc123", stderr="")

            result = check_readyset_container_status("rdst-readyset")

            assert result["success"] is True
            assert result["exists"] is True
            assert result["running"] is False

    def test_docker_error(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=1, stdout="", stderr="Cannot connect to Docker daemon")

            result = check_readyset_container_status("rdst-readyset")

            assert result["success"] is False
            assert "error" in result

    def test_exception(self):
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = Exception("Unexpected error")

            result = check_readyset_container_status("rdst-readyset")

            assert result["success"] is False
            assert "error" in result
