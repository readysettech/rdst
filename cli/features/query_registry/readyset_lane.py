"""The Readyset lane of a load test.

A load test measures a workload against the origin database. When the caller
asks for the Readyset lane too, the same workload runs a second time against
the process's managed Readyset sandbox, and the two are reported side by
side. Everything the second lane needs and the origin lane does not — the
sandbox lease, a cache per query, the tag its traffic carries — lives here.

The Readyset lane is an addition to a run, never a precondition for one: a
sandbox that cannot be leased, or a query the sandbox refuses to cache,
costs that measurement rather than the run.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Iterable, Literal, Sequence

logger = logging.getLogger(__name__)

LANE_ORIGIN = "origin"
LANE_READYSET = "readyset"
# Reporting order, which is also the order a normalized lane list takes.
BENCHMARK_LANES = (LANE_ORIGIN, LANE_READYSET)

# One traffic tag per lane, so a run's two endpoints stay separable from each
# other and from every other RDST feature in the evidence the run writes.
LANE_TAGS = {
    LANE_ORIGIN: "rdst/loadtest",
    LANE_READYSET: "rdst/loadtest-readyset",
}

# A query the sandbox refused to cache is left out of the Readyset lane. The
# sandbox would serve it by proxying to the origin, but that measures an
# upstream round trip plus a hop, and reported beside the origin lane it
# would read as a Readyset result. Compare stops on the same failure.
SKIP_READYSET_CACHE_FAILED = "readyset_cache_failed"

_CONNECTION_KEYS = ("host", "port", "engine", "user", "database", "password")


def normalize_lanes(lanes: Iterable[str] | None) -> tuple[str, ...]:
    """Order and de-duplicate requested lanes; raise ValueError on the rest."""
    if lanes is None:
        return (LANE_ORIGIN,)
    requested = list(lanes)
    unknown = sorted({lane for lane in requested if lane not in BENCHMARK_LANES})
    if unknown:
        raise ValueError(
            "Unknown load test lane: " + ", ".join(str(lane) for lane in unknown)
        )
    ordered = tuple(lane for lane in BENCHMARK_LANES if lane in requested)
    if not ordered:
        raise ValueError("A load test runs at least one lane.")
    return ordered


@dataclass
class ReadysetEndpoint:
    """The sandbox a run's Readyset lane measures, or why it has none.

    The caches the run creates are recorded here as they are created, from
    the benchmark's worker thread, so the lease holder can drop them once the
    run is over however the run ended.
    """

    status: Literal["ok", "unavailable"] = "ok"
    detail: str = ""
    config: dict[str, Any] | None = None
    lease: Any = None
    _caches: list[str] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def available(self) -> bool:
        return self.status == "ok" and self.config is not None

    def note_cache(self, name: str) -> None:
        with self._lock:
            self._caches.append(name)

    def created_caches(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._caches)

    def as_payload(self) -> dict[str, str]:
        return {"status": self.status, "detail": self.detail}


@asynccontextmanager
async def benchmark_sandbox(
    *,
    target: str,
    owner_id: str,
    lanes: Sequence[str],
    manager: Any = None,
) -> AsyncIterator[ReadysetEndpoint | None]:
    """Hold this process's sandbox slot for one load test.

    An origin-only run reserves the slot, which is what keeps a prewarm from
    starting a container in the middle of a measurement. A run with a
    Readyset lane leases the sandbox itself — the lease waits for a container
    whose snapshot is complete — and falls back to the plain reservation when
    no sandbox can be leased.
    """
    from shared.deploy.sandbox_manager import sandbox_manager

    manager = manager or sandbox_manager
    reserve = manager.reserve_measurement(owner_id=owner_id, purpose="load_test")
    if LANE_READYSET not in lanes:
        async with reserve:
            yield None
        return

    async with AsyncExitStack() as stack:
        try:
            lease = await stack.enter_async_context(
                manager.lease(
                    target=target,
                    owner_id=owner_id,
                    purpose="load_test",
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "Load test on %s is measuring the origin alone: %s", target, exc
            )
            async with reserve:
                yield ReadysetEndpoint(
                    status="unavailable", detail=unavailable_detail(exc)
                )
            return
        endpoint = ReadysetEndpoint(
            config=lease.connection.as_target_config(), lease=lease
        )
        # Unwound before the lease is released, so the next lease holder does
        # not inherit this run's caches.
        stack.push_async_callback(_release_caches, endpoint)
        yield endpoint


def unavailable_detail(exc: BaseException) -> str:
    """One line saying why the Readyset lane is not running."""
    message = " ".join(str(exc).split())
    return message or (
        f"The Readyset sandbox could not be leased ({type(exc).__name__})."
    )


async def _release_caches(endpoint: ReadysetEndpoint) -> None:
    names = endpoint.created_caches()
    if not names or endpoint.config is None:
        return
    try:
        failed = await asyncio.shield(
            asyncio.to_thread(drop_lane_caches, endpoint.config, names)
        )
    except asyncio.CancelledError:
        # The drop is still in flight and nobody is left to observe it, so
        # the sandbox is quarantined rather than handed on as clean.
        await _mark_dirty(endpoint, "Load test cache cleanup was interrupted")
        raise
    if failed:
        await _mark_dirty(
            endpoint,
            f"Load test cache cleanup failed for {len(failed)} cache(s)",
        )


async def _mark_dirty(endpoint: ReadysetEndpoint, reason: str) -> None:
    if endpoint.lease is not None:
        await asyncio.shield(endpoint.lease.mark_dirty(reason))


def prepare_lane_caches(
    endpoint: ReadysetEndpoint,
    owner_id: str,
    queries: Sequence[tuple[str, str]],
    on_progress: Callable[[int, int], None] | None = None,
) -> dict[str, str]:
    """Give each ``(identifier, sql)`` a cache; return the refusals by identifier.

    Blocking, and long: the first cache on a cold sandbox builds its state
    from the upstream tables, so each statement carries the same timeout
    Compare gives its own cold create. ``on_progress(prepared, total)`` is
    called as each query leaves the queue, refusal included, so a caller can
    report the wait rather than go silent through it.
    """
    from features.cache.experiment_service import (
        COLD_CREATE_CACHE_TIMEOUT_MS,
        temporary_cache_name,
    )
    from features.cache.service import CacheService
    from shared.query_registry.sql_normalizer import denormalize_for_readyset

    if endpoint.config is None:
        return {identifier: "no sandbox" for identifier, _ in queries}

    service = CacheService()
    connection = _connection_kwargs(endpoint.config)
    failures: dict[str, str] = {}
    total = len(queries)
    for prepared, (identifier, sql) in enumerate(queries, start=1):
        try:
            try:
                name = temporary_cache_name(owner_id, sql)
                statement = denormalize_for_readyset(
                    sql, engine=str(connection["engine"])
                )
            except Exception as exc:
                failures[identifier] = type(exc).__name__
                logger.warning(
                    "Load test could not build Readyset cache DDL for %s: %s",
                    identifier,
                    exc,
                )
                continue
            result = service._run_readyset_sql(
                f"CREATE CACHE {name} FROM {statement}",
                statement_timeout_ms=COLD_CREATE_CACHE_TIMEOUT_MS,
                **connection,
            )
            if result.get("success"):
                endpoint.note_cache(name)
                continue
            error = str(result.get("error") or "").strip()
            failures[identifier] = error or "CREATE CACHE failed"
            logger.warning(
                "Readyset refused a load test cache for %s: %s",
                identifier,
                failures[identifier],
            )
        finally:
            if on_progress is not None:
                on_progress(prepared, total)
    return failures


def drop_lane_caches(
    config: dict[str, Any], names: Sequence[str]
) -> list[str]:
    """Drop the run's caches; return the names the sandbox still holds."""
    from features.cache.service import CacheService

    service = CacheService()
    connection = _connection_kwargs(config)
    failed: list[str] = []
    for name in names:
        result = service._run_readyset_sql(f"DROP CACHE {name}", **connection)
        if not result.get("success"):
            failed.append(name)
            logger.warning(
                "Load test could not drop the Readyset cache %s: %s",
                name,
                result.get("error") or "unknown error",
            )
    return failed


def _connection_kwargs(config: dict[str, Any]) -> dict[str, Any]:
    return {key: config.get(key) for key in _CONNECTION_KEYS}


__all__ = [
    "BENCHMARK_LANES",
    "LANE_ORIGIN",
    "LANE_READYSET",
    "LANE_TAGS",
    "SKIP_READYSET_CACHE_FAILED",
    "ReadysetEndpoint",
    "benchmark_sandbox",
    "drop_lane_caches",
    "normalize_lanes",
    "prepare_lane_caches",
    "unavailable_detail",
]
