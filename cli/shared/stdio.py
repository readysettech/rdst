"""Process text-stream configuration."""

from __future__ import annotations

import os
import sys


def configure_utf8_stdio(*, force: bool = False, line_buffering: bool = False) -> None:
    """Use UTF-8 for Windows CLI pipes and protocol streams."""
    if not force and os.name != "nt":
        return

    stdin_reconfigure = getattr(sys.stdin, "reconfigure", None)
    if stdin_reconfigure is not None:
        try:
            stdin_reconfigure(encoding="utf-8")
        except (OSError, ValueError):
            pass

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(
                encoding="utf-8",
                errors="replace",
                line_buffering=line_buffering,
            )
        except (OSError, ValueError):
            pass
