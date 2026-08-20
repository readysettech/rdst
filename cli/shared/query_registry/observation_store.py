"""Durable observation store (``cache.db``) for the query-observation lane.

SQLite-backed store for derived observation data: counter snapshots,
recent observations, identity aliases, the observation event log,
collector state, collector leases, and RDST self-execution records.
Everything in this file is *derived* from a live database and therefore
recoverable by deletion and re-observation; user-meaningful library data
lives elsewhere and is out of scope for this module.

Design (see docs/architecture/rdst-query-observation-deep-research.md, Q4/Q5):

- WAL mode so a long-lived server and short-lived CLI processes can read
  and write the same file concurrently. Runtimes whose SQLite lacks the
  multi-process WAL-reset fix use the classic DELETE rollback journal
  instead (see ``_journal_mode_for_runtime``): identical correctness,
  writers just block readers.
- Exactly one logical writer per process: every write runs under an
  in-process ``threading.Lock`` and a ``BEGIN IMMEDIATE`` transaction, so
  lock contention surfaces at ``BEGIN`` where ``busy_timeout`` applies.
- Reads use plain short-lived connections in autocommit mode.
- Hand-rolled ``PRAGMA user_version`` migration ladder; a file whose
  version is newer than this build understands, or that fails
  ``quick_check``, is renamed aside and rebuilt from scratch.
- Observation-window timestamps are REAL unix seconds so counter snapshots
  and execution evidence retain sub-second ordering (``duration_ms`` remains
  integer milliseconds). Callers always pass time explicitly; no SQL default
  or hidden ``datetime.now()`` supplies it, so tests stay clock-stable.

Multi-process caveats honoured here: the database path is canonicalized
with ``os.path.realpath`` so every process agrees on one set of journal
files. Known network filesystems (where WAL's shared memory does not work)
are refused on Windows, Linux, macOS, and BSD before SQLite opens the file.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional

import shared.constants as shared_constants

logger = logging.getLogger(__name__)

__all__ = [
    "ExecutionEvidenceWriter",
    "LeaseLostError",
    "OPEN_EXECUTION_MAX_AGE_SECONDS",
    "ObservationStore",
    "SCHEMA_VERSION",
    "default_cache_db_path",
    "record_execution_evidence",
]

SCHEMA_VERSION = 2

# An rdst_execution row with NULL ended_at means the lane never recorded its
# end (for example, its closer thread died with the process). No legitimate
# single execution runs longer than this bound, so overlap reads treat such
# a row as ended at started_at plus the bound and retention closes it there;
# otherwise one crash would mark every future window as containing RDST
# traffic forever.
OPEN_EXECUTION_MAX_AGE_SECONDS = 3600


class LeaseLostError(RuntimeError):
    """A fenced write was rejected: the caller's lease is no longer current.

    Raised inside the write transaction before anything is written, per the
    research (Q5): the store takes an active role in checking tokens and
    rejects any write whose token has gone backwards or whose lease expired.
    """

# Feature floors (see research Q4): RETURNING needs 3.35 and is a hard
# requirement; STRICT tables need 3.37 and degrade gracefully; the WAL-reset
# multi-process corruption fix landed in 3.51.3 with backports to 3.50.7 and
# 3.44.6.
_MIN_SQLITE = (3, 35, 0)
_STRICT_SQLITE = (3, 37, 0)

# Bound on waiting for a journal-mode switch on an existing file. Switching
# into or out of WAL needs the file to have no other connections; contention
# means a process on a different journal strategy still has it open.
_JOURNAL_SWITCH_TIMEOUT_SECONDS = 5.0


def _connection_pragmas() -> tuple[str, ...]:
    """Per-connection pragmas, tuned to the process's journal strategy.

    ``synchronous = NORMAL`` is corruption-safe only under WAL; the rollback
    journal needs FULL for the same power-loss guarantee.
    ``journal_size_limit`` bounds a persistent WAL between checkpoints and
    is inert for DELETE journals, which are removed at commit.
    """
    wal = _journal_mode_for_runtime() == "wal"
    return (
        "PRAGMA busy_timeout = 5000",
        f"PRAGMA synchronous = {'NORMAL' if wal else 'FULL'}",
        "PRAGMA foreign_keys = ON",
        "PRAGMA temp_store = MEMORY",
        "PRAGMA cache_size = -65536",
        "PRAGMA journal_size_limit = 33554432",
    )

_LINUX_NETWORK_FSTYPES = frozenset(
    {
        "nfs",
        "nfs4",
        "cifs",
        "smbfs",
        "smb3",
        "sshfs",
        "fuse.sshfs",
        "9p",
        "afs",
        "ncpfs",
    }
)
_BSD_NETWORK_FSTYPES = frozenset(
    {
        "afpfs",
        "cifs",
        "fusefs.sshfs",
        "nfs",
        "smbfs",
        "webdav",
    }
)

_MOUNT_ESCAPE_RE = re.compile(r"\\([0-7]{3})")


def _decode_mount_path(value: str) -> str:
    r"""Decode the octal escapes used by /proc/mounts (eg ``\040``)."""
    return _MOUNT_ESCAPE_RE.sub(lambda match: chr(int(match.group(1), 8)), value)


_SNAPSHOT_COLUMNS = (
    "engine_key",
    "calls",
    "rows",
    "total_exec_time",
    "mean_exec_time",
    "min_exec_time",
    "max_exec_time",
    "stats_since",
)


def default_cache_db_path() -> Path:
    """Return the default location of cache.db, beside queries.toml."""
    return shared_constants.rdst_data_dir() / "cache.db"


def _wal_reset_fixed(version: tuple[int, ...]) -> bool:
    """True if this SQLite carries the WAL-reset fix (3.51.3, or backports)."""
    return (
        version >= (3, 51, 3)
        or (3, 50, 7) <= version < (3, 51, 0)
        or (3, 44, 6) <= version < (3, 45, 0)
    )


_UNSAFE_SQLITE_OK_ENV = "RDST_UNSAFE_SQLITE_OK"

# Each journal-strategy warning fires once per process, not once per store.
_unsafe_wal_warned = False
_delete_fallback_warned = False
_journal_adoption_warned = False


def _unsafe_sqlite_override() -> bool:
    """True when RDST_UNSAFE_SQLITE_OK opts in to WAL on an unfixed runtime."""
    return os.environ.get(_UNSAFE_SQLITE_OK_ENV, "").strip().lower() in ("1", "true")


def _check_sqlite_runtime() -> bool:
    """Enforce the SQLite feature floor; return STRICT-table availability.

    RETURNING (3.35) is a hard requirement with no override. WAL safety is
    handled separately: :func:`_journal_mode_for_runtime` degrades unfixed
    runtimes to the DELETE rollback journal instead of failing.
    """
    version = sqlite3.sqlite_version_info
    if version < _MIN_SQLITE:
        raise RuntimeError(
            f"SQLite {sqlite3.sqlite_version} is too old; "
            f"RDST requires >= 3.35 (RETURNING support)"
        )
    return version >= _STRICT_SQLITE


def _journal_mode_for_runtime() -> str:
    """Return this process's journal strategy: ``"wal"`` or ``"delete"``.

    Runtimes carrying the multi-process WAL-reset fix (3.51.3, or the
    3.50.7 / 3.44.6 backports) use WAL. The corruption bug is specific to
    the WAL journal, so unfixed runtimes fall back to the classic DELETE
    rollback journal: ``BEGIN IMMEDIATE`` plus ``busy_timeout`` still
    serialize multi-process access correctly, at the cost of writers
    blocking readers. Setting RDST_UNSAFE_SQLITE_OK=1 (or "true") instead
    forces WAL on an unfixed runtime, accepting the corruption risk (for
    controlled environments such as single-process test suites). Either
    degraded path warns once per process.
    """
    if _wal_reset_fixed(sqlite3.sqlite_version_info):
        return "wal"
    if _unsafe_sqlite_override():
        global _unsafe_wal_warned
        if not _unsafe_wal_warned:
            _unsafe_wal_warned = True
            logger.warning(
                "SQLite %s lacks the multi-process WAL-reset fix; %s is set, "
                "so continuing in WAL mode with the multi-process WAL "
                "corruption risk accepted",
                sqlite3.sqlite_version,
                _UNSAFE_SQLITE_OK_ENV,
            )
        return "wal"
    global _delete_fallback_warned
    if not _delete_fallback_warned:
        _delete_fallback_warned = True
        logger.warning(
            "SQLite %s lacks the multi-process WAL-reset fix; RDST stores "
            "are using the rollback-journal (DELETE) mode, which is safe "
            "but serializes readers behind writers. Use a Python whose "
            "SQLite is >= 3.51.3 (or backport 3.50.7 / 3.44.6) to restore "
            "WAL",
            sqlite3.sqlite_version,
        )
    return "delete"


def _apply_journal_mode(conn: sqlite3.Connection, path: Path) -> None:
    """Move the database at ``path`` to this runtime's journal mode.

    WAL is the only persistent journal mode, so the file needs a switch
    exactly when it is in WAL and the strategy is DELETE, or vice versa.
    Switching requires that no other connection has the file open; SQLite
    reports contention as SQLITE_BUSY, which is retried briefly. If another
    live process keeps the file open in the other mode past the deadline,
    this connection adopts the file's current mode instead of failing:
    refusing would not change the mode the other process keeps using, it
    would only make this process unable to read or record anything (for
    example RDST self-execution evidence from CLI lanes while ``rdst web``
    holds cache.db). Adoption is announced once per process; when a runtime
    without the WAL-reset fix adopts WAL this accepts the multi-process WAL
    corruption risk the DELETE fallback normally avoids, so the warning asks
    for the processes' SQLite versions to be aligned.
    """
    desired = _journal_mode_for_runtime()
    current = conn.execute("PRAGMA journal_mode").fetchone()[0]
    if current == desired or (current != "wal" and desired != "wal"):
        return
    # Short busy_timeout keeps each switch attempt quick; the loop owns the
    # overall deadline. The steady-state timeout is restored afterwards.
    conn.execute("PRAGMA busy_timeout = 100")
    try:
        deadline = time.monotonic() + _JOURNAL_SWITCH_TIMEOUT_SECONDS
        while True:
            try:
                actual = conn.execute(
                    f"PRAGMA journal_mode = {desired}"
                ).fetchone()[0]
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                if "lock" not in message and "busy" not in message:
                    raise
                actual = current
            if actual == desired:
                return
            if time.monotonic() >= deadline:
                _adopt_contended_journal_mode(conn, path, current, desired)
                return
            time.sleep(0.05)
    finally:
        conn.execute("PRAGMA busy_timeout = 5000")


def _adopt_contended_journal_mode(
    conn: sqlite3.Connection, path: Path, current: str, desired: str
) -> None:
    """Keep the file's journal mode when another process blocks the switch."""
    if current != "wal":
        # This runtime assumed WAL and set synchronous=NORMAL, which is only
        # corruption-safe under WAL; the rollback journal needs FULL.
        conn.execute("PRAGMA synchronous = FULL")
    global _journal_adoption_warned
    if not _journal_adoption_warned:
        _journal_adoption_warned = True
        risk = (
            " This runtime lacks the multi-process WAL-reset fix, so sharing"
            " a WAL database across processes carries a corruption risk."
            if current == "wal" and not _wal_reset_fixed(sqlite3.sqlite_version_info)
            else ""
        )
        logger.warning(
            "could not switch %s to journal_mode=%s: another process still "
            "has the database open in %s mode; continuing in %s mode.%s "
            "Align rdst processes' SQLite versions to avoid mixed journal "
            "strategies",
            path,
            desired,
            current,
            current,
            risk,
        )


def _refuse_network_path(path: str) -> None:
    """Refuse a store path known to live on a network filesystem.

    WAL requires all processes to share a small amount of memory and does
    not work over network filesystems. Detection covers UNC and mapped drives
    on Windows, mount tables on Linux, and ``statfs`` output on macOS/BSD.
    Detection failures do not guess; a positively identified network path is
    always rejected before SQLite creates or opens the file.
    """
    if os.name == "nt":
        is_network = path.startswith("\\\\")
        if not is_network:
            try:
                import ctypes

                drive = os.path.splitdrive(os.path.abspath(path))[0]
                if drive:
                    # DRIVE_REMOTE from WinBase.h.
                    is_network = ctypes.windll.kernel32.GetDriveTypeW(drive + "\\") == 4
            except (AttributeError, OSError):
                pass
        if is_network:
            raise RuntimeError(
                f"SQLite WAL store {path} is on a Windows network path. "
                "Move RDST_DATA_DIR to a local disk (for example LOCALAPPDATA)."
            )
        return
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/mounts", "r", encoding="utf-8") as handle:
                mounts = [line.split() for line in handle]
        except OSError:
            return
        best_match = ""
        best_fstype = ""
        for parts in mounts:
            if len(parts) < 3:
                continue
            mount_point, fstype = _decode_mount_path(parts[1]), parts[2]
            if path.startswith(mount_point.rstrip("/") + "/") or path == mount_point:
                if len(mount_point) > len(best_match):
                    best_match, best_fstype = mount_point, fstype
        if best_fstype in _LINUX_NETWORK_FSTYPES:
            raise RuntimeError(
                f"SQLite WAL store {path} is on a {best_fstype} network "
                "filesystem. Move RDST_DATA_DIR to a local filesystem."
            )
        return
    if sys.platform == "darwin" or "bsd" in sys.platform:
        probe = Path(path)
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        try:
            result = subprocess.run(
                ["stat", "-f", "%T", str(probe)],
                check=True,
                capture_output=True,
                text=True,
                timeout=2,
            )
        except (OSError, subprocess.SubprocessError):
            return
        fstype = result.stdout.strip().lower()
        if fstype in _BSD_NETWORK_FSTYPES:
            raise RuntimeError(
                f"SQLite WAL store {path} is on a {fstype} network "
                "filesystem. Move RDST_DATA_DIR to a local filesystem."
            )


def _schema_v1(strict: bool) -> tuple[str, ...]:
    """Initial cache.db schema. STRICT is dropped when the runtime lacks it."""
    s = " STRICT," if strict else ""
    s_only = " STRICT" if strict else ""
    return (
        f"""
        CREATE TABLE identity_alias (
          target_id TEXT NOT NULL,
          engine_key TEXT NOT NULL,
          normalized_hash TEXT NOT NULL,
          epoch_id TEXT NOT NULL,
          PRIMARY KEY (target_id, engine_key, epoch_id)
        ){s} WITHOUT ROWID""",
        f"""
        CREATE TABLE counter_snapshot (
          target_id TEXT NOT NULL,
          engine_key TEXT NOT NULL,
          epoch_id TEXT NOT NULL,
          captured_at INTEGER NOT NULL,
          calls INTEGER,
          rows INTEGER,
          total_exec_time REAL,
          mean_exec_time REAL,
          min_exec_time REAL,
          max_exec_time REAL,
          stats_since INTEGER,
          PRIMARY KEY (target_id, engine_key, captured_at)
        ){s} WITHOUT ROWID""",
        "CREATE INDEX cs_captured ON counter_snapshot(captured_at)",
        f"""
        CREATE TABLE recent_observation (
          target_id TEXT NOT NULL,
          normalized_hash TEXT NOT NULL,
          window_start INTEGER NOT NULL,
          window_end INTEGER NOT NULL,
          calls_delta INTEGER,
          exec_time_delta REAL,
          approximate_qps REAL,
          freshness TEXT NOT NULL,
          completeness TEXT NOT NULL,
          attribution TEXT NOT NULL,
          PRIMARY KEY (target_id, normalized_hash, window_end)
        ){s} WITHOUT ROWID""",
        f"""
        CREATE TABLE observation_event (
          seq INTEGER PRIMARY KEY AUTOINCREMENT,
          target_id TEXT NOT NULL,
          kind TEXT NOT NULL,
          payload TEXT NOT NULL,
          created_at INTEGER NOT NULL
        ){s_only}""",
        "CREATE INDEX oe_target ON observation_event(target_id, seq)",
        f"""
        CREATE TABLE collector_state (
          target_id TEXT PRIMARY KEY,
          state TEXT NOT NULL,
          last_attempt_at INTEGER,
          last_success_at INTEGER,
          duration_ms INTEGER,
          next_due_at INTEGER,
          error_code TEXT,
          source_capabilities TEXT,
          epoch_id TEXT
        ){s} WITHOUT ROWID""",
        f"""
        CREATE TABLE collector_lease (
          target_id TEXT PRIMARY KEY,
          owner_id TEXT NOT NULL,
          fencing_token INTEGER NOT NULL,
          expires_at INTEGER NOT NULL
        ){s} WITHOUT ROWID""",
        f"""
        CREATE TABLE rdst_execution (
          target_id TEXT NOT NULL,
          normalized_hash TEXT NOT NULL,
          lane TEXT NOT NULL,
          run_id TEXT NOT NULL,
          started_at INTEGER NOT NULL,
          ended_at INTEGER,
          exec_count INTEGER,
          PRIMARY KEY (target_id, run_id, normalized_hash)
        ){s} WITHOUT ROWID""",
        "CREATE INDEX re_window ON rdst_execution(target_id, normalized_hash, started_at)",
    )


# Migration ladder: version N maps to the DDL that brings a version N-1 file
# to version N. Each entry takes the STRICT-availability flag; later versions
# append literal DDL steps here.
_MIGRATIONS: Dict[int, Any] = {
    1: _schema_v1,
    2: lambda strict: _schema_v2(strict),
}


def _schema_v2(strict: bool) -> tuple[str, ...]:
    """Preserve sub-second attribution timestamps in the three window tables."""
    s = " STRICT," if strict else ""
    return (
        "ALTER TABLE counter_snapshot RENAME TO counter_snapshot_v1",
        f"""
        CREATE TABLE counter_snapshot (
          target_id TEXT NOT NULL,
          engine_key TEXT NOT NULL,
          epoch_id TEXT NOT NULL,
          captured_at REAL NOT NULL,
          calls INTEGER,
          rows INTEGER,
          total_exec_time REAL,
          mean_exec_time REAL,
          min_exec_time REAL,
          max_exec_time REAL,
          stats_since INTEGER,
          PRIMARY KEY (target_id, engine_key, captured_at)
        ){s} WITHOUT ROWID""",
        """
        INSERT INTO counter_snapshot
        SELECT target_id, engine_key, epoch_id, CAST(captured_at AS REAL),
               calls, rows, total_exec_time, mean_exec_time,
               min_exec_time, max_exec_time, stats_since
        FROM counter_snapshot_v1
        """,
        "DROP TABLE counter_snapshot_v1",
        "CREATE INDEX cs_captured ON counter_snapshot(captured_at)",
        "ALTER TABLE recent_observation RENAME TO recent_observation_v1",
        f"""
        CREATE TABLE recent_observation (
          target_id TEXT NOT NULL,
          normalized_hash TEXT NOT NULL,
          window_start REAL NOT NULL,
          window_end REAL NOT NULL,
          calls_delta INTEGER,
          exec_time_delta REAL,
          approximate_qps REAL,
          freshness TEXT NOT NULL,
          completeness TEXT NOT NULL,
          attribution TEXT NOT NULL,
          PRIMARY KEY (target_id, normalized_hash, window_end)
        ){s} WITHOUT ROWID""",
        """
        INSERT INTO recent_observation
        SELECT target_id, normalized_hash, CAST(window_start AS REAL),
               CAST(window_end AS REAL), calls_delta, exec_time_delta,
               approximate_qps, freshness, completeness, attribution
        FROM recent_observation_v1
        """,
        "DROP TABLE recent_observation_v1",
        "ALTER TABLE rdst_execution RENAME TO rdst_execution_v1",
        f"""
        CREATE TABLE rdst_execution (
          target_id TEXT NOT NULL,
          normalized_hash TEXT NOT NULL,
          lane TEXT NOT NULL,
          run_id TEXT NOT NULL,
          started_at REAL NOT NULL,
          ended_at REAL,
          exec_count INTEGER,
          PRIMARY KEY (target_id, run_id, normalized_hash)
        ){s} WITHOUT ROWID""",
        """
        INSERT INTO rdst_execution
        SELECT target_id, normalized_hash, lane, run_id,
               CAST(started_at AS REAL), CAST(ended_at AS REAL), exec_count
        FROM rdst_execution_v1
        WHERE exec_count IS NULL OR exec_count > 0
        """,
        "DROP TABLE rdst_execution_v1",
        "CREATE INDEX re_window ON rdst_execution(target_id, normalized_hash, started_at)",
    )


class ObservationStore:
    """Typed access to cache.db. One instance per process is expected.

    All write methods serialize on an in-process lock and run inside a
    ``BEGIN IMMEDIATE`` transaction. Window timestamps are unix seconds with
    sub-second precision
    supplied by the caller.
    """

    def __init__(self, path: Optional[Path | str] = None) -> None:
        raw = Path(path) if path is not None else default_cache_db_path()
        # Canonicalize so every process sharing this file agrees on one
        # path and therefore one set of -wal/-shm journal files.
        self._path = Path(os.path.realpath(raw))
        self._strict = _check_sqlite_runtime()
        _refuse_network_path(str(self._path))
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.Lock()
        self._write_conn: Optional[sqlite3.Connection] = None
        self._open()

    @property
    def path(self) -> Path:
        return self._path

    # -- lifecycle ----------------------------------------------------------

    def _open(self) -> None:
        conn = self._connect()
        try:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                # Fresh file: auto_vacuum is a one-shot decision that must be
                # made before any CREATE TABLE, then the journal strategy.
                conn.execute("PRAGMA auto_vacuum = INCREMENTAL")
                _apply_journal_mode(conn, self._path)
            elif version > SCHEMA_VERSION:
                logger.warning(
                    "cache.db user_version %d is newer than this build "
                    "understands (%d); rebuilding from scratch",
                    version,
                    SCHEMA_VERSION,
                )
                conn.close()
                self._rebuild()
                return
            else:
                # An existing file follows this runtime's journal strategy,
                # in both directions (WAL upgrade and DELETE fallback).
                _apply_journal_mode(conn, self._path)
                check = conn.execute("PRAGMA quick_check").fetchone()[0]
                if check != "ok":
                    logger.warning(
                        "cache.db failed quick_check (%s); rebuilding from "
                        "scratch",
                        check,
                    )
                    conn.close()
                    self._rebuild()
                    return
            self._migrate(conn, version)
        except BaseException:
            conn.close()
            raise
        self._write_conn = conn

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self._path, isolation_level=None, check_same_thread=False
        )
        for pragma in _connection_pragmas():
            conn.execute(pragma)
        return conn

    def _migrate(self, conn: sqlite3.Connection, from_version: int) -> None:
        for version in range(from_version + 1, SCHEMA_VERSION + 1):
            conn.execute("BEGIN IMMEDIATE")
            try:
                for statement in _MIGRATIONS[version](self._strict):
                    conn.execute(statement)
                # PRAGMA user_version does not accept bound parameters; the
                # value is an int from our own ladder.
                conn.execute(f"PRAGMA user_version = {version:d}")
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise

    def _rebuild(self) -> None:
        """Rename the file (and any journal remnants) aside, then recreate."""
        stamp = int(time.time())
        aside = self._path.with_name(f"{self._path.name}.corrupt-{stamp}")
        os.replace(self._path, aside)
        for suffix in ("-wal", "-shm"):
            leftover = Path(str(self._path) + suffix)
            if leftover.exists():
                os.replace(leftover, Path(str(aside) + suffix))
        logger.warning("previous cache.db kept at %s for bug reports", aside)
        self._open()

    def recreate_from_scratch(self) -> None:
        """Discard the current file and rebuild an empty, current-version store.

        cache.db holds only derived data, so this is a supported recovery
        path: the old file is renamed aside (never deleted) and a fresh
        store is created in place.
        """
        with self._write_lock:
            if self._write_conn is not None:
                self._write_conn.close()
                self._write_conn = None
            self._rebuild()

    def close(self) -> None:
        with self._write_lock:
            if self._write_conn is not None:
                self._write_conn.close()
                self._write_conn = None

    def __enter__(self) -> "ObservationStore":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    # -- connection plumbing ------------------------------------------------

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        """Serialized write transaction: in-process lock + BEGIN IMMEDIATE."""
        with self._write_lock:
            conn = self._write_conn
            if conn is None:
                raise RuntimeError("ObservationStore is closed")
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        """Plain short-lived autocommit connection for reads."""
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _apply_fence(
        self,
        conn: sqlite3.Connection,
        target_id: str,
        owner_id: Optional[str],
        fencing_token: Optional[int],
        now: Optional[int],
    ) -> None:
        """Enforce the write-time fence when fencing parameters are provided.

        A fenced write supplies owner_id, fencing_token, and now together.
        Inside the caller's transaction, the collector_lease row for the
        target must name this owner, carry exactly this token, and still be
        live; any mismatch raises LeaseLostError before anything is written.
        With all three omitted the write is unfenced and unchanged.
        """
        if owner_id is None and fencing_token is None and now is None:
            return
        if owner_id is None or fencing_token is None or now is None:
            raise ValueError(
                "fenced writes require owner_id, fencing_token, and now together"
            )
        row = conn.execute(
            "SELECT owner_id, fencing_token, expires_at FROM collector_lease"
            " WHERE target_id = ?",
            (target_id,),
        ).fetchone()
        if row is None or row[0] != owner_id or row[1] != fencing_token or row[2] <= now:
            raise LeaseLostError(
                f"lease on target {target_id!r} is not held by {owner_id!r} "
                f"with live token {fencing_token}"
            )

    @contextmanager
    def write_fence(
        self,
        target_id: str,
        *,
        owner_id: str,
        fencing_token: int,
        now: int,
    ) -> Iterator[None]:
        """Validate and hold a lease while an external protected write commits.

        Keeping cache.db's write transaction open prevents a lease takeover
        from committing between this token check and the caller's library.db
        commit. This is the cross-file fencing primitive used by discovery's
        QueryRegistry writes.
        """
        with self._write() as conn:
            self._apply_fence(conn, target_id, owner_id, fencing_token, now)
            yield

    # -- counter snapshots --------------------------------------------------

    def record_counter_snapshots(
        self,
        target_id: str,
        epoch_id: str,
        rows: Iterable[Mapping[str, Any]],
        captured_at: float,
        *,
        owner_id: Optional[str] = None,
        fencing_token: Optional[int] = None,
        now: Optional[int] = None,
    ) -> int:
        """Persist one collection cycle's snapshots as a single transaction.

        Each row maps engine_key plus the counter columns (calls, rows,
        total_exec_time, mean_exec_time, min_exec_time, max_exec_time,
        stats_since; missing counters default to NULL). Idempotent for a
        retried batch: conflicting (target_id, engine_key, captured_at)
        rows are updated in place. Returns the number of rows written.
        """
        params = [
            {
                "target_id": target_id,
                "epoch_id": epoch_id,
                "captured_at": captured_at,
                **{column: row.get(column) for column in _SNAPSHOT_COLUMNS},
            }
            for row in rows
        ]
        if not params:
            return 0
        with self._write() as conn:
            self._apply_fence(conn, target_id, owner_id, fencing_token, now)
            conn.executemany(
                """
                INSERT INTO counter_snapshot (
                  target_id, engine_key, epoch_id, captured_at,
                  calls, rows, total_exec_time, mean_exec_time,
                  min_exec_time, max_exec_time, stats_since
                ) VALUES (
                  :target_id, :engine_key, :epoch_id, :captured_at,
                  :calls, :rows, :total_exec_time, :mean_exec_time,
                  :min_exec_time, :max_exec_time, :stats_since
                )
                ON CONFLICT(target_id, engine_key, captured_at) DO UPDATE SET
                  epoch_id = excluded.epoch_id,
                  calls = excluded.calls,
                  rows = excluded.rows,
                  total_exec_time = excluded.total_exec_time,
                  mean_exec_time = excluded.mean_exec_time,
                  min_exec_time = excluded.min_exec_time,
                  max_exec_time = excluded.max_exec_time,
                  stats_since = excluded.stats_since
                """,
                params,
            )
        return len(params)

    def latest_counter_snapshots(
        self, target_id: str, epoch_id: str
    ) -> List[Dict[str, Any]]:
        """Return the latest stored snapshot per engine_key within one epoch.

        Rows carry the counter columns plus captured_at, so a restarted
        collector can rebuild the delta baseline for the epoch it persisted
        instead of re-baselining and losing the restart-gap window.
        """
        with self._read() as conn:
            rows = conn.execute(
                """
                SELECT engine_key, calls, rows, total_exec_time, mean_exec_time,
                       min_exec_time, max_exec_time, stats_since,
                       MAX(captured_at) AS captured_at
                FROM counter_snapshot
                WHERE target_id = ? AND epoch_id = ?
                GROUP BY engine_key
                """,
                (target_id, epoch_id),
            ).fetchall()
        return [dict(row) for row in rows]

    # -- identity aliases -----------------------------------------------------

    def record_identity_aliases(
        self,
        target_id: str,
        epoch_id: str,
        aliases: Mapping[str, str],
        *,
        owner_id: Optional[str] = None,
        fencing_token: Optional[int] = None,
        now: Optional[int] = None,
    ) -> int:
        """Persist engine_key -> normalized_hash resolutions for one epoch.

        Idempotent for a retried batch: a re-resolved key updates its hash in
        place. Returns the number of aliases written.
        """
        params = [
            (target_id, engine_key, normalized_hash, epoch_id)
            for engine_key, normalized_hash in aliases.items()
        ]
        if not params:
            return 0
        with self._write() as conn:
            self._apply_fence(conn, target_id, owner_id, fencing_token, now)
            conn.executemany(
                """
                INSERT INTO identity_alias (target_id, engine_key, normalized_hash, epoch_id)
                VALUES (?,?,?,?)
                ON CONFLICT(target_id, engine_key, epoch_id) DO UPDATE SET
                  normalized_hash = excluded.normalized_hash
                """,
                params,
            )
        return len(params)

    def get_identity_aliases(self, target_id: str, epoch_id: str) -> Dict[str, str]:
        """Return every engine_key -> normalized_hash mapping for one epoch."""
        with self._read() as conn:
            rows = conn.execute(
                "SELECT engine_key, normalized_hash FROM identity_alias"
                " WHERE target_id = ? AND epoch_id = ?",
                (target_id, epoch_id),
            ).fetchall()
        return {row["engine_key"]: row["normalized_hash"] for row in rows}

    def rekey_identities(self, mapping: Mapping[str, str]) -> int:
        """Point observation history at identities library.db has re-keyed.

        library.db owns query identity, and a migration there can re-key a
        query onto a different hash while the aliases, windows, and
        execution spans stored here still name the old one. Rewrites them
        to the new hash. A window or span that already exists under the new
        hash absorbs the old one -- counter deltas add, rates and spans
        take the wider value -- so history survives two identities merging
        into one. Returns the number of rows moved.
        """
        pairs = [
            (old, new)
            for old, new in mapping.items()
            if old and new and old != new
        ]
        if not pairs:
            return 0
        moved = 0
        with self._write() as conn:
            for old, new in pairs:
                moved += conn.execute(
                    "UPDATE identity_alias SET normalized_hash = ?"
                    " WHERE normalized_hash = ?",
                    (new, old),
                ).rowcount
                conn.execute(
                    """
                    INSERT INTO recent_observation
                      SELECT target_id, :new, window_start, window_end,
                             calls_delta, exec_time_delta, approximate_qps,
                             freshness, completeness, attribution
                      FROM recent_observation WHERE normalized_hash = :old
                    ON CONFLICT(target_id, normalized_hash, window_end)
                    DO UPDATE SET
                      window_start = MIN(window_start, excluded.window_start),
                      calls_delta = COALESCE(calls_delta, 0)
                        + COALESCE(excluded.calls_delta, 0),
                      exec_time_delta = COALESCE(exec_time_delta, 0)
                        + COALESCE(excluded.exec_time_delta, 0),
                      approximate_qps = MAX(
                        COALESCE(approximate_qps, 0),
                        COALESCE(excluded.approximate_qps, 0)
                      )
                    """,
                    {"old": old, "new": new},
                )
                moved += conn.execute(
                    "DELETE FROM recent_observation WHERE normalized_hash = ?",
                    (old,),
                ).rowcount
                conn.execute(
                    """
                    INSERT INTO rdst_execution
                      SELECT target_id, :new, lane, run_id, started_at,
                             ended_at, exec_count
                      FROM rdst_execution WHERE normalized_hash = :old
                    ON CONFLICT(target_id, run_id, normalized_hash) DO UPDATE SET
                      started_at = MIN(started_at, excluded.started_at),
                      ended_at = MAX(
                        COALESCE(ended_at, 0), COALESCE(excluded.ended_at, 0)
                      ),
                      exec_count = COALESCE(exec_count, 0)
                        + COALESCE(excluded.exec_count, 0)
                    """,
                    {"old": old, "new": new},
                )
                moved += conn.execute(
                    "DELETE FROM rdst_execution WHERE normalized_hash = ?",
                    (old,),
                ).rowcount
        return moved

    # -- recent observations --------------------------------------------------

    def record_recent_observations(
        self,
        target_id: str,
        rows: Iterable[Mapping[str, Any]],
        *,
        freshness: str = "interval",
        attribution: str = "production_only",
        owner_id: Optional[str] = None,
        fencing_token: Optional[int] = None,
        now: Optional[int] = None,
    ) -> int:
        """Persist one cycle's per-identity window results in one transaction.

        Each row maps normalized_hash, window_start, window_end, completeness,
        plus the nullable calls_delta / exec_time_delta / approximate_qps.
        A row may carry its own attribution (e.g. contains_rdst_traffic);
        rows without one take the batch-level default. Idempotent for a
        retried batch: conflicting (target_id, normalized_hash, window_end)
        rows are updated in place. Returns the number of rows written.
        """
        params = [
            {
                "target_id": target_id,
                "normalized_hash": row["normalized_hash"],
                "window_start": row["window_start"],
                "window_end": row["window_end"],
                "calls_delta": row.get("calls_delta"),
                "exec_time_delta": row.get("exec_time_delta"),
                "approximate_qps": row.get("approximate_qps"),
                "freshness": freshness,
                "completeness": row["completeness"],
                "attribution": row.get("attribution", attribution),
            }
            for row in rows
        ]
        if not params:
            return 0
        with self._write() as conn:
            self._apply_fence(conn, target_id, owner_id, fencing_token, now)
            conn.executemany(
                """
                INSERT INTO recent_observation (
                  target_id, normalized_hash, window_start, window_end,
                  calls_delta, exec_time_delta, approximate_qps,
                  freshness, completeness, attribution
                ) VALUES (
                  :target_id, :normalized_hash, :window_start, :window_end,
                  :calls_delta, :exec_time_delta, :approximate_qps,
                  :freshness, :completeness, :attribution
                )
                ON CONFLICT(target_id, normalized_hash, window_end) DO UPDATE SET
                  window_start = excluded.window_start,
                  calls_delta = excluded.calls_delta,
                  exec_time_delta = excluded.exec_time_delta,
                  approximate_qps = excluded.approximate_qps,
                  freshness = excluded.freshness,
                  completeness = excluded.completeness,
                  attribution = excluded.attribution
                """,
                params,
            )
        return len(params)

    # -- rdst self-executions -------------------------------------------------

    def record_rdst_executions(
        self,
        rows: Iterable[Mapping[str, Any]],
        *,
        owner_id: Optional[str] = None,
        fencing_token: Optional[int] = None,
        now: Optional[int] = None,
    ) -> int:
        """Persist RDST self-execution windows for attribution (research Q11).

        Each row maps target_id, normalized_hash, lane, run_id, started_at,
        ended_at, and exec_count. exec_count is the number of executions RDST
        knows it completed against the target for this identity; None means
        the count is not exactly known, so overlapping observation windows
        should be marked contains_rdst_traffic without subtracting anything.
        Idempotent for a retried run: conflicting (target_id, run_id,
        normalized_hash) rows are overwritten in place. Returns rows written.
        """
        params = [
            {
                "target_id": row["target_id"],
                "normalized_hash": row["normalized_hash"],
                "lane": row["lane"],
                "run_id": row["run_id"],
                "started_at": row["started_at"],
                "ended_at": row.get("ended_at"),
                "exec_count": row.get("exec_count"),
            }
            for row in rows
            if row.get("exec_count") is None
            or (
                not isinstance(row.get("exec_count"), bool)
                and row.get("exec_count") > 0
            )
        ]
        if not params:
            return 0
        with self._write() as conn:
            for target_id in sorted({row["target_id"] for row in params}):
                self._apply_fence(conn, target_id, owner_id, fencing_token, now)
            conn.executemany(
                """
                INSERT INTO rdst_execution (
                  target_id, normalized_hash, lane, run_id,
                  started_at, ended_at, exec_count
                ) VALUES (
                  :target_id, :normalized_hash, :lane, :run_id,
                  :started_at, :ended_at, :exec_count
                )
                ON CONFLICT(target_id, run_id, normalized_hash) DO UPDATE SET
                  lane = excluded.lane,
                  started_at = excluded.started_at,
                  ended_at = excluded.ended_at,
                  exec_count = excluded.exec_count
                """,
                params,
            )
        return len(params)

    def rdst_executions_overlapping(
        self,
        target_id: str,
        normalized_hash: str,
        window_start: float,
        window_end: float,
    ) -> List[Dict[str, Any]]:
        """Return self-executions whose span overlaps the (start, end] window.

        The start is exclusive and the end inclusive, matching counter deltas
        between consecutive snapshots. This also ensures a point bucket at a
        shared boundary belongs to exactly one adjacent window. A NULL
        ended_at means the end of the run was never recorded; such a row is
        treated as ended OPEN_EXECUTION_MAX_AGE_SECONDS after its started_at,
        so it overlaps windows near its run but never every later window,
        while its stored ended_at stays NULL in the returned rows. Rows carry
        lane, run_id, started_at, ended_at, and exec_count (None meaning
        "mark, don't subtract"), oldest first.
        """
        with self._read() as conn:
            return self._executions_overlapping(
                conn, target_id, normalized_hash, window_start, window_end
            )

    def rdst_executions_overlapping_many(
        self,
        target_id: str,
        windows: Iterable[tuple[str, float, float]],
    ) -> Dict[tuple[str, float, float], List[Dict[str, Any]]]:
        """Batched :meth:`rdst_executions_overlapping` on one connection.

        ``windows`` iterates (normalized_hash, window_start, window_end)
        tuples; the result maps each tuple to exactly the rows the single
        reader would return for it. One collection cycle asks about hundreds
        of windows, and the per-target index keeps each per-window read
        cheap, so sharing one read connection is the whole win.
        """
        results: Dict[tuple[str, float, float], List[Dict[str, Any]]] = {}
        with self._read() as conn:
            for normalized_hash, window_start, window_end in windows:
                results[(normalized_hash, window_start, window_end)] = (
                    self._executions_overlapping(
                        conn, target_id, normalized_hash, window_start, window_end
                    )
                )
        return results

    @staticmethod
    def _executions_overlapping(
        conn: sqlite3.Connection,
        target_id: str,
        normalized_hash: str,
        window_start: float,
        window_end: float,
    ) -> List[Dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT lane, run_id, started_at, ended_at, exec_count
            FROM rdst_execution
            WHERE target_id = ? AND normalized_hash = ?
              AND started_at <= ?
              AND COALESCE(ended_at, started_at + ?) > ?
            ORDER BY started_at
            """,
            (
                target_id,
                normalized_hash,
                window_end,
                OPEN_EXECUTION_MAX_AGE_SECONDS,
                window_start,
            ),
        ).fetchall()
        return [dict(row) for row in rows]

    # -- collector state ----------------------------------------------------

    def upsert_collector_state(
        self,
        target_id: str,
        *,
        state: str,
        last_attempt_at: Optional[int] = None,
        last_success_at: Optional[int] = None,
        duration_ms: Optional[int] = None,
        next_due_at: Optional[int] = None,
        error_code: Optional[str] = None,
        source_capabilities: Optional[Mapping[str, Any]] = None,
        epoch_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        fencing_token: Optional[int] = None,
        now: Optional[int] = None,
    ) -> None:
        """Replace the collector state row for a target with this call's values."""
        capabilities_json = (
            json.dumps(source_capabilities, sort_keys=True)
            if source_capabilities is not None
            else None
        )
        with self._write() as conn:
            self._apply_fence(conn, target_id, owner_id, fencing_token, now)
            conn.execute(
                """
                INSERT INTO collector_state (
                  target_id, state, last_attempt_at, last_success_at,
                  duration_ms, next_due_at, error_code, source_capabilities,
                  epoch_id
                ) VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(target_id) DO UPDATE SET
                  state = excluded.state,
                  last_attempt_at = excluded.last_attempt_at,
                  last_success_at = excluded.last_success_at,
                  duration_ms = excluded.duration_ms,
                  next_due_at = excluded.next_due_at,
                  error_code = excluded.error_code,
                  source_capabilities = excluded.source_capabilities,
                  epoch_id = excluded.epoch_id
                """,
                (
                    target_id,
                    state,
                    last_attempt_at,
                    last_success_at,
                    duration_ms,
                    next_due_at,
                    error_code,
                    capabilities_json,
                    epoch_id,
                ),
            )

    def get_collector_state(self, target_id: str) -> Optional[Dict[str, Any]]:
        with self._read() as conn:
            row = conn.execute(
                "SELECT * FROM collector_state WHERE target_id = ?",
                (target_id,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        if result["source_capabilities"] is not None:
            result["source_capabilities"] = json.loads(result["source_capabilities"])
        return result

    # -- event log ----------------------------------------------------------

    def append_event(
        self,
        target_id: str,
        kind: str,
        payload: str | Mapping[str, Any],
        created_at: int,
        *,
        owner_id: Optional[str] = None,
        fencing_token: Optional[int] = None,
        now: Optional[int] = None,
    ) -> int:
        """Append one event and return its monotonically increasing seq.

        Mapping payloads are stored as JSON text; strings are stored as-is.
        """
        text = payload if isinstance(payload, str) else json.dumps(payload, sort_keys=True)
        with self._write() as conn:
            self._apply_fence(conn, target_id, owner_id, fencing_token, now)
            row = conn.execute(
                """
                INSERT INTO observation_event (target_id, kind, payload, created_at)
                VALUES (?,?,?,?) RETURNING seq
                """,
                (target_id, kind, text, created_at),
            ).fetchone()
        return row[0]

    def events_since(
        self, target_id: str, seq: int, limit: int = 1000
    ) -> List[Dict[str, Any]]:
        """Return events for a target strictly newer than seq, oldest first."""
        with self._read() as conn:
            rows = conn.execute(
                """
                SELECT seq, kind, payload, created_at FROM observation_event
                WHERE target_id = ? AND seq > ?
                ORDER BY seq LIMIT ?
                """,
                (target_id, seq, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def latest_seq(self, target_id: str) -> int:
        """Return the newest seq for a target, or 0 when it has no events."""
        with self._read() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM observation_event WHERE target_id = ?",
                (target_id,),
            ).fetchone()
        return row[0]

    def earliest_seq(self, target_id: str) -> int:
        """Return the oldest retained seq for a target, or 0 when it has no events.

        Together with :meth:`latest_seq` this bounds the replayable window,
        so a reconnecting client's cursor can be classified as retained,
        pruned, or foreign.
        """
        with self._read() as conn:
            row = conn.execute(
                "SELECT COALESCE(MIN(seq), 0) FROM observation_event WHERE target_id = ?",
                (target_id,),
            ).fetchone()
        return row[0]

    # -- leases -------------------------------------------------------------

    def acquire_lease(
        self, target_id: str, owner_id: str, ttl_s: int, now: int
    ) -> Optional[int]:
        """Acquire or renew the collection lease for a target.

        Fenced UPSERT per the research: only a target that has never had a
        lease row starts at token 1; every takeover of an expired or
        released lease and every renewal by the current owner increments
        the surviving row's token, so it is monotonic for the lifetime of
        the file and a stalled former owner is rejected by the write-time
        fence. Returns the fencing token on success, or None when another
        live process owns the lease.
        """
        with self._write() as conn:
            row = conn.execute(
                """
                INSERT INTO collector_lease (target_id, owner_id, fencing_token, expires_at)
                VALUES (:target, :me, 1, :now + :ttl)
                ON CONFLICT(target_id) DO UPDATE SET
                  owner_id = :me,
                  fencing_token = collector_lease.fencing_token + 1,
                  expires_at = :now + :ttl
                WHERE collector_lease.expires_at < :now
                   OR collector_lease.owner_id = :me
                RETURNING fencing_token
                """,
                {"target": target_id, "me": owner_id, "ttl": ttl_s, "now": now},
            ).fetchone()
        return row[0] if row is not None else None

    def release_lease(self, target_id: str, owner_id: str) -> bool:
        """Expire this owner's lease immediately. Returns True on release.

        The row is kept, owner and token intact, with expires_at forced to
        zero. Deleting it would let the next acquire re-insert at token 1
        and hand a stale holder of an old token 1 a colliding fence (ABA);
        keeping the row makes every future acquire increment the token.
        """
        with self._write() as conn:
            cursor = conn.execute(
                "UPDATE collector_lease SET expires_at = 0"
                " WHERE target_id = ? AND owner_id = ?",
                (target_id, owner_id),
            )
            return cursor.rowcount > 0

    # -- retention ----------------------------------------------------------

    def prune(self, cutoff: int, chunk: int = 10000) -> int:
        """Delete counter snapshots captured before cutoff, in chunks.

        The newest snapshot per (target_id, engine_key, epoch_id) survives
        regardless of the cutoff: :meth:`latest_counter_snapshots` rebuilds
        the restart delta baseline from exactly those rows, so pruning them
        would silently re-baseline a restarted collector.

        Each chunk is its own BEGIN IMMEDIATE transaction so a large purge
        never blocks a concurrent CLI for seconds; incremental_vacuum then
        returns freed pages to the filesystem. Returns rows deleted.

        counter_snapshot is WITHOUT ROWID, so the chunk is keyed on the
        primary key (row values, SQLite >= 3.15) rather than rowid.
        """
        deleted = 0
        while True:
            with self._write() as conn:
                cursor = conn.execute(
                    """
                    DELETE FROM counter_snapshot
                    WHERE (target_id, engine_key, captured_at) IN (
                      SELECT target_id, engine_key, captured_at
                      FROM counter_snapshot AS victim
                      WHERE captured_at < ?
                        AND EXISTS (
                          SELECT 1 FROM counter_snapshot newer
                          WHERE newer.target_id = victim.target_id
                            AND newer.engine_key = victim.engine_key
                            AND newer.epoch_id = victim.epoch_id
                            AND newer.captured_at > victim.captured_at
                        )
                      LIMIT ?
                    )
                    """,
                    (cutoff, chunk),
                )
                batch = cursor.rowcount
            deleted += batch
            if batch < chunk:
                break
        self._incremental_vacuum()
        return deleted

    def prune_events(self, cutoff: int, keep: int = 1000, chunk: int = 10000) -> int:
        """Compact the observation event log per target, in chunks.

        Every target retains at least its newest ``keep`` events and every
        event created at or after ``cutoff``, whichever keeps more; only
        rows failing both tests are deleted. A pruned-away subscriber
        cursor then falls below :meth:`earliest_seq` and is healed by the
        caller's in-band resync path. Returns rows deleted.
        """
        with self._read() as conn:
            targets = [
                row["target_id"]
                for row in conn.execute(
                    "SELECT DISTINCT target_id FROM observation_event"
                )
            ]
        deleted = 0
        for target_id in targets:
            # Seq of the keep-th newest event; None means the target holds
            # fewer than keep events and everything stays. Events appended
            # after this read only raise the true floor, so a stale floor
            # over-retains, never over-deletes.
            with self._read() as conn:
                row = conn.execute(
                    """
                    SELECT seq FROM observation_event WHERE target_id = ?
                    ORDER BY seq DESC LIMIT 1 OFFSET ?
                    """,
                    (target_id, keep - 1),
                ).fetchone()
            if row is None:
                continue
            keep_floor = row["seq"]
            while True:
                with self._write() as conn:
                    cursor = conn.execute(
                        """
                        DELETE FROM observation_event
                        WHERE seq IN (
                          SELECT seq FROM observation_event
                          WHERE target_id = ? AND seq < ? AND created_at < ?
                          LIMIT ?
                        )
                        """,
                        (target_id, keep_floor, cutoff, chunk),
                    )
                    batch = cursor.rowcount
                deleted += batch
                if batch < chunk:
                    break
        self._incremental_vacuum()
        return deleted

    def prune_rdst_executions(
        self, cutoff: float, chunk: int = 10000, *, now: Optional[float] = None
    ) -> int:
        """Delete closed execution evidence ending before ``cutoff``.

        Rows with a NULL ``ended_at`` describe an execution whose end was
        never observed.  A fresh one is retained: deleting it could turn a
        later counter window from partial into production-only.  When ``now``
        is supplied, open rows older than OPEN_EXECUTION_MAX_AGE_SECONDS are
        first closed at ``started_at`` plus that bound (matching the overlap
        reader's treatment of them) so they age out through the normal delete
        below instead of surviving a crash forever.  The primary-key subquery
        keeps each transaction bounded for the WITHOUT ROWID table.  Returns
        rows deleted.
        """
        if now is not None:
            with self._write() as conn:
                conn.execute(
                    """
                    UPDATE rdst_execution
                    SET ended_at = started_at + :bound
                    WHERE ended_at IS NULL AND started_at < :now - :bound
                    """,
                    {"bound": OPEN_EXECUTION_MAX_AGE_SECONDS, "now": now},
                )
        deleted = 0
        while True:
            with self._write() as conn:
                cursor = conn.execute(
                    """
                    DELETE FROM rdst_execution
                    WHERE (target_id, run_id, normalized_hash) IN (
                      SELECT target_id, run_id, normalized_hash
                      FROM rdst_execution
                      WHERE ended_at IS NOT NULL AND ended_at < ?
                      LIMIT ?
                    )
                    """,
                    (cutoff, chunk),
                )
                batch = cursor.rowcount
            deleted += batch
            if batch < chunk:
                break
        self._incremental_vacuum()
        return deleted

    def _incremental_vacuum(self) -> None:
        with self._write_lock:
            conn = self._write_conn
            if conn is not None:
                conn.execute("PRAGMA incremental_vacuum(1000)")


def _execution_evidence_rows(
    target_id: Optional[str],
    executions: Iterable[Mapping[str, Any]],
    *,
    lane: str,
    run_id: str,
    started_at: float,
    ended_at: Optional[float],
) -> List[Dict[str, Any]]:
    if not target_id:
        return []
    from shared.query_registry.query_registry import hash_sql

    merged: Dict[str, Optional[int]] = {}
    for execution in executions:
        sql = execution.get("sql")
        if not sql:
            continue
        count = execution.get("exec_count")
        # A zero row is not neutral: overlap alone sets
        # contains_rdst_traffic. No completed/possible execution means no
        # evidence row at all.
        if isinstance(count, bool) or (count is not None and count <= 0):
            continue
        normalized_hash = hash_sql(sql)
        if normalized_hash in merged:
            previous = merged[normalized_hash]
            count = None if previous is None or count is None else previous + count
        merged[normalized_hash] = count
    return [
        {
            "target_id": target_id,
            "normalized_hash": normalized_hash,
            "lane": lane,
            "run_id": run_id,
            "started_at": started_at,
            "ended_at": ended_at,
            "exec_count": count,
        }
        for normalized_hash, count in merged.items()
        if count is None or count > 0
    ]


class ExecutionEvidenceWriter:
    """Run-scoped best-effort writer that reuses one ObservationStore.

    Long benchmarks may flush hundreds of time buckets. Keeping this writer
    for the run avoids reopening SQLite and running its initialization health
    check for every bucket. It never creates cache.db for CLI-only users.
    """

    def __init__(
        self,
        target_id: Optional[str],
        *,
        lane: str,
        cache_db_path: Optional[Path | str] = None,
    ) -> None:
        self.target_id = target_id
        self.lane = lane
        self.path = (
            Path(cache_db_path)
            if cache_db_path is not None
            else default_cache_db_path()
        )
        self._lock = threading.Lock()
        self._store: Optional[ObservationStore] = None
        self._open_attempted = False

    def _ensure_store(self) -> Optional[ObservationStore]:
        if self._store is not None or self._open_attempted:
            return self._store
        if not self.target_id or not self.path.is_file():
            return None
        self._open_attempted = True
        self._store = ObservationStore(self.path)
        return self._store

    def record(
        self,
        executions: Iterable[Mapping[str, Any]],
        *,
        run_id: str,
        started_at: float,
        ended_at: Optional[float],
    ) -> None:
        try:
            rows = _execution_evidence_rows(
                self.target_id,
                executions,
                lane=self.lane,
                run_id=run_id,
                started_at=started_at,
                ended_at=ended_at,
            )
            self._persist(rows)
        except Exception:
            logger.debug(
                "Failed to record %s execution evidence for target %s",
                self.lane,
                self.target_id,
                exc_info=True,
            )

    def record_rows(self, evidence: Iterable[Mapping[str, Any]]) -> None:
        """Persist differently-timed evidence rows in one SQLite transaction."""
        try:
            rows: List[Dict[str, Any]] = []
            for item in evidence:
                rows.extend(
                    _execution_evidence_rows(
                        self.target_id,
                        [item],
                        lane=self.lane,
                        run_id=str(item["run_id"]),
                        started_at=float(item["started_at"]),
                        ended_at=(
                            None
                            if item.get("ended_at") is None
                            else float(item["ended_at"])
                        ),
                    )
                )
            self._persist(rows)
        except Exception:
            logger.debug(
                "Failed to record %s execution evidence for target %s",
                self.lane,
                self.target_id,
                exc_info=True,
            )

    def _persist(self, rows: List[Dict[str, Any]]) -> None:
        if not rows:
            return
        try:
            with self._lock:
                store = self._ensure_store()
                if store is not None:
                    store.record_rdst_executions(rows)
        except Exception:
            logger.debug(
                "Failed to record %s execution evidence for target %s",
                self.lane,
                self.target_id,
                exc_info=True,
            )

    def close(self) -> None:
        with self._lock:
            if self._store is not None:
                self._store.close()
                self._store = None

    def __enter__(self) -> "ExecutionEvidenceWriter":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()


def record_execution_evidence(
    target_id: Optional[str],
    executions: Iterable[Mapping[str, Any]],
    *,
    lane: str,
    run_id: str,
    started_at: float,
    ended_at: Optional[float],
    cache_db_path: Optional[Path | str] = None,
) -> None:
    """Best-effort recording of RDST self-executions from an execution lane.

    Each execution maps "sql" (the statement RDST ran, hashed here with the
    registry's hash_sql so the identity matches discovery) and "exec_count"
    (executions RDST knows completed for that statement; None when the count
    is not exactly known, meaning overlapping windows get marked rather than
    subtracted). Executions hashing to the same identity are merged: counts
    sum, and one unknown count makes the merged count unknown.

    This is evidence, never product behavior: the call must not fail or slow
    the user's operation, so every error is swallowed with a debug log, and
    the store is only opened when cache.db already exists. CLI-only users who
    never ran web discovery have no cache.db, and recording never creates
    one, so they keep zero new files.
    """
    with ExecutionEvidenceWriter(
        target_id, lane=lane, cache_db_path=cache_db_path
    ) as writer:
        writer.record(
            executions,
            run_id=run_id,
            started_at=started_at,
            ended_at=ended_at,
        )
