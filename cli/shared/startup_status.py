"""Startup progress markers for the process that hosts the API.

The desktop shell starts ``rdst web`` and waits for its HTTP API. A data-file upgrade can
hold startup for minutes before the server binds its port, so the server reports what it
is doing on stdout, one line per change:

    RDST_STARTUP {"state": "migrating", "store": "library.db"}

``starting`` comes first; ``migrating`` precedes schema or data migration work and
``migrated`` follows it, failed or not, with ``pending`` counting migrations still running.
A shell that sees ``migrating`` lets the process finish rather than timing it out.

Markers are written only after ``enable()``, which ``rdst web`` calls, so CLI commands that
open the same stores never print them.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from contextlib import contextmanager
from typing import Iterator

MARKER = "RDST_STARTUP"
ENV_VAR = "RDST_STARTUP_MARKERS"

_lock = threading.Lock()
_active = 0


def enable() -> None:
    """Turn markers on for this process and any worker it spawns."""
    os.environ[ENV_VAR] = "1"


def enabled() -> bool:
    return os.environ.get(ENV_VAR) == "1"


def format_marker(state: str, **fields: object) -> str:
    return f"{MARKER} {json.dumps({'state': state, **fields}, sort_keys=True)}"


def emit(state: str, **fields: object) -> None:
    if not enabled():
        return
    try:
        sys.stdout.write(format_marker(state, **fields) + "\n")
        sys.stdout.flush()
    except (AttributeError, OSError, ValueError):
        # A missing, closed or broken stdout must never fail the work being reported.
        pass


def begin(store: str) -> str:
    """Report that migration work on ``store`` starts; pair with ``end``."""
    global _active
    with _lock:
        _active += 1
        emit("migrating", store=store)
    return store


def end(store: str) -> None:
    """Report that migration work on ``store`` stopped, finished or failed.

    Overlapping migrations are counted, so ``pending`` is 0 only once the last has ended.
    """
    global _active
    with _lock:
        _active = max(0, _active - 1)
        emit("migrated", store=store, pending=_active)


@contextmanager
def migrating(store: str) -> Iterator[None]:
    """Report migration work on ``store`` for the duration of the block."""
    begin(store)
    try:
        yield
    finally:
        end(store)
