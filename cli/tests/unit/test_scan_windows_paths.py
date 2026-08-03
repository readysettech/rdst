"""Windows path regressions for Git-diff scanning."""

from __future__ import annotations

from subprocess import CompletedProcess
from unittest.mock import patch

from features.scan.service import ScanService


def test_filter_by_diff_matches_nested_windows_paths():
    responses = [
        CompletedProcess(["git"], 0, stdout="C:\\repo\n", stderr=""),
        CompletedProcess(["git"], 0, stdout="app/services/orders.py\n", stderr=""),
    ]
    orm_files = [{"file": r"app\services\orders.py", "orms": ["sqlalchemy"]}]

    with (
        patch("subprocess.run", side_effect=responses),
        patch("features.scan.service.os.path.relpath", return_value="."),
    ):
        filtered = ScanService()._filter_by_diff(orm_files, r"C:\repo", "HEAD")

    assert filtered == orm_files
