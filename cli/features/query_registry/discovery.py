"""Automatic query discovery for the web and desktop Query Library.

The CLI and web API share :class:`TopService` and :class:`QueryRegistry`, but
the automatic collector is an opt-in web/desktop orchestration layer. It reads
the same historical database statistics without changing CLI commands or
turning an observation into explicit Saved intent.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
from typing import Any, AsyncGenerator, Callable, Deque, Dict, Optional

from features.top.events import (
    TopConnectedEvent,
    TopErrorEvent,
    TopQueriesEvent,
)
from features.top.models import TopInput, TopOptions, TopQueryData
from features.top.service import TopService
from shared.query_registry import QueryRegistry

logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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

    def __init__(
        self,
        target: str,
        *,
        interval_seconds: float = 60.0,
        limit: int = 100,
        service_factory: Callable[[], TopService] = TopService,
        registry_factory: Callable[[], QueryRegistry] = QueryRegistry,
        clock: Callable[[], str] = _utc_now,
        history_size: int = 128,
    ) -> None:
        self.target = target
        self.interval_seconds = interval_seconds
        self.limit = limit
        self._service_factory = service_factory
        self._registry_factory = registry_factory
        self._clock = clock
        self._events: Deque[QueryDiscoveryEvent] = deque(maxlen=history_size)
        self._subscribers: set[asyncio.Queue[QueryDiscoveryEvent]] = set()
        self._task: Optional[asyncio.Task[None]] = None
        self._wake = asyncio.Event()
        self._collect_lock = asyncio.Lock()
        self._cursor = 0
        self._snapshot: Dict[str, Any] = {
            "target": target,
            "state": "starting",
            "updated_at": "",
            "source": "",
            "engine": "",
            "query_count": 0,
            "new_hashes": [],
            "error": None,
        }

    @property
    def task(self) -> Optional[asyncio.Task[None]]:
        return self._task

    def _publish(self, event: str, data: Dict[str, Any]) -> QueryDiscoveryEvent:
        self._cursor += 1
        discovery_event = QueryDiscoveryEvent(self._cursor, event, data)
        self._events.append(discovery_event)
        for subscriber in tuple(self._subscribers):
            subscriber.put_nowait(discovery_event)
        return discovery_event

    def snapshot_event(self) -> QueryDiscoveryEvent:
        return QueryDiscoveryEvent(self._cursor, "discovery_snapshot", self._snapshot)

    def replay_after(self, cursor: Optional[int]) -> list[QueryDiscoveryEvent]:
        """Replay retained deltas, or reconcile with the latest snapshot."""
        if cursor is None:
            return [self.snapshot_event()]
        if cursor >= self._cursor:
            return []
        if self._events and cursor >= self._events[0].cursor - 1:
            return [event for event in self._events if event.cursor > cursor]
        return [self.snapshot_event()]

    async def ensure_started(self) -> None:
        if self._task is None or self._task.done():
            self._wake.clear()
            self._task = asyncio.create_task(
                self._run(), name=f"query-discovery:{self.target}"
            )

    def request_refresh(self) -> None:
        self._wake.set()

    async def close(self) -> None:
        task = self._task
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        if self._task is task:
            self._task = None
        self._wake.clear()

    async def subscribe(
        self, after_cursor: Optional[int] = None
    ) -> AsyncGenerator[QueryDiscoveryEvent, None]:
        queue: asyncio.Queue[QueryDiscoveryEvent] = asyncio.Queue()
        self._subscribers.add(queue)
        replay = self.replay_after(after_cursor)
        await self.ensure_started()
        delivered_cursor = after_cursor if after_cursor is not None else -1
        try:
            for event in replay:
                delivered_cursor = max(delivered_cursor, event.cursor)
                yield event
            while True:
                event = await queue.get()
                # A collection can finish between subscribing and replaying.
                # In that case the same event exists in retained history and
                # the subscriber queue; deliver it only once.
                if event.cursor <= delivered_cursor:
                    continue
                delivered_cursor = event.cursor
                yield event
        finally:
            self._subscribers.discard(queue)
            # Discovery is app-owned and target-scoped. Stop polling as soon as
            # the last Web/Desktop transport leaves this target so a target
            # switch cannot accumulate invisible background collectors.
            if not self._subscribers:
                await self.close()

    async def _run(self) -> None:
        while True:
            try:
                await self.collect_now()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "Automatic query discovery cycle failed for target %s: %s",
                    self.target,
                    type(exc).__name__,
                    exc_info=True,
                )
                self._snapshot = {
                    **self._snapshot,
                    "state": "unavailable",
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

    async def collect_now(self) -> QueryDiscoveryEvent:
        """Collect one target snapshot and persist observations, never Saved intent."""
        async with self._collect_lock:
            service = self._service_factory()
            options = TopOptions(limit=self.limit, auto_save_registry=False)
            queries: list[TopQueryData] = []
            source = ""
            engine = ""
            error: Optional[str] = None

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

            if error is not None:
                self._snapshot = {
                    **self._snapshot,
                    "state": "unavailable",
                    "error": error,
                }
                return self._publish("discovery_error", self._snapshot)

            registry = self._registry_factory()
            registry.load()
            previously_observed = {
                entry.hash
                for entry in registry.list_queries(limit=None)
                if entry.lifecycle_for(self.target)
                and entry.lifecycle_for(self.target).first_observed_at
            }
            new_hashes: list[str] = []
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

            self._snapshot = {
                "target": self.target,
                "state": "watching",
                "updated_at": self._clock(),
                "source": source,
                "engine": engine,
                "query_count": len(queries),
                "new_hashes": new_hashes,
                "error": None,
            }
            return self._publish("discovery_update", self._snapshot)


class QueryDiscoveryCoordinator:
    """Process-local target registry that prevents duplicate collectors."""

    def __init__(
        self,
        *,
        collector_factory: Callable[[str], QueryDiscoveryCollector] = (
            QueryDiscoveryCollector
        ),
    ) -> None:
        self._collector_factory = collector_factory
        self._collectors: Dict[str, QueryDiscoveryCollector] = {}

    def collector_for(self, target: str) -> QueryDiscoveryCollector:
        collector = self._collectors.get(target)
        if collector is None:
            collector = self._collector_factory(target)
            self._collectors[target] = collector
        return collector

    async def close(self) -> None:
        await asyncio.gather(
            *(collector.close() for collector in self._collectors.values())
        )
        self._collectors.clear()


query_discovery = QueryDiscoveryCoordinator()
