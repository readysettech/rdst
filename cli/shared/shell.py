"""Platform-appropriate shell guidance."""

from __future__ import annotations

import os
import re


def environment_assignment(
    name: str,
    value: str,
    *,
    windows: bool | None = None,
) -> str:
    """Render an environment assignment for the platform's default shell."""
    is_windows = os.name == "nt" if windows is None else windows
    if is_windows:
        escaped = value.replace("`", "``").replace('"', '`"')
        return f'$env:{name} = "{escaped}"'
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'export {name}="{escaped}"'


def adapt_shell_guidance(text: str, *, windows: bool | None = None) -> str:
    """Translate portable environment examples for PowerShell readers."""
    is_windows = os.name == "nt" if windows is None else windows
    if not is_windows:
        return text

    pattern = re.compile(r"export (?!PATH\b)([A-Z][A-Z0-9_]*)=([\"'])(.*?)\2")
    adapted = pattern.sub(
        lambda match: environment_assignment(
            match.group(1), match.group(3), windows=True
        ),
        text,
    )
    return adapted.replace("Export it:", "Set it:").replace("$EDITOR", "EDITOR")
