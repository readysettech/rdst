"""Regression tests for spawning child rdst processes.

The demo tour and the `top`/`scan` -> `analyze` flows re-invoke rdst as a
child process. They must resolve the installed package and work from any
working directory, including one that does not contain rdst.py -- the exact
case that failed for pip-installed users (a child spawned as `python rdst.py`
resolved against the user's cwd and raised "can't open file rdst.py").
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from shared.child_process import rdst_child_argv

RDST_ROOT = Path(__file__).resolve().parents[1]


def test_rdst_child_argv_uses_module_invocation():
    assert rdst_child_argv(["analyze", "-q", "SELECT 1"]) == [
        sys.executable, "-m", "rdst", "analyze", "-q", "SELECT 1",
    ]


def test_rdst_child_argv_empty():
    assert rdst_child_argv([]) == [sys.executable, "-m", "rdst"]


def test_rdst_runs_from_unrelated_directory(tmp_path):
    """`-m rdst` resolves rdst regardless of cwd. Running from tmp_path (which
    has no rdst.py) reproduces the pip-installed user's environment; PYTHONPATH
    stands in for the installed package so the test does not require an install."""
    env = {**os.environ, "PYTHONPATH": str(RDST_ROOT), "RDST_TESTING": "true"}
    proc = subprocess.run(
        rdst_child_argv(["version"]),
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert "rdst" in proc.stdout.lower()
