"""Required-email behavior for the ``rdst report`` CLI."""

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from shared.cli.report_command import ReportCommand


RDST_ROOT = Path(__file__).resolve().parents[2]


def test_report_command_import_does_not_load_api_package():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import shared.cli.report_command; "
            "assert 'shared.api' not in sys.modules",
        ],
        cwd=RDST_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_noninteractive_report_requires_email():
    command = ReportCommand(console=MagicMock())

    with (
        patch("sys.stdin") as stdin,
        patch("shared.telemetry.telemetry.submit_feedback") as submit_feedback,
    ):
        stdin.isatty.return_value = False
        result = command.run(reason="Great tool")

    assert result is False
    submit_feedback.assert_not_called()


def test_noninteractive_report_normalizes_and_sends_email():
    command = ReportCommand(console=MagicMock())

    with (
        patch("sys.stdin") as stdin,
        patch("shared.telemetry.telemetry.submit_feedback") as submit_feedback,
    ):
        stdin.isatty.return_value = False
        result = command.run(
            reason="Great tool",
            email="  Feedback@Example.COM ",
        )

    assert result is True
    submit_feedback.assert_called_once()
    assert submit_feedback.call_args.kwargs["email"] == "feedback@example.com"
