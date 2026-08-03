"""Cross-platform persistence retry regressions."""

from __future__ import annotations

from pathlib import Path

import pytest
from shared import persistence


def _sharing_violation() -> PermissionError:
    error = PermissionError(13, "The process cannot access the file")
    error.winerror = 32
    return error


def test_atomic_replace_retries_windows_sharing_violation(monkeypatch):
    attempts: list[tuple[str, Path]] = []
    delays: list[float] = []

    def replace(source: str, destination: Path) -> None:
        attempts.append((source, destination))
        if len(attempts) < 4:
            raise _sharing_violation()

    monkeypatch.setattr(persistence.os, "replace", replace)
    monkeypatch.setattr(persistence.time, "sleep", delays.append)

    destination = Path("config.toml")
    persistence._replace_with_retry("config.tmp", destination)

    assert attempts == [("config.tmp", destination)] * 4
    assert delays == pytest.approx([0.05, 0.10, 0.15])


def test_atomic_replace_stops_after_bounded_retries(monkeypatch):
    attempts = 0

    def replace(_source: str, _destination: Path) -> None:
        nonlocal attempts
        attempts += 1
        raise _sharing_violation()

    monkeypatch.setattr(persistence.os, "replace", replace)
    monkeypatch.setattr(persistence.time, "sleep", lambda _delay: None)

    with pytest.raises(PermissionError) as exc_info:
        persistence._replace_with_retry("config.tmp", Path("config.toml"))

    assert exc_info.value.winerror == 32
    assert attempts == 4
