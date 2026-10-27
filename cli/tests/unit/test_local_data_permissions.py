"""~/.rdst and its SQLite stores are readable only by their owner."""

from __future__ import annotations

import os
import stat

import pytest

from shared.constants import ensure_private_data_dir, ensure_private_file

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def test_a_new_data_dir_is_owner_only(monkeypatch, tmp_path):
    data = tmp_path / "fresh" / ".rdst"
    monkeypatch.setenv("RDST_DATA_DIR", str(data))

    assert ensure_private_data_dir() == data
    assert _mode(data) == 0o700


def test_an_existing_data_dir_and_its_stores_are_tightened(monkeypatch, tmp_path):
    data = tmp_path / ".rdst"
    data.mkdir(mode=0o755)
    os.chmod(data, 0o755)
    for name in ("monitor.db", "monitor.db-wal", "library.db", "config.toml", "notes.txt"):
        (data / name).write_text("x")
        os.chmod(data / name, 0o644)
    monkeypatch.setenv("RDST_DATA_DIR", str(data))

    ensure_private_data_dir()

    assert _mode(data) == 0o700
    assert _mode(data / "monitor.db") == 0o600
    assert _mode(data / "monitor.db-wal") == 0o600
    assert _mode(data / "library.db") == 0o600
    # Only the stores: other files keep whatever mode they were written with.
    assert _mode(data / "notes.txt") == 0o644


def test_a_symlinked_store_is_left_alone(monkeypatch, tmp_path):
    data = tmp_path / ".rdst"
    data.mkdir()
    elsewhere = tmp_path / "shared.db"
    elsewhere.write_text("x")
    os.chmod(elsewhere, 0o644)
    (data / "cache.db").symlink_to(elsewhere)
    monkeypatch.setenv("RDST_DATA_DIR", str(data))

    ensure_private_data_dir()

    assert _mode(elsewhere) == 0o644


def test_a_private_file_is_created_owner_only_and_existing_ones_untouched(tmp_path):
    new = tmp_path / "new.db"
    ensure_private_file(new)
    assert _mode(new) == 0o600

    existing = tmp_path / "existing.db"
    existing.write_text("data")
    os.chmod(existing, 0o640)
    ensure_private_file(existing)
    assert existing.read_text() == "data" and _mode(existing) == 0o640


def test_stores_create_their_database_owner_only(tmp_rdst_home):
    from shared.agent_activity.store import AgentActivityStore
    from shared.monitor.monitor_store import MonitorStore, default_monitor_db_path

    MonitorStore().set_settings("shop", {"enabled": True})
    AgentActivityStore().ensure_open()

    assert _mode(default_monitor_db_path()) == 0o600
    assert _mode(tmp_rdst_home / "agent_activity.db") == 0o600
