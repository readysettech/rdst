"""Build argv for re-invoking the rdst CLI as a child process.

Some flows (demo tour, `top`/`scan` -> `analyze`) spawn a fresh rdst process.
These must resolve the installed package, not a source file: `python -m rdst`
finds rdst on the interpreter's import path in both installed and editable
layouts, so it works regardless of the child's working directory. Spawning a
literal ``rdst.py`` only works when the cwd happens to contain that file, which
breaks for anyone running a pip-installed ``rdst`` from another directory.
"""

from __future__ import annotations

import sys


def rdst_child_argv(args: list[str]) -> list[str]:
    """Return argv that runs the rdst CLI with ``args`` via the current
    interpreter, e.g. ``[python, "-m", "rdst", "analyze", "-q", "..."]``."""
    return [sys.executable, "-m", "rdst", *args]
