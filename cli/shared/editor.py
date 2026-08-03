"""Cross-platform editor command resolution."""

from __future__ import annotations

import os
import shlex
import shutil


def _split_editor_command(value: str, *, windows: bool) -> list[str]:
    parts = shlex.split(value, posix=not windows)
    if windows:
        parts = [
            part[1:-1]
            if len(part) >= 2 and part[0] == part[-1] and part[0] in "\"'"
            else part
            for part in parts
        ]
    return parts


def resolve_editor_command(
    configured: str | None = None,
    *,
    windows: bool | None = None,
) -> list[str] | None:
    """Resolve an editor executable while preserving configured arguments."""
    is_windows = os.name == "nt" if windows is None else windows
    value = configured or os.environ.get("EDITOR") or os.environ.get("VISUAL")
    candidates = [value] if value else []
    candidates.extend(["notepad"] if is_windows else ["vim", "nano", "vi", "emacs"])

    for candidate in candidates:
        if not candidate:
            continue
        try:
            parts = _split_editor_command(candidate, windows=is_windows)
        except ValueError:
            continue
        if not parts:
            continue
        executable = shutil.which(parts[0])
        if executable:
            return [executable, *parts[1:]]
    return None
