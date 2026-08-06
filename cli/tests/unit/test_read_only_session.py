"""Tests for read-only session enforcement.

The per-target ``read_only`` flag was parsed and never applied, so it advertised
a guarantee nothing delivered. This is the only control in the SQL path the
database itself enforces; everything else inspects statement text.

The behaviour was verified against live PostgreSQL 15 and MySQL 8.0 containers:
an INSERT is refused with ``ReadOnlySqlTransaction`` and error 1792
respectively, while SELECT continues to work. These tests cover the wiring.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from shared.db_connection import apply_read_only_session


class TestApplyReadOnlySession:
    def test_postgres_uses_the_psycopg2_session_api(self):
        conn = MagicMock()

        apply_read_only_session(conn, "postgresql")

        conn.set_session.assert_called_once_with(readonly=True)

    def test_mysql_issues_a_read_only_session_statement(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value

        apply_read_only_session(conn, "mysql")

        cursor.execute.assert_called_once_with("SET SESSION TRANSACTION READ ONLY")

    @pytest.mark.parametrize("engine", ["sqlite", "", "readyset"])
    def test_unknown_engines_are_left_alone(self, engine):
        """An engine we cannot put into read-only mode must not be silently
        treated as though we had."""
        conn = MagicMock()

        apply_read_only_session(conn, engine)

        conn.set_session.assert_not_called()
        conn.cursor.assert_not_called()


class TestKnownHostsFile:
    """The SSH tunnel must be able to record a host key, not just read one.

    ``load_system_host_keys()`` reads ~/.ssh/known_hosts but does not set a
    filename, and paramiko's AutoAddPolicy only persists a newly seen key when
    one is set. Without it the first sight of a jump host is accepted and then
    forgotten, so a key that changes later cannot be detected.

    Verified against a live sshd container: with the file loaded, the first
    connect records 99 bytes and a subsequent connect to a server with a
    regenerated host key raises BadHostKeyException. Without it, nothing is
    recorded.
    """

    def test_creates_the_file_when_absent(self, tmp_path, monkeypatch):
        from shared.ssh_tunnel import _ensure_known_hosts_file

        monkeypatch.setenv("HOME", str(tmp_path))
        path = _ensure_known_hosts_file()

        assert Path(path) == tmp_path / ".ssh" / "known_hosts"
        assert Path(path).exists()

    def test_the_file_is_private(self, tmp_path, monkeypatch):
        from shared.ssh_tunnel import _ensure_known_hosts_file

        monkeypatch.setenv("HOME", str(tmp_path))
        path = _ensure_known_hosts_file()

        assert oct(Path(path).stat().st_mode & 0o777) == "0o600"

    def test_an_existing_file_is_left_alone(self, tmp_path, monkeypatch):
        from shared.ssh_tunnel import _ensure_known_hosts_file

        monkeypatch.setenv("HOME", str(tmp_path))
        ssh_dir = tmp_path / ".ssh"
        ssh_dir.mkdir()
        existing = ssh_dir / "known_hosts"
        existing.write_text("example.com ssh-ed25519 AAAA\n", encoding="utf-8")

        _ensure_known_hosts_file()

        assert existing.read_text(encoding="utf-8") == "example.com ssh-ed25519 AAAA\n"
