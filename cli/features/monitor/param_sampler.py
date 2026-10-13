"""Frequent, cheap capture of real parameter values for the queries that carry the load.

The collector reads pg_stat_activity once a minute, so fast queries are rarely caught mid-run
and Deep Analyze falls back to table-sampled or no values. This sampler polls every few
seconds, only for top-load queries that still lack samples, with one small catalog read:
PostgreSQL `pg_stat_activity` filtered to those queries' ids (active sessions, and idle ones
whose last statement ran in the last two minutes, which is how fast queries get caught), MySQL
`performance_schema.events_statements_history(_long)`, whose SQL_TEXT keeps the literals.
Each read has a short statement timeout and runs in a worker thread, off the event loop.
Samples land in the monitor store (`query_sample`, several distinct texts per key).
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import time
from typing import Any, Callable

from shared.monitor.monitor_store import SAMPLES_PER_QUERY, MonitorStore
from shared.target_backoff import TargetBackoff, target_backoff

logger = logging.getLogger(__name__)
INTERVAL_S = 3.0
IDLE_INTERVAL_S = 30.0
TOP_KEYS = 20
WANTED_REFRESH_S = 60
STATEMENT_TIMEOUT_MS = 500
MAX_ROWS = 200
# An idle session's last statement counts as a live sample for this long after it ran.
RECENT_S = 120
# pg_stat_activity cuts text at track_activity_query_size (1 kB by default); a cut text
# can't be aligned with its template, so it isn't kept.
TRUNCATED_AT = 1023
_PLACEHOLDER = re.compile(r"\$\d+")
LANE = "rdst/sample"


def sample_interval() -> float:
    try:
        value = float(os.environ.get("RDST_MONITOR_SAMPLE_INTERVAL_S", INTERVAL_S))
        return _safe_interval(value)
    except ValueError:
        return INTERVAL_S


def _safe_interval(value: float) -> float:
    if not math.isfinite(value) or value < 0:
        return INTERVAL_S
    return max(1.0, value) if value else 0.0


def usable(text: str) -> bool:
    """A literal-bearing, whole statement: not a `$1` template, not cut off."""
    return bool(text) and len(text) < TRUNCATED_AT and not _PLACEHOLDER.search(text)


class ParamSampler:
    def __init__(self, store: MonitorStore, *, clock: Callable[[], float] = time.time,
                 interval_s: float | None = None, backoff: TargetBackoff | None = None) -> None:
        self.store = store
        self.clock = clock
        self.interval_s = sample_interval() if interval_s is None else _safe_interval(interval_s)
        self.backoff = backoff or (target_backoff if clock is time.time else TargetBackoff())
        self._task: asyncio.Task | None = None
        self._connections: dict[str, Any] = {}
        self._wanted: dict[str, tuple[float, set[str]]] = {}
        self.polls = 0
        self.captured = 0
        self._last_error: dict[str, str] = {}
        self._sample_connection: Any = None
        self._next_poll: dict[str, tuple[float, float]] = {}
        self._inflight: asyncio.Task | None = None
        # Databases whose connection settings changed; their next pass reconnects.
        self._stale: set[str] = set()

    def reconnect(self, target: str) -> None:
        self._stale.add(target)

    async def start(self) -> None:
        if self._task is None and self.interval_s > 0:
            self._task = asyncio.create_task(self._run(), name="monitor-param-sampler")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        # A cancelled asyncio waiter leaves its native thread running. Close only after
        # that worker has returned, keeping the SQLite and driver handles single-owner.
        if self._inflight is not None:
            done, _ = await asyncio.wait({self._inflight}, timeout=15)
            if not done:
                logger.warning("Parameter sampler exceeded shutdown grace")
                def close_when_finished(task: asyncio.Task) -> None:
                    if not task.cancelled():
                        task.exception()
                    asyncio.create_task(asyncio.to_thread(self._close_all))
                self._inflight.add_done_callback(close_when_finished)
                return
        await asyncio.to_thread(self._close_all)

    async def _run(self) -> None:
        while True:
            busy = False
            try:
                self._inflight = asyncio.create_task(asyncio.to_thread(self.tick))
                busy = await asyncio.shield(self._inflight)
            except Exception:
                logger.debug("Parameter sampling pass failed", exc_info=True)
            await asyncio.sleep(self.interval_s if busy else max(self.interval_s,
                                                                   IDLE_INTERVAL_S))

    def tick(self) -> bool:
        """One pass over monitored targets; True when some target still wants samples."""
        busy = False
        while self._stale:
            self._close(self._stale.pop())
        targets = self.store.monitored_targets()
        for target in (set(self._connections) | set(self._wanted) | set(self._next_poll)
                       | set(self._last_error)) - targets:
            self._close(target)
            self._wanted.pop(target, None)
            self._next_poll.pop(target, None)
            self._last_error.pop(target, None)
            self.backoff.forget(target)
        for target in sorted(targets):
            due, delay = self._next_poll.get(target, (0.0, self.interval_s))
            if self.clock() < due or not self.backoff.acquire(target):
                continue
            try:
                found = self.sample_target(target)
                if found is None:
                    self.backoff.release(target)
                else:
                    self.backoff.finish(target, success=True)
                self._last_error.pop(target, None)
                delay = self.interval_s if found else min(max(delay * 2, 1.0), IDLE_INTERVAL_S)
                self._next_poll[target] = (self.clock() + delay, delay)
                busy |= bool(found)
            except Exception as exc:
                self.backoff.finish(target, success=False)
                # Once per distinct failure, so a broken target doesn't flood the log.
                message = f"{type(exc).__name__}: {exc}"
                if self._last_error.get(target) != message:
                    self._last_error[target] = message
                    logger.warning("Parameter sampling failed for %s: %s", target, message)
                self._close(target)
        if not targets and self._sample_connection is not None:
            self._sample_connection.close()
            self._sample_connection = None
        return busy

    def wanted_keys(self, target: str) -> set[str]:
        """Top-load queries (last 24 h) with fewer than the kept number of samples."""
        now = self.clock()
        cached = self._wanted.get(target)
        if cached and now - cached[0] < WANTED_REFRESH_S:
            return cached[1]
        from features.monitor.api.views import top_keys, workload_keys
        from features.monitor.delivery import query_totals

        totals = query_totals(self.store, target, int(now) - 86400, int(now) + 1)
        top = top_keys(totals, workload_keys(self.store, target, totals), TOP_KEYS)
        if not top:
            self._wanted[target] = (now, set())
            return set()
        placeholders = ",".join("?" for _ in top)
        counts = {row["query_key"]: row["n"] for row in self.store.read(
            f"SELECT query_key,COUNT(*) n FROM query_sample WHERE target=? "
            f"AND query_key IN ({placeholders}) GROUP BY query_key", (target, *top))}
        from shared.monitor.statements import analyzability

        texts = {row["query_key"]: row["text"] for row in self.store.read(
            f"SELECT query_key,text FROM query_text WHERE target=? "
            f"AND query_key IN ({placeholders})", (target, *top))}
        # Only statements Deep Analyze runs need values; DDL and writes never do.
        wanted = {key for key in top if counts.get(key, 0) < SAMPLES_PER_QUERY
                  and analyzability(texts.get(key, ""))["analyzable"]}
        self._wanted[target] = (now, wanted)
        return wanted

    def sample_target(self, target: str) -> bool | None:
        wanted = self.wanted_keys(target)
        if not wanted:
            self._close(target)
            return None
        from features.query_registry.discovery import query_discovery

        collector = query_discovery.collector_for(target)
        aliases = dict(getattr(collector, "_alias_map", {}) or {})
        engine = getattr(collector, "_engine", "postgresql")
        by_engine = {engine_key: key for engine_key, key in aliases.items() if key in wanted}
        if not by_engine:
            return None
        rows = self._read(target, engine, by_engine)
        self.polls += 1
        now = int(self.clock())
        candidates: dict[str, set[str]] = {}
        for key, text in rows[:MAX_ROWS]:
            texts = candidates.setdefault(key, set())
            if key in wanted and usable(text) and len(texts) < SAMPLES_PER_QUERY:
                texts.add(text)
        if not any(candidates.values()):
            return False
        if self._sample_connection is None:
            self.store.ensure_open()
            self._sample_connection = self.store._connect()
        connection = self._sample_connection
        # Capture uses one reusable local connection and one bounded transaction per target.
        with self.store._lock:
            fresh = []
            for key, texts in candidates.items():
                existing = {row[0] for row in connection.execute(
                    "SELECT text FROM query_sample WHERE target=? AND query_key=?",
                    (target, key))}
                fresh.extend((key, text) for text in sorted(texts - existing))
            if not fresh:
                return False
            connection.execute("BEGIN IMMEDIATE")
            try:
                for key, text in fresh:
                    self.store._insert_sample(connection, target, now, key, text)
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise
        self.captured += len(fresh)
        return True

    def _read(self, target: str, engine: str,
              by_engine: dict[str, str]) -> list[tuple[str, str]]:
        connection = self._connection(target)
        cursor = connection.cursor()
        try:
            if engine == "mysql":
                return self._read_mysql(cursor, by_engine)
            rows = self._read_postgres(cursor, by_engine)
            # A COMMIT ends the read-only transaction without counting as a rollback in
            # the database's own rollback rate, which RDST charts and alerts on.
            connection.commit()
            return rows
        except Exception:
            if engine != "mysql":
                connection.rollback()
            raise
        finally:
            cursor.close()

    @staticmethod
    def _read_postgres(cursor: Any, by_engine: dict[str, str]) -> list[tuple[str, str]]:
        from features.query_registry.statement_stats import parse_pg_engine_key
        from shared.db_connection import rdst_self_marker

        by_id: dict[int, set[str]] = {}
        for engine_key, key in by_engine.items():
            try:
                by_id.setdefault(parse_pg_engine_key(engine_key)[2], set()).add(key)
            except ValueError:
                continue
        if not by_id:
            return []
        cursor.execute(f"SET statement_timeout = {STATEMENT_TIMEOUT_MS}")
        cursor.execute(
            rdst_self_marker(LANE) + "SELECT query_id, query FROM pg_stat_activity "
            # An idle session still shows the statement it just ran, with its values and
            # query id, so fast queries are caught between runs; only recent ones count.
            f"WHERE (state = 'active' OR state_change > now() - interval '{RECENT_S} seconds') "
            "AND state IS NOT NULL AND pid <> pg_backend_pid() "
            "AND COALESCE(application_name, '') NOT LIKE 'rdst/%%' "
            f"AND datname = current_database() AND query_id = ANY(%s) LIMIT {MAX_ROWS}",
            (sorted(by_id),))
        return [(key, str(text)) for query_id, text in cursor.fetchall() if text
                for key in by_id.get(int(query_id), ())]

    @staticmethod
    def _read_mysql(cursor: Any, by_engine: dict[str, str]) -> list[tuple[str, str]]:
        from shared.db_connection import rdst_self_marker

        by_digest: dict[tuple[str, str], str] = {}
        for engine_key, key in by_engine.items():
            schema, _, digest = engine_key.rpartition(":")
            by_digest[(schema, digest)] = key
        digests = sorted({digest for _, digest in by_digest})
        placeholders = ",".join(["%s"] * len(digests))
        rows: list[tuple[str, str]] = []
        for table in ("events_statements_history_long", "events_statements_history"):
            try:
                cursor.execute(
                    rdst_self_marker(LANE) +
                    f"SELECT /*+ MAX_EXECUTION_TIME({STATEMENT_TIMEOUT_MS}) */ CURRENT_SCHEMA, "
                    f"DIGEST, SQL_TEXT FROM performance_schema.{table} "
                    f"WHERE SQL_TEXT IS NOT NULL AND DIGEST IN ({placeholders}) "
                    f"LIMIT {MAX_ROWS}", tuple(digests))
            except Exception as exc:
                # The consumer or table may be off; history (per-thread) is the fallback.
                if getattr(exc, "args", (None,))[0] in (1142, 1146):
                    continue
                raise
            for row in cursor.fetchall():
                # The target connection returns dict rows; tuples are accepted too.
                schema, digest, text = (
                    (row["CURRENT_SCHEMA"], row["DIGEST"], row["SQL_TEXT"])
                    if isinstance(row, dict) else row)
                key = by_digest.get((schema or "", digest))
                if key and text:
                    rows.append((key, str(text)))
            if rows:
                break
        return rows

    def _connection(self, target: str) -> Any:
        connection = self._connections.get(target)
        if connection is None:
            from shared.config.targets import TargetsConfig
            from shared.db_connection import create_direct_connection

            config = TargetsConfig()
            config.load()
            target_config = config.get(target)
            if not target_config:
                raise ValueError(f"No configuration for {target}")
            connection = create_direct_connection(target_config, connect_timeout=5, target=target,
                                                  lane=LANE, query_timeout_seconds=2)
            self._connections[target] = connection
        return connection

    def _close(self, target: str) -> None:
        connection = self._connections.pop(target, None)
        if connection is not None:
            try:
                connection.close()
            except Exception:
                logger.debug("Sampler connection close failed", exc_info=True)

    def _close_all(self) -> None:
        for target in list(self._connections):
            self._close(target)
        if self._sample_connection is not None:
            self._sample_connection.close()
            self._sample_connection = None
