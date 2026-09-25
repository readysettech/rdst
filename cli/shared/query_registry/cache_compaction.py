"""One-time compaction of a large cache.db from an older install.

Deleting rows never shrinks a SQLite file by itself: freed pages go on the freelist, and only
a file created with incremental auto_vacuum can hand them back a batch at a time. Returning
tens of thousands of pages that way moves each one, which is slow. A large cache.db is
compacted at startup instead: expired observation windows are deleted in short chunks, and
when the file isn't incremental or is then mostly free space, one VACUUM rewrites the small
live remainder with incremental auto_vacuum, so later pruning keeps the file small.

The work runs on a low-priority background thread. Each step is a short transaction with
retries, so collection (which writes cache.db every cycle) waits at most one chunk or the
final VACUUM of the pruned file, never the whole job.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

from shared.query_registry.observation_store import (
    StorageError,
    _connection_pragmas,
    _storage_message,
    default_cache_db_path,
)

logger = logging.getLogger(__name__)

# A file this size or larger is worth one rewrite when it isn't incremental.
LARGE_FILE_BYTES = 100 * 1024 * 1024
# Free space worth reclaiming in one VACUUM instead of page by page (or a quarter of the file).
LARGE_FREE_BYTES = 100 * 1024 * 1024
CHUNK_ROWS = 10_000
# How long a step keeps retrying while another writer holds the file.
LOCK_WAIT_S = 600.0
_AUTO_VACUUM_INCREMENTAL = 2
_started = threading.Lock()


def inspect(path: Path) -> Optional[dict[str, Any]]:
    """Size, auto_vacuum mode and free space, read from the header without writing."""
    if not path.exists():
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA busy_timeout = 1000")
        auto_vacuum, page_size, pages, free = (
            conn.execute(f"PRAGMA {name}").fetchone()[0]
            for name in ("auto_vacuum", "page_size", "page_count", "freelist_count"))
    finally:
        conn.close()
    return {"bytes": path.stat().st_size, "auto_vacuum": auto_vacuum,
            "page_size": page_size, "pages": pages, "free_pages": free}


def needs_compaction(info: Optional[dict[str, Any]]) -> bool:
    """A large file: prune it once at startup, then rewrite it if that's worth it."""
    return bool(info) and info["bytes"] >= LARGE_FILE_BYTES


def worth_vacuum(info: Optional[dict[str, Any]]) -> bool:
    """Not incremental yet, or enough free pages that one rewrite beats page-by-page."""
    if not info:
        return False
    free = info["free_pages"] * info["page_size"]
    return (info["auto_vacuum"] != _AUTO_VACUUM_INCREMENTAL or free >= LARGE_FREE_BYTES
            or info["free_pages"] * 4 >= info["pages"] > 0)


def _is_locked(exc: BaseException) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc).lower()


def _retrying(conn: sqlite3.Connection, sql: str, params: tuple = (),
              deadline: float = 0.0) -> sqlite3.Cursor:
    while True:
        try:
            return conn.execute(sql, params)
        except sqlite3.OperationalError as exc:
            if not _is_locked(exc) or time.monotonic() > deadline:
                raise
            time.sleep(1.0)


def _lower_thread_priority() -> None:
    try:
        if sys.platform.startswith("linux"):
            os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 19)
        elif sys.platform == "win32":
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            kernel32.SetThreadPriority(kernel32.GetCurrentThread(), -2)  # LOWEST
    except Exception:
        logger.debug("Couldn't lower the compaction thread's priority", exc_info=True)


def compact(path: Path, *, keep_seconds: float, now: Optional[float] = None,
            chunk: int = CHUNK_ROWS, lock_wait_s: float = LOCK_WAIT_S) -> dict[str, Any]:
    """Delete observation windows older than `keep_seconds`, then VACUUM to incremental."""
    before = inspect(path)
    started = time.monotonic()
    deadline = started + lock_wait_s
    cutoff = (time.time() if now is None else now) - keep_seconds
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    deleted, vacuumed = 0, False
    try:
        for pragma in _connection_pragmas():
            conn.execute(pragma)
        # Short waits per attempt; _retrying owns the longer wait between attempts.
        conn.execute("PRAGMA busy_timeout = 250")
        while True:
            _retrying(conn, "BEGIN IMMEDIATE", deadline=deadline)
            try:
                batch = conn.execute(
                    "DELETE FROM recent_observation WHERE (target_id, normalized_hash, "
                    "window_end) IN (SELECT target_id, normalized_hash, window_end FROM "
                    "recent_observation WHERE window_end < ? LIMIT ?)", (cutoff, chunk)).rowcount
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            deleted += batch
            if batch < chunk:
                break
        vacuumed = worth_vacuum(inspect(path))
        if vacuumed:
            _retrying(conn, f"PRAGMA auto_vacuum = {_AUTO_VACUUM_INCREMENTAL}", deadline=deadline)
            _retrying(conn, "VACUUM", deadline=deadline)
    except (sqlite3.DatabaseError, OSError) as exc:
        message = _storage_message(path, exc)
        if message:
            raise StorageError(message) from exc
        raise
    finally:
        conn.close()
    after = inspect(path)
    result = {"deleted_rows": deleted, "vacuumed": vacuumed, "seconds": round(time.monotonic() - started, 2),
              "bytes_before": before["bytes"] if before else 0,
              "bytes_after": after["bytes"] if after else 0}
    logger.info("Compacted %s: %s", path, result)
    return result


def start_background_compaction(path: Optional[Path] = None, *,
                                keep_seconds: float) -> Optional[threading.Thread]:
    """Start the one-time compaction when cache.db needs it; returns the thread, if any.

    The check reads three header values, so startup never waits on the file's size.
    `RDST_CACHE_COMPACTION=0` turns it off.
    """
    if os.environ.get("RDST_CACHE_COMPACTION", "").strip() == "0":
        return None
    path = Path(path or default_cache_db_path())
    try:
        info = inspect(path)
    except sqlite3.Error:
        logger.debug("cache.db header unreadable; skipping compaction", exc_info=True)
        return None
    if not needs_compaction(info) or not _started.acquire(blocking=False):
        return None

    def run() -> None:
        _lower_thread_priority()
        try:
            compact(path, keep_seconds=keep_seconds)
        except Exception:
            logger.warning("cache.db compaction stopped; it retries on the next start",
                           exc_info=True)
        finally:
            _started.release()

    thread = threading.Thread(target=run, name="rdst-cache-compaction", daemon=True)
    thread.start()
    return thread
