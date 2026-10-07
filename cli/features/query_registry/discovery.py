"""Automatic query discovery for the web and desktop Query Library.

The CLI and web API share :class:`TopService` and :class:`QueryRegistry`, but
the automatic collector is an opt-in web/desktop orchestration layer. It reads
the same historical database statistics without changing CLI commands or
turning an observation into explicit Saved intent.
"""

from __future__ import annotations

import asyncio
import heapq
from collections import deque
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import os
import threading
import time
from typing import Any, AsyncGenerator, Callable, Deque, Dict, Optional

from features.query_registry.statement_stats import (
    COLLECT_ENTRANT_STATEMENTS,
    PG_VERSION_SQL,
    CollectResult,
    MysqlDigestSource,
    PgStatStatementsSource,
    StatementStatsSource,
    _toplevel_flag,
    mysql_sample_template,
    pg_engine_key,
)
from features.top.events import (
    TopConnectedEvent,
    TopErrorEvent,
    TopQueriesEvent,
)
from features.top.models import TopInput, TopOptions, TopQueryData
from features.top.service import TopService
from shared.query_registry import QueryRegistry, hash_sql
from shared.query_registry.query_registry import (
    _identity_slot_count,
    canonicalize_sql,
    extract_observed_params,
)
from shared.query_registry.self_traffic import match_self_template
from shared.query_registry.sql_normalizer import (
    normalize_and_extract,
    references_user_relations,
)
from shared.query_registry.delta_processor import (
    ABSENT,
    COMPLETE,
    CounterRow,
    DeltaResult,
    advance_baseline,
    process_snapshot,
)
from shared.query_registry.observation_store import (
    LeaseLostError,
    ObservationStore,
    default_cache_db_path,
)
from shared.monitor.monitor_store import (
    FULL_DETAIL_QUERIES, SERIES_QUERIES, MonitorStore, default_monitor_db_path, is_locked,
)

logger = logging.getLogger(__name__)

PG_MONITOR_DB_SQL = (
    "/*rdst:observe*/ SELECT xact_commit,xact_rollback,blks_hit,blks_read,"
    "deadlocks,numbackends,current_setting('max_connections')::integer,"
    "EXTRACT(EPOCH FROM stats_reset),"
    "(SELECT COUNT(*) FROM pg_stat_activity WHERE datname=current_database() "
    "AND wait_event_type='Lock' AND pid<>pg_backend_pid() "
    "AND COALESCE(application_name, '') NOT LIKE 'rdst/%') "
    "FROM pg_stat_database WHERE datname=current_database()"
)
LIBRARY_ADMISSION_LIMIT = 100
LIBRARY_REFRESH_LIMIT = 100
# Collector readings kept while monitor.db stays locked.
MONITOR_PENDING_MAX = 60
# Statement texts hashed into identities per cycle; the rest wait for later cycles,
# heaviest first. Hashing costs milliseconds per text, so this bounds one cycle's work.
IDENTITY_RESOLUTION_BATCH = 5_000
# The first monitored reading seeds its baseline before wider Library reconciliation.
STARTUP_IDENTITY_RESOLUTION_BATCH = 100
# Wall-clock budget for hashing identities in one cycle. A frozen build on a slow machine
# can spend tens of milliseconds per text, so thousands of new statements would hold the
# cycle past its deadline; the rest wait for later cycles, heaviest first.
IDENTITY_RESOLUTION_BUDGET_SECONDS = 3.0

MYSQL_MONITOR_STATUS_SQL = (
    "/*rdst:observe*/ SHOW GLOBAL STATUS WHERE Variable_name IN "
    "('Com_commit','Com_rollback','Innodb_buffer_pool_read_requests',"
    "'Innodb_pages_read','Innodb_deadlocks','Threads_connected')"
)
MYSQL_MONITOR_LOCK_SQL = "/*rdst:observe*/ SELECT COUNT(*) FROM performance_schema.data_lock_waits"
MYSQL_MONITOR_MAX_SQL = "/*rdst:observe*/ SHOW GLOBAL VARIABLES LIKE 'max_connections'"
MYSQL_MONITOR_DEADLOCK_SQL = (
    "/*rdst:observe*/ SELECT COUNT FROM information_schema.INNODB_METRICS "
    "WHERE NAME='lock_deadlocks' AND STATUS='enabled'"
)
# Data statements that failed. PostgreSQL's xact_rollback counts every transaction that ended
# in an error as well as explicit ROLLBACKs; Com_rollback only sees the ROLLBACK statements.
# Failed DESCRIBE, SHOW and unparseable statements (tools, RDST's schema reads) aren't
# transactions.
MYSQL_MONITOR_ERRORS_SQL = (
    "/*rdst:observe*/ SELECT COALESCE(SUM(SUM_ERRORS),0) FROM "
    "performance_schema.events_statements_summary_global_by_event_name WHERE EVENT_NAME IN ("
    "'statement/sql/select','statement/sql/insert','statement/sql/update',"
    "'statement/sql/delete','statement/sql/replace','statement/sql/insert_select',"
    "'statement/sql/replace_select','statement/sql/update_multi','statement/sql/delete_multi',"
    "'statement/sql/call_procedure')"
)


def mysql_monitor_sample(executor: Callable, epoch_id: str) -> tuple[int | str | None, ...]:
    """Return counters in the same positions as the PostgreSQL monitor sample."""
    status = {str(name).lower(): int(value) for name, value in
              executor(MYSQL_MONITOR_STATUS_SQL, None)}
    maximum = int(executor(MYSQL_MONITOR_MAX_SQL, None)[0][1])
    deadlocks = status.get("innodb_deadlocks")
    if deadlocks is None:
        try:
            rows = executor(MYSQL_MONITOR_DEADLOCK_SQL, None)
            deadlocks = int(rows[0][0]) if rows else 0
        except Exception:
            deadlocks = 0
    try:
        rows = executor(MYSQL_MONITOR_ERRORS_SQL, None)
        errors = int(rows[0][0] or 0) if rows else 0
    except Exception:
        errors = 0
    try:
        rows = executor(MYSQL_MONITOR_LOCK_SQL, None)
        lock_waits = int(rows[0][0]) if rows else None
    except Exception:
        lock_waits = None
    return (status.get("com_commit", 0), status.get("com_rollback", 0) + errors,
            status.get("innodb_buffer_pool_read_requests", 0),
            # Every page read from disk, read-ahead included, like PostgreSQL's blks_read;
            # Innodb_buffer_pool_reads leaves out the pages a scan prefetches.
            status.get("innodb_pages_read", 0),
            deadlocks, status["threads_connected"],
            maximum, epoch_id, lock_waits)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _unix_now() -> int:
    return int(time.time())


def _duration_ms(value: str) -> float:
    """Convert TopService's display duration back to registry milliseconds."""
    normalized = value.strip().lower()
    try:
        if normalized.endswith("ms"):
            return float(normalized[:-2])
        if normalized.endswith("s"):
            return float(normalized[:-1]) * 1000
        return float(normalized)
    except (TypeError, ValueError):
        return 0.0


def _reusable_top_service() -> TopService:
    """Default collector service: keeps execution resources warm across cycles."""
    return TopService(reuse_execution_resources=True)


TWO_PHASE_DISCOVERY_ENV = "RDST_TWO_PHASE_DISCOVERY"

# Beyond this replay gap a snapshot resync is cheaper for both sides than
# streaming the whole backlog event by event.
RESYNC_GAP_THRESHOLD = 5000

# Idle streams emit an id-bearing bookmark on this cadence (research 2b): the
# client's Last-Event-ID keeps tracking the latest durable seq, so a reconnect
# after a quiet stretch resumes instead of resyncing, and the traffic defeats
# proxy idle timeouts. Bookmarks are per-subscriber and never stored.
BOOKMARK_INTERVAL_SECONDS = 30

# Store retention (research 2b): each target keeps 15 minutes of events or
# its newest 1000, whichever retains more; snapshots keep the same window
# plus the latest row per engine key for the restart baseline.
RETENTION_WINDOW_SECONDS = 15 * 60
RETENTION_EVENT_KEEP = 1000
# Retention piggybacks on a successful cycle at most this often, so
# steady-state cycles are not taxed with compaction work.
RETENTION_MIN_INTERVAL_SECONDS = 600
# Per-cycle observation windows in cache.db; monitor.db keeps the history the product reads.
OBSERVATION_RETENTION_SECONDS = 60 * 60

# Attribution overlap reads widen the queried range symmetrically by this
# allowance: the per-cycle clock offset carries sub-second noise (fetch
# latency, thread handoff between the server read and the local clock read),
# so a little slack keeps boundary executions from being missed. An overlap
# admitted only by the allowance marks the window and nothing else: it is
# never subtracted and never downgrades completeness.
ATTRIBUTION_SKEW_ALLOWANCE_SECONDS = 2.0


def two_phase_discovery_enabled() -> bool:
    """Two-phase collection is the default; "0"/"false" forces the legacy TopService path."""
    return os.environ.get(TWO_PHASE_DISCOVERY_ENV, "1").strip().lower() not in ("0", "false")


# Source/capability gaps that cannot heal within a collector's lifetime:
# missing relation or function (extension not installed) or missing
# privileges on the statistics views.
_PG_CAPABILITY_SQLSTATES = frozenset({"42P01", "42883", "42501"})
# MySQL equivalents: 1146 no such table, 1142 command denied, 1227 privilege
# required.
_MYSQL_CAPABILITY_ERRNOS = frozenset({1142, 1146, 1227})
_CAPABILITY_MESSAGE_PATTERNS = (
    "does not exist",
    "doesn't exist",
    "permission denied",
    "command denied",
    "access denied",
)


def _capability_error_code(exc: BaseException) -> Optional[str]:
    """Classify a collection error as a permanent source/capability gap.

    Returns a short error code when the target lacks the statistics source
    (missing view or function, insufficient privileges) and None for
    transient errors (timeouts, connection loss). Driver exception
    attributes are checked first; the message match is a fallback because
    drivers expose codes differently.
    """
    if isinstance(exc, ValueError) and "unsupported engine" in str(exc):
        return "unsupported_engine"
    sqlstate = getattr(exc, "pgcode", None) or getattr(exc, "sqlstate", None)
    if sqlstate in _PG_CAPABILITY_SQLSTATES:
        return f"sqlstate_{sqlstate}"
    errno = getattr(exc, "errno", None)
    if errno is None and exc.args and isinstance(exc.args[0], int):
        errno = exc.args[0]
    if errno in _MYSQL_CAPABILITY_ERRNOS:
        return f"errno_{errno}"
    message = str(exc).lower()
    if any(state.lower() in message for state in _PG_CAPABILITY_SQLSTATES):
        return "sqlstate_message"
    if any(pattern in message for pattern in _CAPABILITY_MESSAGE_PATTERNS):
        return "message_match"
    return None


def _usable_statement_text(text: object) -> Optional[str]:
    """Return statement text usable for identity resolution, or None.

    None and engine placeholders (pg placeholder texts start with '<',
    e.g. '<insufficient privilege>') leave the key unresolved so its text
    is requested again once it becomes available.
    """
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    if not stripped or stripped.startswith("<"):
        return None
    return text


def _activity_capture_map(raw_texts: list[str]) -> Dict[str, str]:
    """Registry identity -> sampled literal-bearing text, first sample wins.

    hash_sql renumbers every placeholder style by textual position, so a
    sampled text's own hash reaches the statement's identity however it was
    minted: from literal-bearing SQL (live-activity saves, the legacy path,
    manual analyze) or from engine-normalized text (pg_stat_statements $N).
    MySQL digest identities keep DIGEST_TEXT's backtick quoting, which raw
    PROCESSLIST text rarely carries, and receive observed values from
    QUERY_SAMPLE_TEXT instead; MySQL samples typically reach literal-minted
    identities only. The snapshot has no meaningful order.
    """
    capture: Dict[str, str] = {}
    for raw in raw_texts:
        text = _usable_statement_text(raw)
        if text is None:
            continue
        try:
            capture.setdefault(hash_sql(text), text)
        except Exception:
            continue
    return capture


def _connection_executor(connection: Any) -> Callable[..., Any]:
    def executor(sql, params=None):
        cursor = connection.cursor()
        try:
            if params is None:
                cursor.execute(sql)
            else:
                cursor.execute(sql, params)
            if cursor.description is None:
                return []
            return cursor.fetchall()
        finally:
            cursor.close()

    return executor


def _counters_moved(previous: Optional[CounterRow], current: CounterRow) -> bool:
    """Whether ``current`` differs from the checkpointed row it would replace."""
    return previous is None or (
        previous.calls, previous.rows, previous.total_exec_time, previous.min_exec_time,
        previous.max_exec_time, previous.stats_since, previous.epoch_id,
    ) != (current.calls, current.rows, current.total_exec_time, current.min_exec_time,
          current.max_exec_time, current.stats_since, current.epoch_id)


OBSERVE_QUERY_TIMEOUT_S = 30


def _default_connection_factory(target: str) -> tuple[Any, str]:
    """Open the collector's own direct connection and report the target's engine."""
    from shared.config.targets import TargetsConfig, normalize_db_type
    from shared.db_connection import create_direct_connection

    cfg = TargetsConfig()
    cfg.load()
    name = target or cfg.get_default()
    target_config = cfg.get(name) if name else None
    if not target_config:
        raise ValueError(f"Target {target!r} is not configured")
    engine = normalize_db_type(target_config.get("engine")) or ""
    # Reads are catalog and statistics queries; a cap keeps one stuck database from holding
    # its monitor cycle.
    connection = create_direct_connection(target_config, target=name, lane="rdst/observe",
                                          query_timeout_seconds=OBSERVE_QUERY_TIMEOUT_S)
    if engine == "postgresql":
        # SET LOCAL session setup needs a transaction block; the per-cycle
        # rollback returns the connection to idle between collections.
        try:
            connection.autocommit = False
        except Exception:
            logger.debug("Could not disable autocommit for target %s", target, exc_info=True)
    elif engine == "mysql":
        # The statement-stats source reads rows positionally; direct MySQL
        # connections default to dict rows.
        try:
            import pymysql.cursors

            connection.cursorclass = pymysql.cursors.Cursor
            connection.autocommit(True)
        except Exception:
            logger.debug("Could not set tuple cursor for target %s", target, exc_info=True)
    return connection, engine


@dataclass(frozen=True)
class QueryDiscoveryEvent:
    cursor: int
    event: str
    data: Dict[str, Any]

    def to_sse(self) -> Dict[str, str]:
        return {
            "id": str(self.cursor),
            "event": self.event,
            "data": json.dumps({"cursor": self.cursor, **self.data}),
        }


class QueryDiscoveryCollector:
    """One scheduled historical collector for one configured target."""

    # Server-minus-local clock offset, measured on the latest two-phase
    # fetch. Counter windows carry server timestamps while rdst_execution
    # rows carry local time; attribution shifts window bounds by this offset
    # before the overlap read. The latest measurement wins over smoothing:
    # each cycle attributes its own windows against the two clocks read
    # within that same fetch, and an admin clock step is then absorbed
    # within one cycle.
    _clock_offset = 0.0

    def __init__(
        self,
        target: str,
        *,
        interval_seconds: float = 60.0,
        limit: int = 100,
        service_factory: Callable[[], TopService] = _reusable_top_service,
        registry_factory: Callable[[], QueryRegistry] = QueryRegistry,
        clock: Callable[[], str] = _utc_now,
        unix_clock: Callable[[], int] = _unix_now,
        local_clock: Callable[[], float] = time.time,
        history_size: int = 128,
        store: Optional[ObservationStore] = None,
        queue_maxsize: int = 32,
        two_phase: Optional[bool] = None,
        connection_factory: Callable[[str], tuple[Any, str]] = _default_connection_factory,
    ) -> None:
        self.target = target
        self.interval_seconds = interval_seconds
        self.limit = limit
        self._service_factory = service_factory
        self._service: Optional[TopService] = None
        # Two-phase collection reads engine statement counters directly and
        # feeds the delta lane; the legacy TopService path stays intact as
        # the rollback mode behind the same env flag.
        self._two_phase = two_phase_discovery_enabled() if two_phase is None else two_phase
        self._connection_factory = connection_factory
        self._connection: Optional[Any] = None
        self._stats_source: Optional[StatementStatsSource] = None
        self._engine = ""
        self._source_name = ""
        self._monitor: Optional[MonitorStore] = None
        self._monitor_enabled = False
        self._monitor_db_sample: Optional[tuple[Any, ...]] = None
        self._monitor_db_previous: Optional[tuple[Any, ...]] = None
        # False until a monitored cycle has set the baseline that rates are measured from.
        self._monitor_baseline_set = False
        # Minutes whose write failed on a locked monitor.db, written first next cycle.
        self._monitor_pending: Deque[tuple[Any, ...]] = deque(maxlen=MONITOR_PENDING_MAX)
        self._db_touch_seconds = 0.0
        self._library_refresh_cursor = 0
        # A permanent source/capability gap (missing extension, missing
        # privileges) switches this collector to the legacy TopService path
        # for its remaining lifetime; transient errors never set this.
        self._legacy_fallback = False
        self._source_unavailable: Optional[Dict[str, Any]] = None
        # Delta-lane state: baseline and cursor are restored together from
        # the store on collector start when a same-epoch baseline exists;
        # otherwise both start empty and the first sweep re-baselines.
        self._baseline: Dict[str, CounterRow] = {}
        self._baseline_captured_at: Optional[float] = None
        self._baseline_epoch = ""
        self._cursor_state: Optional[Dict[str, Any]] = None
        self._cursor_restored = False
        self._alias_map: Dict[str, str] = {}
        self._alias_epoch: Optional[str] = None
        # Identities whose SQL touches no user relation (RDST's own catalog
        # and statistics queries, SELECT 1 probes). They stay in the counter
        # and delta lanes as evidence but never enter the Query Library, and
        # they are excluded before the top-K cut so they cannot crowd out
        # user queries on an otherwise idle target.
        self._system_identities: set[str] = set()
        self._registry_factory = registry_factory
        self._clock = clock
        self._unix_clock = unix_clock
        self._local_clock = local_clock
        self._events: Deque[QueryDiscoveryEvent] = deque(maxlen=history_size)
        self._subscribers: set[asyncio.Queue[QueryDiscoveryEvent]] = set()
        self._task: Optional[asyncio.Task[None]] = None
        # Exactly one cadence owns a collector at a time. With the M4
        # scheduler enabled, SSE is a read-only transport: subscribing must
        # neither start a second polling task nor tear down the scheduler's
        # reusable resources when the last browser disconnects.
        self._scheduler_owned = False
        self._subscriber_state_callback: Optional[Callable[[str, bool], None]] = None
        self._wake = asyncio.Event()
        self._collect_lock = asyncio.Lock()
        # The observation store is the durable lane: event ids become its
        # monotonic seq and reconnect replay reads it, so a restart no longer
        # resets the cursor. All store failures degrade to in-memory behavior.
        self._store = store
        # Scheduler-owned write fence: while set, every store write carries
        # owner/token/now so the store rejects writes after a lost lease.
        # SSE-subscriber-driven collection leaves it unset and writes unfenced.
        self._write_fence: Optional[tuple[str, int, Callable[[], int]]] = None
        self._queue_maxsize = queue_maxsize
        self._last_retention_at: Optional[int] = None
        self._cursor = 0
        self._cursor_synced = False
        self._snapshot: Dict[str, Any] = {
            "target": target,
            "state": "starting",
            "updated_at": "",
            "source": "",
            "engine": "",
            "query_count": 0,
            "new_hashes": [],
            "changed": False,
            "stats": None,
            "capture_warning": None,
            "error": None,
        }

    @property
    def task(self) -> Optional[asyncio.Task[None]]:
        return self._task

    @property
    def has_subscribers(self) -> bool:
        return bool(self._subscribers)

    def set_write_fence(
        self, owner_id: str, fencing_token: int, now_fn: Callable[[], int]
    ) -> None:
        """Fence subsequent store writes with the scheduler's lease token."""
        self._write_fence = (owner_id, fencing_token, now_fn)

    def clear_write_fence(self) -> None:
        self._write_fence = None

    def set_scheduler_owned(self, owned: bool) -> None:
        """Select scheduler-owned cadence instead of subscriber-owned polling."""
        self._scheduler_owned = owned
        if owned:
            self._wake.set()

    def set_subscriber_state_callback(
        self, callback: Optional[Callable[[str, bool], None]]
    ) -> None:
        """Notify the scheduler on first-subscribe and last-unsubscribe edges."""
        self._subscriber_state_callback = callback

    @property
    def scheduler_owned(self) -> bool:
        return self._scheduler_owned

    def _fence_kwargs(self) -> Dict[str, Any]:
        if self._write_fence is None:
            return {}
        owner_id, fencing_token, now_fn = self._write_fence
        return {"owner_id": owner_id, "fencing_token": fencing_token, "now": now_fn()}

    def _registry_write_guard(self):
        """Hold the cache.db lease lock across one library.db commit.

        The lease and library live in different SQLite files. Checking the
        token and immediately releasing cache.db would leave a race in which
        a resumed process commits library.db after a new owner takes over.
        ObservationStore.write_fence instead keeps its write transaction open
        until QueryRegistry's library transaction has committed, ordering a
        takeover strictly after the old commit or rejecting the old writer.
        """
        if self._store is None or self._write_fence is None:
            return nullcontext()
        owner_id, fencing_token, now_fn = self._write_fence
        return self._store.write_fence(
            self.target,
            owner_id=owner_id,
            fencing_token=fencing_token,
            now=now_fn(),
        )

    def _registry(self) -> QueryRegistry:
        registry = self._registry_factory()
        registry.set_write_guard(
            self._registry_write_guard if self._write_fence is not None else None
        )
        return registry

    def _sync_cursor(self) -> None:
        """Lift the in-memory cursor to the store's seq once per collector."""
        if self._store is None or self._cursor_synced:
            return
        self._cursor_synced = True
        try:
            self._cursor = max(self._cursor, self._store.latest_seq(self.target))
        except Exception:
            logger.warning(
                "Failed to read the latest observation seq for target %s",
                self.target,
                exc_info=True,
            )

    def _store_append(self, event: str, data: Dict[str, Any]) -> Optional[int]:
        if self._store is None:
            return None
        try:
            return self._store.append_event(
                self.target,
                event,
                data,
                created_at=self._unix_clock(),
                **self._fence_kwargs(),
            )
        except LeaseLostError:
            raise
        except Exception:
            logger.warning(
                "Failed to persist a discovery event for target %s",
                self.target,
                exc_info=True,
            )
            return None

    def _persist(self, event: str, data: Dict[str, Any]) -> Optional[int]:
        self._sync_cursor()
        return self._store_append(event, data)

    async def publish_async(self, event: str, data: Dict[str, Any]) -> QueryDiscoveryEvent:
        """Persist in a worker thread, then deliver on the loop.

        The observation store can wait on another writer's lock (the Library write fence
        holds it across a commit); that wait must never stall the event loop.
        """
        return self._deliver(event, data, await asyncio.to_thread(self._persist, event, data))

    def _publish(self, event: str, data: Dict[str, Any]) -> QueryDiscoveryEvent:
        return self._deliver(event, data, self._persist(event, data))

    def _deliver(self, event: str, data: Dict[str, Any],
                 seq: Optional[int]) -> QueryDiscoveryEvent:
        # Ids stay monotonic even when the store is unavailable for a few
        # cycles; a reconnect then lands ahead of the durable seq and is
        # healed by the resync path.
        self._cursor = self._cursor + 1 if seq is None else max(self._cursor + 1, seq)
        discovery_event = QueryDiscoveryEvent(self._cursor, event, data)
        self._events.append(discovery_event)
        for subscriber in tuple(self._subscribers):
            try:
                subscriber.put_nowait(discovery_event)
            except asyncio.QueueFull:
                # A slow consumer gets one coalescing resync instead of an
                # unbounded backlog; it re-fetches state out of band.
                while not subscriber.empty():
                    subscriber.get_nowait()
                subscriber.put_nowait(self.resync_event("subscriber_overflow"))
        return discovery_event

    def snapshot_event(self) -> QueryDiscoveryEvent:
        return QueryDiscoveryEvent(self._cursor, "discovery_snapshot", self._snapshot)

    def bookmark_event(self) -> QueryDiscoveryEvent:
        """Cursor keepalive carrying the latest durable seq; never appended
        to the store and never a change signal for the client."""
        return QueryDiscoveryEvent(self._cursor, "bookmark", {})

    def resync_event(self, reason: str) -> QueryDiscoveryEvent:
        """In-band cursor reset: the 200-stream equivalent of 410 + relist."""
        data = {
            "reason": reason,
            "latest_seq": self._cursor,
            "state": dict(self._snapshot),
        }
        return QueryDiscoveryEvent(self._cursor, "resync", data)

    def replay_after(self, cursor: Optional[int]) -> list[QueryDiscoveryEvent]:
        """Replay retained deltas, or reconcile via snapshot/resync."""
        self._sync_cursor()
        if cursor is None:
            return [self.snapshot_event()]
        if self._store is not None:
            try:
                return self._replay_from_store(cursor)
            except Exception:
                logger.warning(
                    "Durable replay failed for target %s; using in-memory history",
                    self.target,
                    exc_info=True,
                )
        return self._replay_from_memory(cursor)

    def _replay_from_store(self, cursor: int) -> list[QueryDiscoveryEvent]:
        latest = self._store.latest_seq(self.target)
        if cursor == latest:
            return []
        if cursor > latest or cursor < self._store.earliest_seq(self.target) - 1:
            # Pruned history, or a cursor minted by another process/data dir.
            return [self.resync_event("cursor_not_retained")]
        if latest - cursor > RESYNC_GAP_THRESHOLD:
            # The history exists but is not worth replaying; reuse the reason
            # clients already handle for a cursor they cannot resume from.
            return [self.resync_event("cursor_not_retained")]
        # Page until the client reaches latest: events_since caps each read,
        # and the gap check above bounds the loop to a few pages.
        events: list[QueryDiscoveryEvent] = []
        position = cursor
        while position < latest:
            page = self._store.events_since(self.target, position)
            if not page:
                break
            events.extend(
                QueryDiscoveryEvent(row["seq"], row["kind"], json.loads(row["payload"]))
                for row in page
            )
            position = page[-1]["seq"]
        return events

    def _replay_from_memory(self, cursor: int) -> list[QueryDiscoveryEvent]:
        if cursor == self._cursor:
            return []
        if self._events and self._cursor > cursor >= self._events[0].cursor - 1:
            return [event for event in self._events if event.cursor > cursor]
        return [self.resync_event("cursor_not_retained")]

    async def ensure_started(self) -> None:
        if self._scheduler_owned:
            return
        if self._task is None or self._task.done():
            self._wake.clear()
            self._task = asyncio.create_task(
                self._run(), name=f"query-discovery:{self.target}"
            )

    def request_refresh(self) -> None:
        self._wake.set()

    def _dispose_service(self) -> None:
        """Release the reused service so the next cycle starts fresh."""
        service = self._service
        self._service = None
        if service is None:
            return
        close = getattr(service, "close", None)
        if close is None:
            return
        try:
            close()
        except Exception:
            logger.debug(
                "Failed to close the discovery service for target %s",
                self.target,
                exc_info=True,
            )

    def _dispose_connection(self) -> None:
        """Release the reused direct connection so the next cycle reconnects."""
        connection = self._connection
        self._connection = None
        self._stats_source = None
        if connection is None:
            return
        try:
            connection.close()
        except Exception:
            logger.debug(
                "Failed to close the discovery connection for target %s",
                self.target,
                exc_info=True,
            )

    async def close(self) -> None:
        task = self._task
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            if self._task is task:
                self._task = None
            self._wake.clear()
        self._dispose_service()
        self._dispose_connection()

    async def subscribe(
        self, after_cursor: Optional[int] = None
    ) -> AsyncGenerator[QueryDiscoveryEvent, None]:
        queue: asyncio.Queue[QueryDiscoveryEvent] = asyncio.Queue(
            maxsize=self._queue_maxsize
        )
        first_subscriber = not self._subscribers
        self._subscribers.add(queue)
        if first_subscriber and self._subscriber_state_callback is not None:
            try:
                self._subscriber_state_callback(self.target, True)
            except Exception:
                logger.warning(
                    "Failed to notify scheduler of subscriber for target %s",
                    self.target,
                    exc_info=True,
                )
        replay = self.replay_after(after_cursor)
        if not self._scheduler_owned:
            await self.ensure_started()
        delivered_cursor = after_cursor if after_cursor is not None else -1
        try:
            for event in replay:
                # A resync resets the client to the current seq; a foreign
                # cursor larger than it must not mask the events that follow.
                if event.event == "resync":
                    delivered_cursor = event.cursor
                else:
                    delivered_cursor = max(delivered_cursor, event.cursor)
                yield event
            while True:
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=BOOKMARK_INTERVAL_SECONDS
                    )
                except asyncio.TimeoutError:
                    yield self.bookmark_event()
                    continue
                # A collection can finish between subscribing and replaying.
                # In that case the same event exists in retained history and
                # the subscriber queue; deliver it only once.
                if event.cursor <= delivered_cursor:
                    continue
                delivered_cursor = event.cursor
                yield event
        finally:
            self._subscribers.discard(queue)
            if not self._subscribers and self._subscriber_state_callback is not None:
                try:
                    self._subscriber_state_callback(self.target, False)
                except Exception:
                    logger.warning(
                        "Failed to notify scheduler of unsubscribe for target %s",
                        self.target,
                        exc_info=True,
                    )
            # Discovery is app-owned and target-scoped. Stop polling as soon as
            # the last Web/Desktop transport leaves this target so a target
            # switch cannot accumulate invisible background collectors.
            if not self._subscribers and not self._scheduler_owned:
                await self.close()

    async def _run(self) -> None:
        while not self._scheduler_owned:
            try:
                await self.collect_now()
            except asyncio.CancelledError:
                raise
            except LeaseLostError:
                # A fenced cycle lost its lease: the store already rejected
                # the remaining writes, and publishing the error would be
                # rejected the same way. The scheduler owns the backoff.
                logger.warning(
                    "Query discovery cycle for target %s lost its lease",
                    self.target,
                )
            except Exception as exc:
                logger.warning(
                    "Automatic query discovery cycle failed for target %s: %s",
                    self.target,
                    type(exc).__name__,
                    exc_info=True,
                )
                self._dispose_service()
                self._dispose_connection()
                self._record_collector_state(
                    state="unavailable",
                    last_attempt_at=self._unix_clock(),
                    error_code="collection_failed",
                    source_capabilities=self._fallback_capabilities(),
                )
                self._snapshot = {
                    **self._snapshot,
                    "state": "unavailable",
                    "changed": False,
                    "error": "Query discovery is temporarily unavailable.",
                }
                await self.publish_async("discovery_error", self._snapshot)
            try:
                await asyncio.wait_for(
                    self._wake.wait(), timeout=self.interval_seconds
                )
                self._wake.clear()
            except asyncio.TimeoutError:
                pass

    def _record_collector_state(
        self,
        *,
        state: str,
        last_attempt_at: int,
        last_success_at: Optional[int] = None,
        duration_ms: Optional[int] = None,
        error_code: Optional[str] = None,
        epoch_id: str = "",
        source_capabilities: Optional[Dict[str, Any]] = None,
    ) -> None:
        if self._store is None:
            return
        try:
            # The legacy path carries no epoch; two-phase cycles pass the
            # real epoch_id plus the source's capability payload.
            self._store.upsert_collector_state(
                self.target,
                state=state,
                last_attempt_at=last_attempt_at,
                last_success_at=last_success_at,
                duration_ms=duration_ms,
                error_code=error_code,
                epoch_id=epoch_id,
                source_capabilities=source_capabilities,
                **self._fence_kwargs(),
            )
        except LeaseLostError:
            raise
        except Exception:
            logger.warning(
                "Failed to persist collector state for target %s",
                self.target,
                exc_info=True,
            )

    def _record_counter_snapshots(
        self, queries: list[TopQueryData], captured_at: float
    ) -> None:
        if self._store is None or not queries:
            return
        # Provisional mapping until two-phase collection lands: engine_key is
        # the upstream query hash, calls mirrors the cumulative freq counter,
        # and durations are parsed back from the display strings TopService
        # emits (values in milliseconds).
        rows = [
            {
                "engine_key": query.query_hash,
                "calls": query.freq,
                "total_exec_time": _duration_ms(query.total_time),
                "mean_exec_time": _duration_ms(query.avg_time),
                "max_exec_time": query.max_duration_ms,
            }
            for query in queries
            if query.query_hash
        ]
        try:
            self._store.record_counter_snapshots(
                self.target, "", rows, captured_at=captured_at, **self._fence_kwargs()
            )
        except LeaseLostError:
            raise
        except Exception:
            logger.warning(
                "Failed to persist counter snapshots for target %s",
                self.target,
                exc_info=True,
            )

    def _record_counter_rows(
        self, rows: list[CounterRow], epoch_id: str, captured_at: float
    ) -> None:
        if self._store is None or not rows:
            return
        payload = [
            {
                "engine_key": row.engine_key,
                "calls": row.calls,
                "rows": row.rows,
                "total_exec_time": row.total_exec_time,
                "mean_exec_time": row.mean_exec_time,
                "min_exec_time": row.min_exec_time,
                "max_exec_time": row.max_exec_time,
                "stats_since": (
                    int(row.stats_since) if row.stats_since is not None else None
                ),
            }
            for row in rows
        ]
        try:
            self._store.record_counter_snapshots(
                self.target,
                epoch_id,
                payload,
                captured_at=captured_at,
                **self._fence_kwargs(),
            )
        except LeaseLostError:
            raise
        except Exception:
            logger.warning(
                "Failed to persist counter snapshots for target %s",
                self.target,
                exc_info=True,
            )

    def _record_identity_aliases(self, epoch_id: str, aliases: Dict[str, str]) -> None:
        if self._store is None or not aliases:
            return
        try:
            self._store.record_identity_aliases(
                self.target, epoch_id, aliases, **self._fence_kwargs()
            )
        except LeaseLostError:
            raise
        except Exception:
            logger.warning(
                "Failed to persist identity aliases for target %s",
                self.target,
                exc_info=True,
            )

    def _record_recent_observations(self, rows: list[Dict[str, Any]]) -> None:
        if self._store is None or not rows:
            return
        try:
            self._store.record_recent_observations(
                self.target, rows, **self._fence_kwargs()
            )
        except LeaseLostError:
            raise
        except Exception:
            logger.warning(
                "Failed to persist recent observations for target %s",
                self.target,
                exc_info=True,
            )

    def _load_persisted_aliases(self, epoch_id: str) -> Dict[str, str]:
        if self._store is None:
            return {}
        try:
            return self._store.get_identity_aliases(self.target, epoch_id)
        except Exception:
            logger.warning(
                "Failed to read identity aliases for target %s",
                self.target,
                exc_info=True,
            )
            return {}

    def _restore_cursor_state(self) -> None:
        """Adopt the persisted cursor and baseline, once per collector.

        The cursor is only valid together with the baseline it was advanced
        from: restoring it alone would turn post-restart incremental rows
        into a bogus new baseline. Without a stored same-epoch baseline the
        cursor is discarded and the next fetch is a full sweep. The cursor
        also validates itself against the live server (MySQL carries
        server_start inside it), so a stale value degrades to a full sweep.
        """
        if self._cursor_restored or self._store is None:
            return
        self._cursor_restored = True
        try:
            state = self._store.get_collector_state(self.target)
        except Exception:
            logger.warning(
                "Failed to read collector state for target %s",
                self.target,
                exc_info=True,
            )
            return
        capabilities = (state or {}).get("source_capabilities") or {}
        cursor = capabilities.get("cursor_state")
        epoch_id = (state or {}).get("epoch_id") or ""
        baseline = self._load_persisted_baseline(epoch_id)
        if baseline:
            # A restored same-epoch baseline turns the first post-restart
            # sweep into valid deltas, so calls made during the restart gap
            # surface instead of re-baselining.
            self._baseline = baseline
            self._baseline_captured_at = max(
                row.captured_at for row in baseline.values()
            )
            self._baseline_epoch = epoch_id
            if isinstance(cursor, dict):
                self._cursor_state = cursor

    def _load_persisted_baseline(self, epoch_id: str) -> Dict[str, CounterRow]:
        if self._store is None or not epoch_id:
            return {}
        try:
            rows = self._store.latest_counter_snapshots(self.target, epoch_id)
        except Exception:
            logger.warning(
                "Failed to read stored counter snapshots for target %s",
                self.target,
                exc_info=True,
            )
            return {}
        return {
            row["engine_key"]: CounterRow(
                engine_key=row["engine_key"],
                calls=int(row["calls"] or 0),
                rows=int(row["rows"] or 0),
                total_exec_time=float(row["total_exec_time"] or 0.0),
                mean_exec_time=float(row["mean_exec_time"] or 0.0),
                min_exec_time=float(row["min_exec_time"] or 0.0),
                max_exec_time=float(row["max_exec_time"] or 0.0),
                captured_at=float(row["captured_at"]),
                epoch_id=epoch_id,
                stats_since=(
                    float(row["stats_since"])
                    if row["stats_since"] is not None
                    else None
                ),
            )
            for row in rows
        }

    def _timed_executor(self, connection: Any) -> Callable[..., Any]:
        execute = _connection_executor(connection)

        def timed(sql: str, params: Any = None) -> Any:
            started = time.monotonic()
            try:
                return execute(sql, params)
            finally:
                self._db_touch_seconds += time.monotonic() - started

        return timed

    def _engine_total_delta(self, result: CollectResult) -> Optional[tuple[int, float]]:
        """Engine-wide (calls, ms) since the previous reading, when both readings share an
        epoch and no statement was evicted in between; None otherwise."""
        previous = getattr(self, "_engine_totals", None)
        dealloc = result.capabilities.get("dealloc")
        self._engine_totals = ((result.epoch_id, dealloc, result.totals)
                               if result.totals is not None else None)
        if previous is None or result.totals is None or previous[:2] != (result.epoch_id, dealloc):
            return None
        calls = result.totals[1] - previous[2][1]
        total_ms = result.totals[2] - previous[2][2]
        return (calls, total_ms) if calls >= 0 and total_ms >= 0 else None

    def _unresolved_batch(self, rows: list[CounterRow]) -> list[CounterRow]:
        """The engine rows still lacking an identity that this cycle resolves: the heaviest
        IDENTITY_RESOLUTION_BATCH of them, so a newly seen engine with a hundred thousand
        statements is resolved over several cycles instead of one long one."""
        unresolved = [row for row in rows if row.engine_key not in self._alias_map]
        limit = (min(IDENTITY_RESOLUTION_BATCH, STARTUP_IDENTITY_RESOLUTION_BATCH)
                 if self._monitor_enabled and not self._monitor_baseline_set
                 else IDENTITY_RESOLUTION_BATCH)
        # Heaviest first, so a cycle cut short by the time budget resolves what matters.
        return heapq.nlargest(min(limit, len(unresolved)), unresolved,
                              key=lambda row: row.total_exec_time)

    def _two_phase_fetch(
        self,
    ) -> tuple[CollectResult, Dict[str, str], list[str]]:
        """Blocking Phase A (plus Phase B when keys lack text) on the reused
        connection, then one bounded activity snapshot for parameter values."""
        self._db_touch_seconds = 0.0
        if self._connection is None:
            connect_started = time.monotonic()
            connection, engine = self._connection_factory(self.target)
            self._db_touch_seconds += time.monotonic() - connect_started
            self._connection = connection
            self._engine = engine
            if engine == "postgresql":
                self._stats_source = PgStatStatementsSource()
                self._source_name = "pg_stat"
            elif engine == "mysql":
                self._stats_source = MysqlDigestSource()
                self._source_name = "digest"
            else:
                self._connection = None
                try:
                    connection.close()
                except Exception:
                    pass
                raise ValueError(
                    f"unsupported engine for two-phase discovery: {engine!r}"
                )
        connection = self._connection
        source = self._stats_source
        executor = self._timed_executor(connection)

        try:
            result = source.collect(
                executor,
                cursor_state=self._cursor_state,
                known_text_keys=frozenset(self._alias_map),
                tracked_keys=tuple(self._baseline),
            )
            if result.epoch_id != self._alias_epoch:
                self._alias_map = self._load_persisted_aliases(result.epoch_id)
                self._alias_epoch = result.epoch_id
            texts = dict(result.texts)
            needed = {
                row.engine_key for row in self._unresolved_batch(result.rows)
                if row.engine_key not in texts
            }
            texts.update(self._fetch_statement_texts(needed))
            if self._monitor_enabled and self._monitor is not None:
                # Query Library admission is intentionally capped per pass, but
                # monitor history needs text for every observed identity. On a
                # restart, backfill older aliases that predate this sink path.
                try:
                    known = {row["query_key"] for row in self._monitor.read(
                        "SELECT query_key FROM query_text WHERE target=?", (self.target,))}
                    missing_monitor = sorted(
                        row.engine_key for row in result.rows
                        if row.engine_key not in texts
                        and (key := self._alias_map.get(row.engine_key)) is not None
                        and key not in known and key not in self._system_identities
                    )
                    texts.update(self._fetch_statement_texts(set(missing_monitor[:500])))
                except Exception:
                    logger.warning("Monitor text backfill failed for %s", self.target,
                                   exc_info=True)
            # The activity snapshot only recovers parameter values, so its
            # failure (privileges, timeout) never fails the cycle -- the
            # same non-fatal posture as every store write.
            activity_texts: list[str] = []
            self._activity_sample_ok = False
            source.last_activity_details = []
            try:
                activity_texts = source.activity_sample(executor)
                self._activity_sample_ok = True
            except Exception:
                logger.debug(
                    "Activity sampling failed for target %s; the cycle "
                    "continues without parameter capture",
                    self.target,
                    exc_info=True,
                )
            self._monitor_db_sample = None
            if self._monitor_enabled:
                try:
                    if self._engine == "postgresql":
                        rows = executor(PG_MONITOR_DB_SQL, None)
                        self._monitor_db_sample = tuple(rows[0]) if rows else None
                    else:
                        self._monitor_db_sample = mysql_monitor_sample(executor, result.epoch_id)
                except Exception:
                    self._monitor_db_sample = None
                    logger.debug("Monitor database sample failed for %s", self.target, exc_info=True)
            return result, texts, activity_texts
        finally:
            # End the read transaction so the reused connection idles between
            # cycles (SET LOCAL scoping relies on it on PostgreSQL).
            if self._engine == "postgresql":
                rollback_started = time.monotonic()
                try:
                    connection.rollback()
                except Exception:
                    logger.debug(
                        "Rollback after collection failed for target %s",
                        self.target,
                        exc_info=True,
                    )
                finally:
                    self._db_touch_seconds += time.monotonic() - rollback_started

    def _fetch_statement_texts(self, needed: set[str]) -> Dict[str, str]:
        """Blocking Phase B: fetch statement text for exactly these engine keys.

        Placeholder texts (e.g. '<insufficient privilege>') are dropped so
        the key stays unresolved and is retried on a later cycle.
        """
        connection = self._connection
        source = self._stats_source
        if not needed or connection is None or not isinstance(
            source, PgStatStatementsSource
        ):
            return {}
        executor = self._timed_executor(connection)
        texts: Dict[str, str] = {}
        try:
            for statement in source.text_fetch_setup_statements:
                executor(statement)
            version = int(executor(PG_VERSION_SQL)[0][0])
            for sql, params in source.text_fetch_sql(needed, version):
                for raw in executor(sql, params):
                    if version >= 140000:
                        userid, dbid, queryid, toplevel, text = raw
                        key = pg_engine_key(
                            userid, dbid, queryid, _toplevel_flag(toplevel)
                        )
                    else:
                        userid, dbid, queryid, text = raw
                        key = pg_engine_key(userid, dbid, queryid, "t")
                    usable = _usable_statement_text(text)
                    if key in needed and usable is not None:
                        texts[key] = usable
            return texts
        finally:
            rollback_started = time.monotonic()
            try:
                connection.rollback()
            except Exception:
                logger.debug(
                    "Rollback after text fetch failed for target %s",
                    self.target,
                    exc_info=True,
                )
            finally:
                self._db_touch_seconds += time.monotonic() - rollback_started

    def _recent_observation_rows(
        self, deltas: list[DeltaResult], *, incremental: bool
    ) -> list[Dict[str, Any]]:
        """Window results keyed by library identity; engine keys without one are dropped."""
        tiers = None
        coarse = {}
        if self._monitor_enabled and self._monitor is not None:
            tiers = {row["query_key"]: row["rank"] for row in self._monitor.read(
                "SELECT query_key,rank FROM query_tier WHERE target=?", (self.target,))}
            coarse = {key: row for key, row in getattr(self, "_coarse_observations", {}).items()
                      if tiers.get(key, 0) > FULL_DETAIL_QUERIES}
        merged: Dict[str, Dict[str, Any]] = {}
        for delta in deltas:
            if delta.is_baseline:
                continue
            if incremental and delta.completeness == ABSENT:
                # An incremental fetch omits idle rows; absence is not evidence.
                continue
            normalized = self._alias_map.get(delta.engine_key)
            if normalized is None:
                logger.debug(
                    "Dropping a window delta for engine key %s: no resolved identity yet",
                    delta.engine_key,
                )
                continue
            if tiers is not None and normalized not in tiers:
                continue
            row = {
                "normalized_hash": normalized,
                "window_start": delta.window_start,
                "window_end": delta.window_end,
                "calls_delta": delta.calls_delta,
                "exec_time_delta": delta.exec_time_delta,
                "approximate_qps": delta.approximate_qps,
                "completeness": delta.completeness,
            }
            current = merged.get(normalized)
            if current is None:
                merged[normalized] = row
            elif current["completeness"] == COMPLETE and delta.completeness == COMPLETE:
                # Several engine keys (e.g. per-user rows) share one identity.
                for field in ("calls_delta", "exec_time_delta", "approximate_qps"):
                    current[field] += row[field]
            elif current["completeness"] == COMPLETE:
                # One incomplete engine row makes the merged window incomplete;
                # its metrics are already None.
                merged[normalized] = row
        if tiers is not None:
            completed = []
            for key, row in merged.items():
                if tiers[key] <= FULL_DETAIL_QUERIES:
                    completed.append(row)
                    continue
                prior = coarse.get(key)
                if prior is not None:
                    row["window_start"] = min(prior["window_start"], row["window_start"])
                    if prior["completeness"] == COMPLETE and row["completeness"] == COMPLETE:
                        row["calls_delta"] += prior["calls_delta"]
                        row["exec_time_delta"] += prior["exec_time_delta"]
                        row["approximate_qps"] = row["calls_delta"] / max(
                            1, row["window_end"] - row["window_start"])
                    else:
                        row.update(calls_delta=None, exec_time_delta=None, approximate_qps=None,
                                   completeness=prior["completeness"] if prior["completeness"] != COMPLETE
                                   else row["completeness"])
                if row["window_end"] - row["window_start"] >= 300:
                    completed.append(row)
                    coarse.pop(key, None)
                else:
                    coarse[key] = row
            self._coarse_observations = coarse
            return completed
        return list(merged.values())

    def _apply_rdst_attribution(
        self, rows: list[Dict[str, Any]]
    ) -> list[Dict[str, Any]]:
        """Attribute RDST self-traffic inside each valid window, per Q11(3):

        - no overlapping self-execution: the row stays production_only;
        - every overlap carries an exact exec_count wholly contained in this
          window: the row is marked
          contains_rdst_traffic and calls_delta becomes the production
          share, floored at zero, with approximate_qps recomputed over
          the same window; exec_time_delta stays as measured because
          execution time cannot be attributed exactly;
        - any overlap with an unknown count, or an exact aggregate spanning a
          window boundary: the row is marked
          contains_rdst_traffic and completeness is downgraded to
          partial without subtracting a guess.

        Windows carry server-clock timestamps while rdst_execution rows carry
        local time, so the bounds are shifted by the cycle's measured clock
        offset (and widened by ATTRIBUTION_SKEW_ALLOWANCE_SECONDS) before one
        batched overlap read serves the whole cycle. The subtraction and
        completeness rules apply only to overlaps of the shifted, unwidened
        window; an overlap admitted only by the allowance is near-boundary
        evidence that marks the row and nothing else, so a point bucket on a
        shared boundary still belongs to exactly one adjacent window.
        """
        if self._store is None:
            return rows
        pending = [row for row in rows if row["calls_delta"] is not None]
        if not pending:
            return rows
        offset = self._clock_offset
        windows = [
            (
                row["normalized_hash"],
                row["window_start"] - offset - ATTRIBUTION_SKEW_ALLOWANCE_SECONDS,
                row["window_end"] - offset + ATTRIBUTION_SKEW_ALLOWANCE_SECONDS,
            )
            for row in pending
        ]
        try:
            overlap_map = self._store.rdst_executions_overlapping_many(
                self.target, windows
            )
        except Exception:
            logger.debug(
                "RDST attribution lookup failed for target %s; "
                "leaving the cycle's windows unattributed",
                self.target,
                exc_info=True,
            )
            return rows
        for row, window in zip(pending, windows):
            overlaps = overlap_map.get(window) or []
            if not overlaps:
                continue
            row["attribution"] = "contains_rdst_traffic"
            local_start = row["window_start"] - offset
            local_end = row["window_end"] - offset
            in_window = [
                overlap
                for overlap in overlaps
                if overlap["started_at"] <= local_end
                and (
                    overlap["ended_at"] is None
                    or overlap["ended_at"] > local_start
                )
            ]
            if not in_window:
                continue
            counts = [overlap["exec_count"] for overlap in in_window]
            # An exact aggregate spanning a counter boundary cannot be split
            # truthfully between the adjacent windows.  Do not subtract the
            # full count from either one; preserve the signal as partial.
            fully_contained = all(
                overlap["started_at"] > local_start
                and overlap["ended_at"] is not None
                and overlap["ended_at"] <= local_end
                for overlap in in_window
            )
            if any(count is None for count in counts) or not fully_contained:
                row["completeness"] = "partial"
                continue
            adjusted = max(0, row["calls_delta"] - sum(counts))
            row["calls_delta"] = adjusted
            row["approximate_qps"] = adjusted / max(
                row["window_end"] - row["window_start"], 1.0
            )
        return rows

    async def _resolve_candidate_texts(
        self,
        candidates: list[str],
        identity_rows: Dict[str, list[CounterRow]],
        texts: Dict[str, str],
        registry: QueryRegistry,
    ) -> Dict[str, str]:
        """Text for each top-K identity: this sweep's texts, then the
        library, then one bounded extra Phase B fetch for the few keys
        still missing (at most the top-K identities' keys per cycle)."""
        resolved: Dict[str, str] = {}
        missing: Dict[str, str] = {}
        for normalized in candidates:
            text: Optional[str] = None
            for row in identity_rows[normalized]:
                text = texts.get(row.engine_key)
                if text is not None:
                    break
            if text is None:
                entry = registry.get_query(normalized)
                if entry is not None and entry.original_sql:
                    text = entry.original_sql
            if text is not None:
                resolved[normalized] = text
            else:
                for row in identity_rows[normalized]:
                    missing[row.engine_key] = normalized
        if missing:
            try:
                fetched = await asyncio.to_thread(
                    self._fetch_statement_texts, set(missing)
                )
            except Exception:
                logger.debug(
                    "Late-admission text fetch failed for target %s",
                    self.target,
                    exc_info=True,
                )
                fetched = {}
            for engine_key, text in fetched.items():
                resolved.setdefault(missing[engine_key], text)
        return resolved

    @staticmethod
    def _sample_text_for(
        normalized: str,
        identity_rows: Dict[str, list[CounterRow]],
        sample_texts: Dict[str, str],
    ) -> Optional[str]:
        """One literal-bearing sample for the identity, from this sweep's rows.

        Identity stays keyed on the engine's normalized text (the sample's
        literals hash differently), so the sample only feeds observed
        parameter values through add_query's observed_params_sql. PostgreSQL
        never yields samples here: pg_stat_statements stores normalized text
        only, so PG observed values come from the cycle's pg_stat_activity
        snapshot until the adapter telemetry lane provides exact ones.
        """
        for row in identity_rows.get(normalized, ()):
            sample = sample_texts.get(row.engine_key)
            if sample:
                return sample
        return None

    def _pg_target_reset(self, rows: list[CounterRow]) -> bool:
        """Detect a selective PG stats reset that leaves the global epoch unchanged."""
        if self._engine != "postgresql" or self._baseline_captured_at is None:
            return False
        previous = {key: row for key, row in self._baseline.items()
                    if row.captured_at == self._baseline_captured_at}
        if len(previous) < 100:
            return False
        current = {row.engine_key: row for row in rows}
        shared = set(previous) & set(current)
        if len(shared) * 2 >= len(previous):
            return False
        if len(shared) < 10:
            return True
        regressions = sum(
            current[key].calls < previous[key].calls or
            current[key].total_exec_time < previous[key].total_exec_time
            for key in shared)
        return regressions >= max(5, len(shared) // 5)

    async def _collect_two_phase(self) -> Optional[QueryDiscoveryEvent]:
        """One two-phase cycle, or None when a capability gap engaged the
        sticky legacy fallback and the caller should finish the cycle there."""
        collection_started = time.monotonic()
        attempted_at = self._unix_clock()
        connection_reused = self._connection is not None
        self._restore_cursor_state()
        self._monitor_enabled = False
        monitor_path = default_monitor_db_path()
        if monitor_path.exists():
            try:
                if (
                    self._monitor is None
                    or str(self._monitor.path) != os.path.realpath(monitor_path)
                ):
                    self._monitor = MonitorStore(monitor_path)
                self._monitor_enabled = self._monitor.levers(self.target)["enabled"]
            except Exception:
                logger.warning("Monitor store unavailable for %s", self.target, exc_info=True)

        fetch_started = time.monotonic()
        try:
            result, texts, activity_texts = await asyncio.to_thread(
                self._two_phase_fetch
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("Two-phase query discovery failed", exc_info=True)
            self._dispose_connection()
            capability_code = _capability_error_code(exc)
            if capability_code is not None:
                logger.warning(
                    "Two-phase discovery is unavailable for target %s (%s: %s); "
                    "switching this collector to the legacy collection path",
                    self.target,
                    capability_code,
                    type(exc).__name__,
                )
                self._legacy_fallback = True
                self._source_unavailable = {
                    "error_code": capability_code,
                    "error": type(exc).__name__,
                }
                return None
            logger.info(
                "Query discovery failed for target %s: %s",
                self.target,
                type(exc).__name__,
            )
            await asyncio.to_thread(
                self._record_collector_state, state="unavailable", last_attempt_at=attempted_at,
                duration_ms=int((time.monotonic() - collection_started) * 1000),
                error_code="collection_failed",
            )
            self._snapshot = {
                **self._snapshot,
                "state": "unavailable",
                "changed": False,
                "error": "Query discovery is temporarily unavailable.",
            }
            return await self.publish_async("discovery_error", self._snapshot)
        fetch_ms = (time.monotonic() - fetch_started) * 1000.0
        local_fetched_at = self._local_clock()

        def advance() -> tuple[Dict[str, CounterRow], float, Dict[str, str], bool, bool,
                               list[DeltaResult], Dict[str, CounterRow], list[CounterRow],
                               Optional[tuple[int, float]], Optional[float]]:
            rows_by_key = {row.engine_key: row for row in result.rows}
            captured_at = result.rows[0].captured_at if result.rows else float(attempted_at)
            if result.rows:
                # Both clocks were just read around the same fetch: captured_at
                # is the server's now() and local_fetched_at the local one.
                self._clock_offset = captured_at - local_fetched_at

            # Identity resolution: alias newly texted keys with the same hash the
            # registry computes, so both lanes agree on one identity. Missing or
            # placeholder text leaves the key unresolved so it is retried; the
            # aliases are persisted after registry validation below.
            new_aliases: Dict[str, str] = {}
            resolve_deadline = time.monotonic() + IDENTITY_RESOLUTION_BUDGET_SECONDS
            for row in self._unresolved_batch(result.rows):
                if time.monotonic() >= resolve_deadline:
                    break
                engine_key = row.engine_key
                text = texts.get(engine_key)
                if text is None:
                    continue
                usable = _usable_statement_text(text)
                if usable is None:
                    continue
                try:
                    new_aliases[engine_key] = hash_sql(usable)
                except Exception:
                    logger.debug(
                        "Failed to hash statement text for engine key %s",
                        engine_key,
                        exc_info=True,
                    )
            self._alias_map.update(new_aliases)

            # Delta lane: diffing is valid only within one epoch; a change resets
            # the baseline and cursor and produces no deltas this cycle.
            target_reset = (self._baseline_epoch == result.epoch_id
                            and self._pg_target_reset(result.rows))
            epoch_changed = bool(self._baseline) and (
                self._baseline_epoch != result.epoch_id or target_reset)
            if target_reset and self._store is not None:
                try:
                    self._store.clear_counter_snapshots(
                        self.target, result.epoch_id, **self._fence_kwargs())
                except LeaseLostError:
                    raise
                except Exception:
                    logger.warning("Failed to clear reset counters for %s", self.target,
                                   exc_info=True)
            deltas: list[DeltaResult] = []
            if epoch_changed:
                self._baseline = {}
                self._baseline_captured_at = None
                self._cursor_state = None
            elif self._baseline and self._baseline_captured_at is not None:
                deltas = process_snapshot(
                    self._baseline, result.rows, self._baseline_captured_at, captured_at
                )
            prior_baseline = dict(self._baseline)
            baseline_rows = result.rows
            if self._monitor_enabled and self._monitor is not None:
                tier_rows = self._monitor.read(
                    "SELECT query_key,rank FROM query_tier WHERE target=?", (self.target,))
                ranks = {row["query_key"]: row["rank"] for row in tier_rows}
                # Retained queries need a counter pair even when the native sweep exceeds the cap.
                baseline_rows = sorted(result.rows, key=lambda row: (
                    -ranks.get(self._alias_map.get(row.engine_key), SERIES_QUERIES + 1),
                    row.total_exec_time))
            previous_capture = self._baseline_captured_at
            # Tracked queries keep their pairs; the headroom pairs the heaviest newcomers, so a
            # newly hot statement gets a delta and can earn a tracked slot.
            self._baseline = advance_baseline(self._baseline, baseline_rows,
                                              max_keys=SERIES_QUERIES + COLLECT_ENTRANT_STATEMENTS)
            self._baseline_captured_at = captured_at
            self._baseline_epoch = result.epoch_id
            self._cursor_state = result.next_cursor_state
            # Only baseline rows can pair with a later sweep, and a row whose counters match its
            # stored checkpoint adds nothing a restart could use, so neither is rewritten.
            checkpoint = [row for row in result.rows if self._baseline.get(row.engine_key) is row
                          and _counters_moved(prior_baseline.get(row.engine_key), row)]
            # A full sweep re-reads every held pair, so a pair whose window starts before the
            # previous sweep left the slice in between: that window is a gap, not a minute.
            contiguous = previous_capture if not result.incremental else None
            return (rows_by_key, captured_at, new_aliases, target_reset, epoch_changed, deltas,
                    prior_baseline, checkpoint, self._engine_total_delta(result), contiguous)

        # Hashing new texts and diffing a full native sweep are CPU work over every
        # engine row; they run off the event loop so API requests keep being served.
        (rows_by_key, captured_at, new_aliases, target_reset, epoch_changed, deltas,
         prior_baseline, checkpoint, engine_delta, contiguous) = await asyncio.to_thread(advance)

        # Commit the counter checkpoint before its delta reaches monitor.db,
        # so a restart cannot count the same window twice.
        await asyncio.to_thread(
            self._record_counter_rows, checkpoint, result.epoch_id, captured_at=captured_at
        )

        # Preserve the minute bucket before any Library parsing or admission.
        # The monitor owns its text and identity history independently.
        if not self._monitor_enabled:
            self._monitor_baseline_set = False
        if self._monitor_enabled and self._monitor is not None:
            # The first monitored reading's deltas reach back to a snapshot taken before
            # monitoring started (or before a restart), over an interval the chart can't
            # know. That reading only sets the baseline; rates start with the next one.
            baseline = not self._monitor_baseline_set
            self._monitor_baseline_set = True

            def record_monitor() -> None:
                with self._registry_write_guard():
                    self._record_monitor_cycle(
                        int(local_fetched_at), deltas, None,
                        getattr(self._stats_source, "last_activity_details", []),
                        result.rows, prior_baseline, result.sample_texts, texts,
                        activity_sampled=getattr(self, "_activity_sample_ok", False),
                        baseline=baseline, engine_delta=engine_delta,
                        contiguous_since=contiguous,
                    )

            try:
                await asyncio.to_thread(record_monitor)
            except LeaseLostError:
                raise
            except Exception:
                logger.warning("Monitor sink failed for %s", self.target, exc_info=True)
            else:
                await asyncio.to_thread(self._name_new_queries)

        def load_registry() -> tuple[QueryRegistry, set[str]]:
            registry = self._registry()
            return registry, registry.observed_hashes(self.target)

        registry, previously_observed = await asyncio.to_thread(load_registry)

        # Identity-level aggregation over the merged baseline: absent engine
        # keys keep their last counters, so an incremental sweep never
        # shrinks an identity's cumulative evidence. Absent identities can
        # be admitted to the Library when they reappear with fetchable text.
        identity_rows: Dict[str, list[CounterRow]] = {}
        for engine_key, counter_row in self._baseline.items():
            normalized = self._alias_map.get(engine_key)
            if normalized is not None:
                identity_rows.setdefault(normalized, []).append(counter_row)

        delta_by_key = {delta.engine_key: delta for delta in deltas}

        def interval_rank(normalized: str) -> float:
            windows = [
                delta_by_key[row.engine_key].exec_time_delta
                for row in identity_rows[normalized]
                if row.engine_key in delta_by_key
                and delta_by_key[row.engine_key].exec_time_delta is not None
            ]
            if windows:
                return sum(windows)
            return sum(row.total_exec_time for row in identity_rows[normalized])

        # Library adds stay bounded. Retained baselines include absent engine
        # keys after eviction or a selective reset; those keys have no text
        # to fetch and must not occupy every top-K admission slot.
        present = set(rows_by_key)
        admission_limit = LIBRARY_ADMISSION_LIMIT
        refresh_limit = LIBRARY_REFRESH_LIMIT
        tracked = None
        if self._monitor_enabled and self._monitor is not None:
            tracked = {row["query_key"] for row in await asyncio.to_thread(self._monitor.read,
                "SELECT query_key FROM query_text WHERE target=? UNION "
                "SELECT query_key FROM query_load WHERE target=?", (self.target, self.target))}
        candidates = sorted(
            (
                normalized
                for normalized in identity_rows
                if normalized not in previously_observed
                and (tracked is None or normalized in tracked)
                and normalized not in self._system_identities
                and any(row.engine_key in present for row in identity_rows[normalized])
            ),
            key=interval_rank,
            reverse=True,
        )[: min(self.limit, admission_limit)]

        # Refresh a rotating slice of present Library identities. New
        # identities retain their separate top-K admission budget.
        known = sorted(
            key for key in identity_rows
            if key in previously_observed and key not in self._system_identities
            and any(row.engine_key in present for row in identity_rows[key])
        )
        if known:
            offset = self._library_refresh_cursor % len(known)
            ordered = known[offset:] + known[:offset]
            refresh_keys = set(ordered[:refresh_limit])
            self._library_refresh_cursor = (offset + len(refresh_keys)) % len(known)
        else:
            refresh_keys = set()

        def scope_registry() -> Dict[str, str]:
            # The cycle reads and writes only these identities; the Library can be far
            # larger than one cycle's work, so it is never decoded whole here.
            captures = _activity_capture_map(activity_texts)
            registry.load_keys([*candidates, *refresh_keys, *captures])
            return captures

        activity_captures = await asyncio.to_thread(scope_registry)
        candidate_texts = await self._resolve_candidate_texts(
            candidates, identity_rows, texts, registry
        )

        def admit() -> tuple[list[str], bool, int, int, int, float]:
            new_hashes: list[str] = []
            changed = False
            refreshed = 0
            params_captured = 0
            system_skipped = 0
            sql_dialect = "postgres" if self._engine == "postgresql" else self._engine
            # Only MySQL texts need their dialect in the registry: DIGEST_TEXT
            # backtick-quotes every identifier, which the dialect-less parser
            # rejects. PostgreSQL must stay dialect-less because the postgres
            # parser reads the digits of $N placeholders as literals.
            add_dialect = "mysql" if self._engine == "mysql" else None
            persist_started = time.monotonic()
            with registry.defer_save():
                for normalized in candidates:
                    text = candidate_texts.get(normalized)
                    if text is None:
                        # No usable text this cycle; the identity is retried as
                        # long as it keeps ranking into the top-K.
                        continue
                    if not references_user_relations(text, dialect=sql_dialect):
                        # Catalog-only and relation-free statements are RDST's
                        # own observation traffic or probes, not user workload;
                        # they stay in the counter/delta lanes but never enter
                        # the Query Library or future candidate rankings.
                        self._system_identities.add(normalized)
                        system_skipped += 1
                        continue
                    if match_self_template(text):
                        # Statement stores replay RDST's own diagnostic
                        # statements captured before self markers were emitted;
                        # recognizing the shapes here keeps them out of the
                        # library regardless of what the store remembers.
                        self._system_identities.add(normalized)
                        system_skipped += 1
                        continue
                    counter_rows = identity_rows[normalized]
                    calls = sum(item.calls for item in counter_rows)
                    avg_ms = (
                        sum(item.mean_exec_time * item.calls for item in counter_rows)
                        / calls
                        if calls
                        else 0.0
                    )
                    try:
                        query_hash, _ = registry.add_query(
                            sql=text,
                            source="top-historical",
                            frequency=calls,
                            target=self.target,
                            dialect=add_dialect,
                            avg_duration_ms=avg_ms,
                            observed_params_sql=self._sample_text_for(
                                normalized, identity_rows, result.sample_texts
                            ),
                            observed=True,
                            save_intent=False,
                        )
                    except ValueError:
                        logger.debug(
                            "Skipping an invalid query returned by discovery",
                            exc_info=True,
                        )
                        # An alias minted from this cycle's rejected text must
                        # not persist as resolved; drop it so text is retried.
                        for engine_key in [
                            key
                            for key, value in new_aliases.items()
                            if value == normalized
                        ]:
                            del new_aliases[engine_key]
                            self._alias_map.pop(engine_key, None)
                        continue
                    if query_hash not in previously_observed:
                        new_hashes.append(query_hash)
                        previously_observed.add(query_hash)
                        changed = True

                # Known entries receive a rotating evidence refresh: frequency
                # mirrors cumulative calls and avg is the calls-weighted mean.
                # Only first observations become New.
                added = set(new_hashes)
                for normalized, counter_rows in identity_rows.items():
                    if normalized in added or normalized not in refresh_keys:
                        continue
                    entry = registry.get_query(normalized)
                    if entry is None or not entry.original_sql:
                        continue
                    calls = sum(item.calls for item in counter_rows)
                    avg_ms = (
                        sum(item.mean_exec_time * item.calls for item in counter_rows)
                        / calls
                        if calls
                        else 0.0
                    )
                    try:
                        prior_stats = (entry.frequency, entry.avg_duration_ms)
                        query_hash, _ = registry.add_query(
                            sql=entry.original_sql,
                            source="top-historical",
                            frequency=calls,
                            target=self.target,
                            dialect=add_dialect,
                            avg_duration_ms=avg_ms,
                            observed_params_sql=self._sample_text_for(
                                normalized, identity_rows, result.sample_texts
                            ),
                            observed=True,
                            save_intent=False,
                        )
                    except ValueError:
                        logger.debug(
                            "Skipping an evidence refresh for a known entry",
                            exc_info=True,
                        )
                        continue
                    refreshed += 1
                    refreshed_entry = registry.get_query(query_hash)
                    if refreshed_entry is not None and prior_stats != (
                        refreshed_entry.frequency,
                        refreshed_entry.avg_duration_ms,
                    ):
                        changed = True

                # Parameter capture from the cycle's activity snapshot: a sampled
                # literal-bearing text refreshes observed values for an identity
                # already in the Library (including ones admitted above). Texts
                # matching no Library entry are dropped: activity sampling is
                # unrepresentative evidence for admission (research Q7), so this
                # lane captures parameter values, never queries.
                for identity, sampled in activity_captures.items():
                    entry = registry.get_query(identity)
                    if entry is None or not entry.original_sql:
                        continue
                    if _identity_slot_count(entry.sql):
                        # An identity minted from engine-normalized text: the
                        # sample's values are extracted against the stored slot
                        # text so they key by textual slot position ($k / the
                        # k-th '?' resolves as pk).
                        extracted = extract_observed_params(
                            sampled, entry.sql, add_dialect
                        )
                        add_sql, observed_sql = entry.original_sql, sampled
                    else:
                        # An identity minted from literal-bearing SQL: re-adding
                        # the fresh sample extracts values with the same :pN
                        # numbering the entry's stored text carries.
                        try:
                            _, extracted = normalize_and_extract(
                                canonicalize_sql(sampled), add_dialect
                            )
                        except Exception:
                            extracted = {}
                        add_sql, observed_sql = sampled, None
                    if not extracted:
                        # Values are written only when extraction succeeds; a
                        # truncated or unmappable sample must not touch the entry.
                        continue
                    prior_params = dict(entry.most_recent_params)
                    try:
                        query_hash, _ = registry.add_query(
                            sql=add_sql,
                            source="top-historical",
                            target=self.target,
                            dialect=add_dialect,
                            observed_params_sql=observed_sql,
                            observed=True,
                            save_intent=False,
                        )
                    except ValueError:
                        logger.debug(
                            "Skipping parameter capture for a sampled text",
                            exc_info=True,
                        )
                        continue
                    params_captured += 1
                    captured_entry = registry.get_query(query_hash)
                    if (
                        captured_entry is not None
                        and captured_entry.most_recent_params != prior_params
                    ):
                        changed = True
            persistence_ms = (time.monotonic() - persist_started) * 1000.0
            return (new_hashes, changed, refreshed, params_captured, system_skipped,
                    persistence_ms)

        # Parsing and Library writes are CPU and SQLite work: they run off the event loop,
        # so one wide statement never stalls every other request.
        (new_hashes, changed, refreshed, params_captured, system_skipped,
         persistence_ms) = await asyncio.to_thread(admit)

        capabilities: Dict[str, Any] = dict(result.capabilities)
        capabilities["target_counter_reset"] = target_reset
        capabilities["cursor_state"] = result.next_cursor_state
        capabilities["incremental"] = result.incremental

        def record_evidence() -> None:
            self._record_identity_aliases(result.epoch_id, new_aliases)
            self._record_recent_observations(self._apply_rdst_attribution(
                self._recent_observation_rows(deltas, incremental=result.incremental)))
            self._record_collector_state(
                state="watching",
                last_attempt_at=attempted_at,
                last_success_at=attempted_at,
                duration_ms=int((time.monotonic() - collection_started) * 1000),
                epoch_id=result.epoch_id,
                source_capabilities=capabilities,
            )

        await asyncio.to_thread(record_evidence)
        self._snapshot = {
            "target": self.target,
            "state": "watching",
            "updated_at": self._clock(),
            "source": self._source_name,
            "engine": self._engine,
            "query_count": len(result.rows),
            "new_hashes": new_hashes,
            "changed": changed,
            "stats": {
                "collection_duration_ms": round(
                    (time.monotonic() - collection_started) * 1000.0, 3
                ),
                "db_fetch_duration_ms": round(fetch_ms, 3),
                "db_touch_duration_ms": round(self._db_touch_seconds * 1000, 3),
                "rows_returned": len(result.rows),
                "new_identity_count": len(new_hashes),
                "refreshed_identity_count": refreshed,
                "activity_sampled": len(activity_texts),
                "params_captured": params_captured,
                "system_skipped": system_skipped,
                "persistence_ms": round(persistence_ms, 3),
                "service_reused": connection_reused,
                "deltas_computed": len(deltas),
                "epoch_id": result.epoch_id,
                "epoch_changed": epoch_changed,
                "target_counter_reset": target_reset,
                "incremental": result.incremental,
            },
            "error": None,
        }
        return await self.publish_async("discovery_update", self._snapshot)

    def _record_monitor_cycle(
        self, ts: int, deltas: list[DeltaResult], registry: QueryRegistry | None,
        activity: list[dict[str, Any]],
        current_rows: list[CounterRow] | None = None,
        prior_rows: Dict[str, CounterRow] | None = None,
        sample_texts: Dict[str, str] | None = None,
        observed_texts: Dict[str, str] | None = None,
        *, activity_sampled: bool = False, baseline: bool = False,
        engine_delta: Optional[tuple[int, float]] = None,
        contiguous_since: Optional[float] = None,
    ) -> None:
        """Map valid per-key deltas into one-minute history after identity resolution.

        `engine_delta` is the engine-wide (calls, ms) since the previous reading; traffic of
        statements outside the fetched slice is recorded under everything else from it.
        `contiguous_since` drops pairs whose window opened before the previous reading.

        A `baseline` cycle records texts, samples and the reading itself, but no calls or
        database counters: its deltas span an unknown interval.
        """
        # Classify fetched texts first: relation-free and RDST self statements join the
        # system identities before any of their deltas reach monitor history. The set
        # is process-local, so after a restart this is where they are recognized again.
        texts: Dict[str, str] = {}
        known_texts = getattr(self, "_monitor_known_texts", set())
        detailed_keys = {row["query_key"] for row in self._monitor.read(
            "SELECT query_key FROM query_tier WHERE target=?", (self.target,))}
        sample_keys = self._monitor.full_detail_keys(self.target)
        if not detailed_keys:
            sample_keys.update(self._alias_map.get(row.engine_key) for row in
                               sorted(current_rows or [], key=lambda row: -row.total_exec_time)[:1000])
            sample_keys.update(self._alias_map.get(delta.engine_key) for delta in
                               sorted(deltas, key=lambda row: -(row.exec_time_delta or 0))[:1000])
        system_keys: set[str] = set()
        for engine_key, text in (observed_texts or {}).items():
            key = self._alias_map.get(engine_key)
            if key is None or key in self._system_identities or key in known_texts:
                continue
            if (not references_user_relations(
                    text, dialect="postgres" if self._engine == "postgresql"
                    else self._engine) or match_self_template(text)):
                self._system_identities.add(key)
                system_keys.add(key)
                continue
            texts[key] = text
        if self._engine == "mysql":
            # The sample, with its values masked, is the readable and runnable text; the
            # digest text stays the identity.
            for engine_key, sample in (sample_texts or {}).items():
                key = self._alias_map.get(engine_key)
                template = mysql_sample_template(sample) if key in texts else None
                if template:
                    texts[key] = template
        statements: Dict[str, Dict[str, Any]] = {}
        for delta in deltas:
            key = self._alias_map.get(delta.engine_key)
            if (key is None or key in self._system_identities or
                    delta.completeness != COMPLETE or delta.is_baseline or
                    not delta.calls_delta):
                continue
            if contiguous_since is not None and delta.window_start < contiguous_since:
                continue
            row = statements.setdefault(
                key, {"query_key": key, "calls": 0, "total_ms": 0.0, "rows": 0,
                      "blks_hit": 0, "blks_read": 0, "temp_blks": 0}
            )
            row["calls"] += delta.calls_delta
            row["total_ms"] += delta.exec_time_delta or 0.0
            current = self._baseline.get(delta.engine_key)
            previous = prior_rows.get(delta.engine_key) if prior_rows is not None else getattr(
                self, "_monitor_previous_rows", {}).get(delta.engine_key)
            if current is not None and previous is not None and current.temp_blks >= previous.temp_blks:
                row["temp_blks"] += current.temp_blks - previous.temp_blks
            if current is not None and (not detailed_keys or key in detailed_keys):
                # CounterRow rows is cumulative; use the prior snapshot when
                # available, otherwise leave this optional metric at zero.
                previous = (
                    prior_rows.get(delta.engine_key) if prior_rows is not None
                    else getattr(self, "_monitor_previous_rows", {}).get(delta.engine_key)
                )
                if previous is not None and current.rows >= previous.rows:
                    row["rows"] += current.rows - previous.rows
                block_previous = getattr(self, "_monitor_previous_rows", {}).get(delta.engine_key)
                if block_previous is not None and current.blks_hit >= block_previous.blks_hit:
                    row["blks_hit"] += current.blks_hit - block_previous.blks_hit
                if block_previous is not None and current.blks_read >= block_previous.blks_read:
                    row["blks_read"] += current.blks_read - block_previous.blks_read
        self._monitor_previous_rows = dict(self._baseline)
        if registry is not None:
            current_keys = {
                self._alias_map[row.engine_key]
                for row in (current_rows or [])
                if row.engine_key in self._alias_map
            }
            for key in set(statements) | current_keys:
                if key in self._system_identities or key in known_texts:
                    continue
                entry = registry.get_query(key)
                if entry is not None and entry.original_sql:
                    texts.setdefault(key, entry.original_sql)
        stuck = []
        samples = []
        if self._engine == "postgresql":
            from features.query_registry.statement_stats import parse_pg_engine_key
            by_query_id: Dict[int, set[str]] = {}
            for engine_key, key in self._alias_map.items():
                if key not in sample_keys:
                    continue
                try:
                    by_query_id.setdefault(parse_pg_engine_key(engine_key)[2], set()).add(key)
                except ValueError:
                    continue
            for row in activity:
                if row.get("query_id") is not None:
                    for key in by_query_id.get(row["query_id"], ()):
                        if key in sample_keys and key not in self._system_identities:
                            samples.append({"query_key": key, "text": row["query"]})
        else:
            for engine_key, text in (sample_texts or {}).items():
                key = self._alias_map.get(engine_key)
                if key in sample_keys and key not in self._system_identities:
                    samples.append({"query_key": key, "text": text})
        for row in activity:
            if row["duration_s"] < 60:
                continue
            try:
                key = hash_sql(row["query"])
            except Exception:
                key = None
            stuck.append({
                "pid": row["pid"], "query_key": key, "state": row["state"],
                "duration_s": row["duration_s"],
            })
            if key is not None:
                texts.setdefault(key, row["query"])
        calls = sum(row["calls"] for row in statements.values())
        total_ms = sum(row["total_ms"] for row in statements.values())
        untracked = None
        if engine_delta is not None and engine_delta[0] >= calls:
            untracked = (engine_delta[0] - calls, max(0.0, engine_delta[1] - total_ms))
            calls, total_ms = engine_delta[0], max(total_ms, engine_delta[1])
        db = None
        sample = self._monitor_db_sample
        if sample is not None:
            previous = self._monitor_db_previous
            counters = [0, 0, 0, 0, 0]
            if previous is not None and all(
                int(sample[i]) >= int(previous[i]) for i in range(5)
            ) and (
                len(sample) < 8 or len(previous) < 8 or sample[7] == previous[7]
            ):
                counters = [int(sample[i]) - int(previous[i]) for i in range(5)]
            reads = counters[3]
            hits = (max(0, counters[2] - reads) if self._engine == "mysql"
                    else counters[2])
            db = {
                "calls": calls, "total_ms": total_ms,
                "active_conns": int(sample[5]), "max_conns": int(sample[6]),
                "cache_hit_ratio": hits / (hits + reads) if hits + reads else None,
                "blks_hit": hits, "blks_read": reads,
                "xact_commit": counters[0], "xact_rollback": counters[1],
                "deadlocks": counters[4],
                "lock_waits": sample[8] if len(sample) > 8 else None,
            }
            self._monitor_db_previous = sample
        seen = len(statements)
        # The engine's running totals per identity: the long-run cost per call that steadies
        # the ranking while live readings are few. A baseline reading has them too.
        priors: Dict[str, list] = {}
        for row in current_rows or []:
            key = self._alias_map.get(row.engine_key)
            if key is None or key in self._system_identities:
                continue
            entry = priors.setdefault(key, [0, 0.0])
            entry[0] += int(row.calls or 0)
            entry[1] += float(row.total_exec_time or 0.0)
        if baseline:
            statements, db, untracked = {}, None, None
        # A minute that meets a locked file stays queued, in order, instead of leaving a gap.
        self._monitor_pending.append((ts, list(statements.values()), texts, stuck, db, samples,
                                      system_keys, seen, untracked,
                                      {key: (calls, ms) for key, (calls, ms) in priors.items()}))
        while self._monitor_pending:
            (minute, rows, minute_texts, minute_stuck, minute_db, minute_samples, keys, read,
             minute_untracked, minute_priors) = self._monitor_pending[0]
            try:
                self._monitor.record(self.target, minute, rows, minute_texts, minute_stuck,
                                     minute_db, minute_samples, system_keys=keys,
                                     queries_read=read, workload_checked=True,
                                     untracked=minute_untracked, priors=minute_priors)
            except Exception as exc:
                if not is_locked(exc):
                    self._monitor_pending.popleft()
                raise
            known_texts = {row["query_key"] for row in self._monitor.read(
                "SELECT query_key FROM query_text WHERE target=?", (self.target,))}
            self._monitor_known_texts = known_texts
            self._monitor_pending.popleft()
        if activity_sampled:
            self._monitor.sync_stuck_items(self.target, ts, stuck)

    def _name_new_queries(self) -> None:
        """Give newly tracked queries their display names here, so page requests find them
        named instead of parsing the queries' families while a person waits."""
        from features.monitor.naming import target_names

        try:
            target_names(self._monitor, self.target)
        except Exception:
            logger.debug("Naming new queries failed for %s", self.target, exc_info=True)

    def _fallback_capabilities(self) -> Optional[Dict[str, Any]]:
        if self._source_unavailable is None:
            return None
        return {"source_unavailable": self._source_unavailable}

    def _capture_warning(self) -> Optional[Dict[str, Any]]:
        if self._source_unavailable is None:
            return None
        if self._engine == "mysql":
            return {
                "source": "performance_schema",
                "instructions": [
                    "Set performance_schema=ON in the MySQL server configuration and restart MySQL.",
                    "Grant the RDST database user SELECT access to performance_schema statement digest tables.",
                ],
            }
        return {
            "source": "pg_stat_statements",
            "instructions": [
                "Add pg_stat_statements to shared_preload_libraries and restart PostgreSQL.",
                "Run CREATE EXTENSION IF NOT EXISTS pg_stat_statements; in this database.",
                "Grant the RDST database user permission to read pg_stat_statements.",
            ],
        }

    def _maybe_run_retention(self) -> None:
        """Compact store retention after a successful cycle, on a time gate.

        Retention failures are logged and never fail the cycle, matching
        every other store write in this collector.
        """
        if self._store is None:
            return
        now = self._unix_clock()
        if (
            self._last_retention_at is not None
            and now - self._last_retention_at < RETENTION_MIN_INTERVAL_SECONDS
        ):
            return
        self._last_retention_at = now
        cutoff = now - RETENTION_WINDOW_SECONDS
        try:
            self._store.prune(cutoff)
            self._store.prune_events(cutoff, keep=RETENTION_EVENT_KEEP)
            self._store.prune_rdst_executions(cutoff, now=now)
            self._store.prune_observations(now - OBSERVATION_RETENTION_SECONDS)
        except Exception:
            logger.warning(
                "Failed to compact observation retention for target %s",
                self.target,
                exc_info=True,
            )

    async def collect_now(self) -> QueryDiscoveryEvent:
        """Collect one target snapshot and persist observations, never Saved intent."""
        if default_monitor_db_path().exists():
            settings = await asyncio.to_thread(MonitorStore().read,
                "SELECT value FROM monitor_setting WHERE target=? AND key='enabled'", (self.target,))
            if settings and settings[0]["value"] == "0":
                self._monitor_enabled = self._monitor_baseline_set = False
                self._monitor_pending.clear()
                return QueryDiscoveryEvent(cursor=self._cursor, event="discovery_update", data=self._snapshot)
        if self._two_phase and not self._legacy_fallback:
            async with self._collect_lock:
                event = await self._collect_two_phase()
            if event is not None:
                if event.event == "discovery_update":
                    await asyncio.to_thread(self._maybe_run_retention)
                return event
            # A capability gap switched this collector to the legacy path;
            # the same cycle continues through it immediately.
        event = await self._collect_legacy()
        if event.event == "discovery_update":
            await asyncio.to_thread(self._maybe_run_retention)
        return event

    async def _collect_legacy(self) -> QueryDiscoveryEvent:
        async with self._collect_lock:
            collection_started = time.monotonic()
            attempted_at = self._unix_clock()
            # Reuse one service per collector lifetime so per-cycle setup
            # (temp dir, DataManager, connectivity checks) is paid once.
            service_reused = self._service is not None
            service = self._service
            if service is None:
                service = self._service_factory()
                self._service = service
            options = TopOptions(limit=self.limit, auto_save_registry=False)
            queries: list[TopQueryData] = []
            source = ""
            engine = ""
            error: Optional[str] = None

            fetch_started = time.monotonic()
            try:
                async for event in service.get_top_queries(
                    TopInput(target=self.target, source="auto"), options
                ):
                    if isinstance(event, TopConnectedEvent):
                        source = event.source
                        engine = event.db_engine
                    elif isinstance(event, TopQueriesEvent):
                        queries = event.queries
                        source = event.source or source
                        engine = event.db_engine or engine
                    elif isinstance(event, TopErrorEvent):
                        error = event.message
                        break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug("Automatic query discovery failed", exc_info=True)
                error = "Query discovery is temporarily unavailable."
                logger.info(
                    "Query discovery failed for target %s: %s",
                    self.target,
                    type(exc).__name__,
                )

            fetch_ms = (time.monotonic() - fetch_started) * 1000.0

            if error is not None:
                # A failed cycle must not poison reuse: drop the service so
                # the next cycle rebuilds its resources from scratch.
                self._dispose_service()
                self._record_collector_state(
                    state="unavailable",
                    last_attempt_at=attempted_at,
                    duration_ms=int((time.monotonic() - collection_started) * 1000),
                    error_code="collection_failed",
                    source_capabilities=self._fallback_capabilities(),
                )
                self._snapshot = {
                    **self._snapshot,
                    "state": "unavailable",
                    "changed": False,
                    "capture_warning": self._capture_warning(),
                    "error": error,
                }
                return await self.publish_async("discovery_error", self._snapshot)

            registry = self._registry()
            registry.load()
            previously_observed: set[str] = set()
            prior_stats: Dict[str, tuple[int, float]] = {}
            for entry in registry.list_queries(limit=None):
                lifecycle = entry.lifecycle_for(self.target)
                if lifecycle and lifecycle.first_observed_at:
                    previously_observed.add(entry.hash)
                prior_stats[entry.hash] = (entry.frequency, entry.avg_duration_ms)

            new_hashes: list[str] = []
            changed = False
            persist_started = time.monotonic()
            with registry.defer_save():
                for query in queries:
                    # The two-phase lane's admission rule, applied here too:
                    # the fallback reads the same engine views, so it sees the
                    # same catalog statements and RDST diagnostics.
                    if not references_user_relations(
                        query.query_text
                    ) or match_self_template(query.query_text):
                        continue
                    try:
                        query_hash, _ = registry.add_query(
                            sql=query.query_text,
                            source="top-historical",
                            frequency=query.freq,
                            target=self.target,
                            avg_duration_ms=_duration_ms(query.avg_time),
                            observed=True,
                            save_intent=False,
                        )
                    except ValueError:
                        logger.debug(
                            "Skipping an invalid query returned by discovery",
                            exc_info=True,
                        )
                        continue
                    if query_hash not in previously_observed:
                        new_hashes.append(query_hash)
                        previously_observed.add(query_hash)
                        changed = True
                    else:
                        entry = registry.get_query(query_hash)
                        if entry is not None and prior_stats.get(query_hash) != (
                            entry.frequency,
                            entry.avg_duration_ms,
                        ):
                            changed = True
            persistence_ms = (time.monotonic() - persist_started) * 1000.0

            self._record_counter_snapshots(queries, captured_at=attempted_at)
            self._record_collector_state(
                state="watching",
                last_attempt_at=attempted_at,
                last_success_at=attempted_at,
                duration_ms=int((time.monotonic() - collection_started) * 1000),
                source_capabilities=self._fallback_capabilities(),
            )
            self._snapshot = {
                "target": self.target,
                "state": "watching",
                "updated_at": self._clock(),
                "source": source,
                "engine": engine,
                "query_count": len(queries),
                "new_hashes": new_hashes,
                "changed": changed,
                "stats": {
                    "collection_duration_ms": round(
                        (time.monotonic() - collection_started) * 1000.0, 3
                    ),
                    "db_fetch_duration_ms": round(fetch_ms, 3),
                    "rows_returned": len(queries),
                    "new_identity_count": len(new_hashes),
                    "persistence_ms": round(persistence_ms, 3),
                    "service_reused": service_reused,
                },
                "capture_warning": self._capture_warning(),
                "error": None,
            }
            return await self.publish_async("discovery_update", self._snapshot)


class QueryDiscoveryCoordinator:
    """Process-local target registry that prevents duplicate collectors.

    The module-level singleton must stay inert on import: the observation
    store (one per process for the discovery lane) is created only when the
    first collector is requested, never at import time, so CLI import graphs
    remain side-effect free.
    """

    def __init__(
        self,
        *,
        collector_factory: Optional[Callable[[str], QueryDiscoveryCollector]] = None,
        store_factory: Callable[[], ObservationStore] = ObservationStore,
    ) -> None:
        self._collector_factory = collector_factory
        self._store_factory = store_factory
        self._store: Optional[ObservationStore] = None
        self._store_lock = threading.RLock()
        self._collectors: Dict[str, QueryDiscoveryCollector] = {}
        self._scheduler_owned = False
        self._subscription_listener: Optional[Callable[[str, bool], None]] = None

    def set_scheduler_owned(self, owned: bool) -> None:
        """Make scheduler/SSE cadence ownership explicit for every collector."""
        self._scheduler_owned = owned
        for collector in self._collectors.values():
            collector.set_scheduler_owned(owned)

    def set_subscription_listener(
        self, listener: Optional[Callable[[str, bool], None]]
    ) -> None:
        """Connect collector subscriber edges to the scheduler, if active."""
        self._subscription_listener = listener
        for target, collector in self._collectors.items():
            collector.set_subscriber_state_callback(listener)
            if listener is not None and collector.has_subscribers:
                listener(target, True)

    def _process_store(self) -> Optional[ObservationStore]:
        with self._store_lock:
            if self._store is None:
                try:
                    self._store = self._store_factory()
                except Exception:
                    logger.warning(
                        "Observation store unavailable; discovery continues with "
                        "in-memory replay only",
                        exc_info=True,
                    )
            return self._store

    def store(self) -> Optional[ObservationStore]:
        """The process-wide observation store shared with the scheduler."""
        return self._process_store()

    def existing_store(self) -> Optional[ObservationStore]:
        """Return the process store only when cache.db already exists.

        Query Library freshness is a read-only optional projection. It must
        not create cache.db, and repeated pages must reuse the scheduler's
        validated store instead of repeating construction, quick_check, and
        filesystem probes. The path comparison also makes call-time
        RDST_DATA_DIR/HOME replacement deterministic in tests.
        """
        cache_path = default_cache_db_path()
        expected_path = os.path.realpath(cache_path)
        with self._store_lock:
            if (
                self._store is not None
                and os.path.realpath(self._store.path) != expected_path
            ):
                self._store.close()
                self._store = None
            if self._store is not None:
                return self._store
            if not cache_path.exists():
                return None
            return self._process_store()

    def has_subscribers(self, target: str) -> bool:
        """Whether the target has a live SSE subscriber (cadence class input)."""
        collector = self._collectors.get(target)
        return collector is not None and collector.has_subscribers

    def collector_for(self, target: str) -> QueryDiscoveryCollector:
        collector = self._collectors.get(target)
        if collector is None:
            if self._collector_factory is not None:
                collector = self._collector_factory(target)
            else:
                collector = QueryDiscoveryCollector(
                    target, store=self._process_store()
                )
            collector.set_scheduler_owned(self._scheduler_owned)
            collector.set_subscriber_state_callback(self._subscription_listener)
            self._collectors[target] = collector
        return collector

    def publish_assessment_update(self, target: str) -> None:
        collector = self._collectors.get(target)
        if collector is None or not collector.has_subscribers:
            return
        collector._publish("assessment_update", {"target": target, "changed": True})

    async def publish_assessment_update_async(self, target: str) -> None:
        collector = self._collectors.get(target)
        if collector is None or not collector.has_subscribers:
            return
        await collector.publish_async("assessment_update", {"target": target, "changed": True})

    async def close(self) -> None:
        await asyncio.gather(
            *(collector.close() for collector in self._collectors.values())
        )
        self._collectors.clear()
        self._scheduler_owned = False
        self._subscription_listener = None
        with self._store_lock:
            if self._store is not None:
                self._store.close()
                self._store = None


query_discovery = QueryDiscoveryCoordinator()
