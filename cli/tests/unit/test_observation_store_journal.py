from pathlib import Path

import pytest

from shared.query_registry import observation_store


def test_a_journal_file_another_connection_holds_is_not_treated_as_damaged(tmp_path, monkeypatch):
    database = tmp_path / "library.db"
    (tmp_path / "library.db-wal").write_bytes(b"\x00" * 32)
    real_open = Path.open

    def held(self, *args, **kwargs):
        if self.name.endswith("-wal"):
            raise PermissionError(13, "Permission denied", str(self))
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", held)
    observation_store._check_journal_headers(database)


def test_a_damaged_journal_still_stops_the_open(tmp_path):
    (tmp_path / "library.db-wal").write_bytes(b"junkjunk")
    with pytest.raises(Exception):
        observation_store._check_journal_headers(tmp_path / "library.db")
