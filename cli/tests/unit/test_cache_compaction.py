"""One-time compaction of a large cache.db (UPGRADE_NOTES.md, "Disk and time")."""

from __future__ import annotations

import sqlite3
import threading
import time

import pytest

from shared.query_registry import cache_compaction
from shared.query_registry.observation_store import ObservationStore

NOW = 1_790_000_000


def _cache(path, *, rows: int, recent: int, auto_vacuum: int = 0, now: float = NOW):
    ObservationStore(path).close()
    conn = sqlite3.connect(path)
    conn.executemany(
        "INSERT INTO recent_observation VALUES (?,?,?,?,1,1.0,0.1,'interval','complete',"
        "'production_only')",
        [("t", f"h{i % 50}", now - 7200 - i * 60 - 60, now - 7200 - i * 60) for i in range(rows)]
        + [("t", f"r{i}", now - 120, now - 60) for i in range(recent)])
    conn.commit()
    conn.execute(f"PRAGMA auto_vacuum = {auto_vacuum}")
    conn.execute("VACUUM")
    conn.close()


def test_small_or_disabled_files_are_left_alone(tmp_path, monkeypatch):
    path = tmp_path / "cache.db"
    _cache(path, rows=100, recent=1)
    assert cache_compaction.start_background_compaction(path, keep_seconds=3600) is None
    monkeypatch.setattr(cache_compaction, "LARGE_FILE_BYTES", 1)
    monkeypatch.setenv("RDST_CACHE_COMPACTION", "0")
    assert cache_compaction.start_background_compaction(path, keep_seconds=3600) is None
    assert cache_compaction.start_background_compaction(tmp_path / "absent.db",
                                                        keep_seconds=3600) is None


def test_prunes_expired_windows_and_converts_to_incremental(tmp_path, monkeypatch):
    path = tmp_path / "cache.db"
    _cache(path, rows=30_000, recent=5, now=time.time())
    before = path.stat().st_size
    monkeypatch.setattr(cache_compaction, "LARGE_FILE_BYTES", 1)
    thread = cache_compaction.start_background_compaction(path, keep_seconds=3600)
    assert thread is not None
    thread.join(60)
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA auto_vacuum").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM recent_observation").fetchone()[0] == 5
    conn.close()
    assert path.stat().st_size < before / 4


def test_writers_wait_at_most_a_chunk(tmp_path):
    path = tmp_path / "cache.db"
    _cache(path, rows=60_000, recent=0, auto_vacuum=2)
    # Another writer holds the file when compaction starts; compaction waits for it.
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    done: dict = {}
    worker = threading.Thread(target=lambda: done.update(
        cache_compaction.compact(path, keep_seconds=3600, now=NOW, chunk=2000)))
    worker.start()
    time.sleep(1.5)
    holder.execute("COMMIT")
    waits = []
    while worker.is_alive():
        started = time.monotonic()
        holder.execute("PRAGMA busy_timeout = 5000")
        holder.execute("INSERT OR REPLACE INTO collector_state (target_id, state) VALUES ('t', 'idle')")
        waits.append(time.monotonic() - started)
        time.sleep(0.01)
    worker.join()
    holder.close()
    assert done["deleted_rows"] == 60_000 and done["vacuumed"]
    assert max(waits, default=0) < 2


def test_newer_or_damaged_header_is_skipped(tmp_path, monkeypatch):
    path = tmp_path / "cache.db"
    path.write_bytes(b"not a database" * 100)
    monkeypatch.setattr(cache_compaction, "LARGE_FILE_BYTES", 1)
    assert cache_compaction.start_background_compaction(path, keep_seconds=3600) is None
    with pytest.raises(sqlite3.DatabaseError):
        cache_compaction.inspect(path)
