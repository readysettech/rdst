from __future__ import annotations

import subprocess
from unittest import mock


class TestDockerAvailable:

    def test_docker_in_path(self):
        from lib.api.docker_prepull import _docker_available

        with mock.patch("shutil.which", return_value="/usr/local/bin/docker"):
            assert _docker_available() is True

    def test_docker_not_in_path(self):
        from lib.api.docker_prepull import _docker_available

        with mock.patch("shutil.which", return_value=None):
            assert _docker_available() is False


class TestPullImage:

    def test_successful_pull(self, capsys):
        import lib.api.docker_prepull as prepull

        prepull._pull_status = {
            "started": True,
            "completed": False,
            "success": False,
            "error": None,
        }

        mock_result = mock.Mock()
        mock_result.returncode = 0

        with mock.patch("subprocess.run", return_value=mock_result):
            prepull._pull_image()

        assert prepull._pull_status["success"] is True
        assert prepull._pull_status["completed"] is True
        assert prepull._pull_status["error"] is None

        captured = capsys.readouterr()
        assert "Pre-pulled ReadySet image" in captured.out

    def test_failed_pull_with_stderr(self, capsys):
        import lib.api.docker_prepull as prepull

        prepull._pull_status = {
            "started": True,
            "completed": False,
            "success": False,
            "error": None,
        }

        mock_result = mock.Mock()
        mock_result.returncode = 1
        mock_result.stderr = "Cannot connect to Docker daemon"

        with mock.patch("subprocess.run", return_value=mock_result):
            prepull._pull_image()

        assert prepull._pull_status["success"] is False
        assert prepull._pull_status["completed"] is True
        assert prepull._pull_status["error"] == "Cannot connect to Docker daemon"

        captured = capsys.readouterr()
        assert "pre-pull failed" in captured.out

    def test_failed_pull_empty_stderr(self):
        import lib.api.docker_prepull as prepull

        prepull._pull_status = {
            "started": True,
            "completed": False,
            "success": False,
            "error": None,
        }

        mock_result = mock.Mock()
        mock_result.returncode = 1
        mock_result.stderr = ""

        with mock.patch("subprocess.run", return_value=mock_result):
            prepull._pull_image()

        assert prepull._pull_status["error"] == "Pull failed"

    def test_timeout(self, capsys):
        import lib.api.docker_prepull as prepull

        prepull._pull_status = {
            "started": True,
            "completed": False,
            "success": False,
            "error": None,
        }

        with mock.patch(
            "subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="docker", timeout=600)
        ):
            prepull._pull_image()

        assert prepull._pull_status["success"] is False
        assert prepull._pull_status["completed"] is True
        assert "timed out" in prepull._pull_status["error"]

        captured = capsys.readouterr()
        assert "timed out" in captured.out

    def test_exception(self, capsys):
        import lib.api.docker_prepull as prepull

        prepull._pull_status = {
            "started": True,
            "completed": False,
            "success": False,
            "error": None,
        }

        with mock.patch("subprocess.run", side_effect=OSError("Permission denied")):
            prepull._pull_image()

        assert prepull._pull_status["success"] is False
        assert prepull._pull_status["completed"] is True
        assert "Permission denied" in prepull._pull_status["error"]

        captured = capsys.readouterr()
        assert "Permission denied" in captured.out


class TestStartPrepull:

    def test_noop_if_already_started(self):
        import lib.api.docker_prepull as prepull

        prepull._pull_status = {
            "started": True,
            "completed": False,
            "success": False,
            "error": None,
        }

        with mock.patch("threading.Thread") as mock_thread:
            prepull.start_prepull()
            mock_thread.assert_not_called()

    def test_noop_if_docker_missing(self):
        import lib.api.docker_prepull as prepull

        prepull._pull_status = {
            "started": False,
            "completed": False,
            "success": False,
            "error": None,
        }

        with mock.patch.object(prepull, "_docker_available", return_value=False):
            with mock.patch("threading.Thread") as mock_thread:
                prepull.start_prepull()
                mock_thread.assert_not_called()

        assert prepull._pull_status["started"] is False

    def test_starts_daemon_thread(self, capsys):
        import lib.api.docker_prepull as prepull

        prepull._pull_status = {
            "started": False,
            "completed": False,
            "success": False,
            "error": None,
        }

        mock_thread_instance = mock.Mock()

        with mock.patch.object(prepull, "_docker_available", return_value=True):
            with mock.patch(
                "threading.Thread", return_value=mock_thread_instance
            ) as mock_thread_class:
                prepull.start_prepull()

                mock_thread_class.assert_called_once()
                call_kwargs = mock_thread_class.call_args[1]
                assert call_kwargs["target"] == prepull._pull_image
                assert call_kwargs["daemon"] is True

                mock_thread_instance.start.assert_called_once()

        assert prepull._pull_status["started"] is True

        captured = capsys.readouterr()
        assert "Pre-pulling ReadySet Docker image" in captured.out


class TestGetPrepullStatus:

    def test_returns_copy(self):
        import lib.api.docker_prepull as prepull

        prepull._pull_status = {
            "started": True,
            "completed": True,
            "success": True,
            "error": None,
        }

        status = prepull.get_prepull_status()

        assert status == prepull._pull_status
        assert status is not prepull._pull_status

        # mutating the copy shouldn't affect the original
        status["success"] = False
        assert prepull._pull_status["success"] is True

    def test_has_all_fields(self):
        import lib.api.docker_prepull as prepull

        prepull._pull_status = {
            "started": True,
            "completed": True,
            "success": False,
            "error": "Some error",
        }

        status = prepull.get_prepull_status()

        assert "started" in status
        assert "completed" in status
        assert "success" in status
        assert "error" in status
        assert status["error"] == "Some error"


class TestEndToEnd:

    def test_full_flow(self):
        import lib.api.docker_prepull as prepull

        prepull._pull_status = {
            "started": False,
            "completed": False,
            "success": False,
            "error": None,
        }
        prepull._pull_thread = None

        mock_result = mock.Mock()
        mock_result.returncode = 0

        with mock.patch.object(prepull, "_docker_available", return_value=True):
            with mock.patch("subprocess.run", return_value=mock_result):
                prepull.start_prepull()

                if prepull._pull_thread:
                    prepull._pull_thread.join(timeout=5)

        status = prepull.get_prepull_status()
        assert status["started"] is True
        assert status["completed"] is True
        assert status["success"] is True
        assert status["error"] is None

    def test_only_starts_once(self):
        import lib.api.docker_prepull as prepull

        prepull._pull_status = {
            "started": False,
            "completed": False,
            "success": False,
            "error": None,
        }

        call_count = 0

        def mock_thread_start():
            nonlocal call_count
            call_count += 1

        mock_thread = mock.Mock()
        mock_thread.start = mock_thread_start

        with mock.patch.object(prepull, "_docker_available", return_value=True):
            with mock.patch("threading.Thread", return_value=mock_thread):
                prepull.start_prepull()
                prepull.start_prepull()
                prepull.start_prepull()

        assert call_count == 1
