"""Cross-platform editor command resolution tests."""

from __future__ import annotations

from unittest.mock import patch

from shared.editor import resolve_editor_command


def test_resolve_editor_preserves_configured_arguments():
    with patch("shared.editor.shutil.which", return_value="/usr/bin/code"):
        command = resolve_editor_command("code --wait", windows=False)

    assert command == ["/usr/bin/code", "--wait"]


def test_resolve_editor_handles_quoted_windows_path():
    configured = r'"C:\Program Files\Editor\editor.exe" --wait'
    with patch(
        "shared.editor.shutil.which",
        return_value=r"C:\Program Files\Editor\editor.exe",
    ):
        command = resolve_editor_command(configured, windows=True)

    assert command == [r"C:\Program Files\Editor\editor.exe", "--wait"]


def test_resolve_editor_uses_notepad_on_windows():
    def which(name):
        return r"C:\Windows\System32\notepad.exe" if name == "notepad" else None

    with patch("shared.editor.shutil.which", side_effect=which):
        command = resolve_editor_command(None, windows=True)

    assert command == [r"C:\Windows\System32\notepad.exe"]


def test_resolve_editor_returns_none_when_no_candidate_exists():
    with patch("shared.editor.shutil.which", return_value=None):
        assert resolve_editor_command(None, windows=False) is None
