"""Tests for UTF-8 process text boundaries."""

from __future__ import annotations

from unittest.mock import MagicMock

from shared.stdio import configure_utf8_stdio


def test_configure_utf8_stdio_reconfigures_all_streams(monkeypatch):
    stdin = MagicMock()
    stdout = MagicMock()
    stderr = MagicMock()
    monkeypatch.setattr("shared.stdio.sys.stdin", stdin)
    monkeypatch.setattr("shared.stdio.sys.stdout", stdout)
    monkeypatch.setattr("shared.stdio.sys.stderr", stderr)

    configure_utf8_stdio(force=True, line_buffering=True)

    stdin.reconfigure.assert_called_once_with(encoding="utf-8")
    stdout.reconfigure.assert_called_once_with(
        encoding="utf-8", errors="replace", line_buffering=True
    )
    stderr.reconfigure.assert_called_once_with(
        encoding="utf-8", errors="replace", line_buffering=True
    )


def test_configure_utf8_stdio_tolerates_wrapped_streams(monkeypatch):
    class WrappedStream:
        pass

    monkeypatch.setattr("shared.stdio.sys.stdin", WrappedStream())
    monkeypatch.setattr("shared.stdio.sys.stdout", WrappedStream())
    monkeypatch.setattr("shared.stdio.sys.stderr", WrappedStream())

    configure_utf8_stdio(force=True)
