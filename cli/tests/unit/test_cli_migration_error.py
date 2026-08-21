"""A failed library.db migration reads as its own message, not as a crash.

LibraryMigrationError already carries the whole explanation — which step
failed, why, and where the pre-migration backup sits. Printing a traceback
above it buries the one part the user can act on.
"""

from __future__ import annotations

import sys

import pytest

import rdst as rdst_cli
from shared.query_registry.library_store import LibraryMigrationError

MESSAGE = (
    "Migrating library.db from v11 to v12 failed at step 'add lane column'. "
    "Your data is unchanged; the backup is at ~/.rdst/library.db.v11.bak."
)


@pytest.fixture
def cli_argv(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["rdst", "query", "list"])
    monkeypatch.setattr(rdst_cli, "configure_utf8_stdio", lambda: None)


def test_migration_failure_prints_only_its_message(cli_argv, monkeypatch, capsys):
    def _fail():
        raise LibraryMigrationError(MESSAGE)

    monkeypatch.setattr(rdst_cli, "parse_arguments", _fail)

    with pytest.raises(SystemExit) as exit_info:
        rdst_cli.main()

    assert exit_info.value.code == 1
    captured = capsys.readouterr()
    assert MESSAGE in captured.err
    assert "Traceback" not in captured.err


def test_other_failures_still_print_a_traceback(cli_argv, monkeypatch, capsys):
    def _fail():
        raise RuntimeError("something unexpected")

    monkeypatch.setattr(rdst_cli, "parse_arguments", _fail)

    with pytest.raises(SystemExit) as exit_info:
        rdst_cli.main()

    assert exit_info.value.code == 1
    captured = capsys.readouterr()
    assert "Traceback" in captured.err
