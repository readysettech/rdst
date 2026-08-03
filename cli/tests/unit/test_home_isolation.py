"""Regression tests for platform-independent RDST home isolation."""

from __future__ import annotations

import os
from pathlib import Path


def test_tmp_rdst_home_isolates_windows_home_variables(tmp_rdst_home: Path):
    expected_home = str(tmp_rdst_home.parent)
    drive, tail = os.path.splitdrive(expected_home)

    assert os.environ["HOME"] == expected_home
    assert os.environ["USERPROFILE"] == expected_home
    assert os.environ["HOMEDRIVE"] == drive
    assert os.environ["HOMEPATH"] == tail
