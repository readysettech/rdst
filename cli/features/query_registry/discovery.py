"""Automatic query discovery for the web and desktop Query Library.

The CLI and web API share :class:`TopService` and :class:`QueryRegistry`, but
the automatic collector is an opt-in web/desktop orchestration layer. It reads
the same historical database statistics without changing CLI commands or
turning an observation into explicit Saved intent.
"""

from __future__ import annotations

import asyncio
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
    PG_VERSION_SQL,
    CollectResult,
    MysqlDigestSource,
    PgStatStatementsSource,
    StatementStatsSource,
    _toplevel_flag,
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

logger = logging.getLogger(__name__)


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
    connection = create_direct_connection(target_config, target=name, lane="rdst/observe")
    if engine == "postgresql":
        # SET LOCAL session setup needs a transaction block; the per-cycle
        # rollback returns the connection to idle between collections.
        try:
            connection.autocommit = False
        except Exception:
            logger.debug("Could not disable autocommit for target %s", target, exc_info=True)
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

    def _publish(self, event: str, data: Dict[str, Any]) -> QueryDiscoveryEvent:
        self._sync_cursor()
        seq = self._store_append(event, data)
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
        while True:
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
                self._publish("discovery_error", self._snapshot)
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

    def _two_phase_fetch(
        self,
    ) -> tuple[CollectResult, Dict[str, str], list[str]]:
        """Blocking Phase A (plus Phase B when keys lack text) on the reused
        connection, then one bounded activity snapshot for parameter values."""
        if self._connection is None:
            connection, engine = self._connection_factory(self.target)
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
        executor = _connection_executor(connection)

        try:
            result = source.collect(
                executor,
                cursor_state=self._cursor_state,
                known_text_keys=frozenset(self._alias_map),
            )
            if result.epoch_id != self._alias_epoch:
                self._alias_map = self._load_persisted_aliases(result.epoch_id)
                self._alias_epoch = result.epoch_id
            texts = dict(result.texts)
            needed = {
                row.engine_key
                for row in result.rows
                if row.engine_key not in self._alias_map
                and row.engine_key not in texts
            }
            texts.update(self._fetch_statement_texts(needed))
            # The activity snapshot only recovers parameter values, so its
            # failure (privileges, timeout) never fails the cycle -- the
            # same non-fatal posture as every store write.
            activity_texts: list[str] = []
            try:
                activity_texts = source.activity_sample(executor)
            except Exception:
                logger.debug(
                    "Activity sampling failed for target %s; the cycle "
                    "continues without parameter capture",
                    self.target,
                    exc_info=True,
                )
            return result, texts, activity_texts
        finally:
            # End the read transaction so the reused connection idles between
            # cycles (SET LOCAL scoping relies on it on PostgreSQL).
            try:
                connection.rollback()
            except Exception:
                logger.debug(
                    "Rollback after collection failed for target %s",
                    self.target,
                    exc_info=True,
                )

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
        executor = _connection_executor(connection)
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
            try:
                connection.rollback()
            except Exception:
                logger.debug(
                    "Rollback after text fetch failed for target %s",
                    self.target,
                    exc_info=True,
                )

    def _recent_observation_rows(
        self, deltas: list[DeltaResult], *, incremental: bool
    ) -> list[Dict[str, Any]]:
        """Window results keyed by library identity; engine keys without one are dropped."""
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

    async def _collect_two_phase(self) -> Optional[QueryDiscoveryEvent]:
        """One two-phase cycle, or None when a capability gap engaged the
        sticky legacy fallback and the caller should finish the cycle there."""
        collection_started = time.monotonic()
        attempted_at = self._unix_clock()
        connection_reused = self._connection is not None
        self._restore_cursor_state()

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
            self._record_collector_state(
                state="unavailable",
                last_attempt_at=attempted_at,
                duration_ms=int((time.monotonic() - collection_started) * 1000),
                error_code="collection_failed",
            )
            self._snapshot = {
                **self._snapshot,
                "state": "unavailable",
                "changed": False,
                "error": "Query discovery is temporarily unavailable.",
            }
            return self._publish("discovery_error", self._snapshot)
        fetch_ms = (time.monotonic() - fetch_started) * 1000.0
        local_fetched_at = self._local_clock()

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
        for engine_key, text in texts.items():
            if engine_key in self._alias_map or engine_key not in rows_by_key:
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
        epoch_changed = bool(self._baseline) and self._baseline_epoch != result.epoch_id
        deltas: list[DeltaResult] = []
        if epoch_changed:
            self._baseline = {}
            self._baseline_captured_at = None
            self._cursor_state = None
        elif self._baseline and self._baseline_captured_at is not None:
            deltas = process_snapshot(
                self._baseline, result.rows, self._baseline_captured_at, captured_at
            )
        self._baseline = advance_baseline(self._baseline, result.rows)
        self._baseline_captured_at = captured_at
        self._baseline_epoch = result.epoch_id
        self._cursor_state = result.next_cursor_state

        registry = self._registry()
        registry.load()
        previously_observed: set[str] = set()
        prior_stats: Dict[str, tuple[int, float]] = {}
        for entry in registry.list_queries(limit=None):
            lifecycle = entry.lifecycle_for(self.target)
            if lifecycle and lifecycle.first_observed_at:
                previously_observed.add(entry.hash)
            prior_stats[entry.hash] = (entry.frequency, entry.avg_duration_ms)

        # Identity-level aggregation over the merged baseline: absent engine
        # keys keep their last counters, so an incremental sweep never
        # shrinks an identity's cumulative evidence, and every aliased
        # identity in the epoch stays a Library candidate even when its
        # first sighting lost the top-K cut.
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

        # Library adds stay bounded: one slot per normalized identity, only
        # identities the target has not observed before, ranked by this
        # window's execution time (cumulative total without a valid window)
        # and capped at the collector limit.
        candidates = sorted(
            (
                normalized
                for normalized in identity_rows
                if normalized not in previously_observed
                and normalized not in self._system_identities
            ),
            key=interval_rank,
            reverse=True,
        )[: self.limit]
        candidate_texts = await self._resolve_candidate_texts(
            candidates, identity_rows, texts, registry
        )

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

            # Known entries receive refreshed evidence, exactly as the legacy
            # path re-adds every observed row: frequency mirrors the engine's
            # cumulative calls and avg is the calls-weighted mean. Only first
            # observations become New, so nothing here touches new_hashes.
            added = set(new_hashes)
            for normalized, counter_rows in identity_rows.items():
                if normalized in added:
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
                if refreshed_entry is not None and prior_stats.get(query_hash) != (
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
            for identity, sampled in _activity_capture_map(activity_texts).items():
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

        self._record_identity_aliases(result.epoch_id, new_aliases)
        self._record_recent_observations(
            self._apply_rdst_attribution(
                self._recent_observation_rows(deltas, incremental=result.incremental)
            )
        )
        self._record_counter_rows(
            result.rows, result.epoch_id, captured_at=captured_at
        )
        capabilities: Dict[str, Any] = dict(result.capabilities)
        capabilities["cursor_state"] = result.next_cursor_state
        capabilities["incremental"] = result.incremental
        self._record_collector_state(
            state="watching",
            last_attempt_at=attempted_at,
            last_success_at=attempted_at,
            duration_ms=int((time.monotonic() - collection_started) * 1000),
            epoch_id=result.epoch_id,
            source_capabilities=capabilities,
        )
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
                "incremental": result.incremental,
            },
            "error": None,
        }
        return self._publish("discovery_update", self._snapshot)

    def _fallback_capabilities(self) -> Optional[Dict[str, Any]]:
        if self._source_unavailable is None:
            return None
        return {"source_unavailable": self._source_unavailable}

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
        except Exception:
            logger.warning(
                "Failed to compact observation retention for target %s",
                self.target,
                exc_info=True,
            )

    async def collect_now(self) -> QueryDiscoveryEvent:
        """Collect one target snapshot and persist observations, never Saved intent."""
        if self._two_phase and not self._legacy_fallback:
            async with self._collect_lock:
                event = await self._collect_two_phase()
            if event is not None:
                if event.event == "discovery_update":
                    self._maybe_run_retention()
                return event
            # A capability gap switched this collector to the legacy path;
            # the same cycle continues through it immediately.
        event = await self._collect_legacy()
        if event.event == "discovery_update":
            self._maybe_run_retention()
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
                    "error": error,
                }
                return self._publish("discovery_error", self._snapshot)

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
                "error": None,
            }
            return self._publish("discovery_update", self._snapshot)


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
