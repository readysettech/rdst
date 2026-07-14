"""Dual-path load driver for the demo.

RDST Web is the load generator: it fires the same query workload at both the
direct-Postgres endpoint and the SQP+QueryPilot endpoint simultaneously, and
measures QPS + latency percentiles client-side, per time window. There is no
separate load-generator container — this is that generator.

Threaded (not async): psycopg2 releases the GIL during network I/O, so a pool of
worker threads sustains high QPS against small-result queries. A `workers` knob
maps to the demo's load-intensity control.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from random import Random
from typing import Any, Callable


# Per-tier think time paces UNCACHED queries so the shared upstream Postgres
# stays stable under load. It is a ceiling on the uncached path only: a query
# that returns faster than FAST_PATH_MS was served from Readyset's cache, so its
# worker runs open-loop and the cached tier can surge. This is what lets caching
# multiply completions instead of being capped by a fixed pace.
TIER_THINK_S = {
    "cheap": 0.005,
    "mid": 0.030,
    "row": 0.004,
    "heavy": 0.0,
}
# A query under this wall-clock latency was served from cache; skip its think so
# cached tiers surge. Uncached queries (direct Postgres, or router pass-through)
# land well above it and stay paced.
FAST_PATH_MS = 2.0


@dataclass
class WindowStat:
    path: str
    count: int
    errors: int
    retried: int
    transient_errors: int
    qps: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    avg_ms: float


@dataclass
class LoadQuery:
    sql: str
    weight: int = 1
    group: str = "default"
    phase: str = "base"
    activation_weight: int | None = None
    key: str | None = None
    role: str = "default"
    category: str = "default"


@dataclass
class QueryStat:
    count: int = 0
    total_ms: float = 0.0

    @property
    def avg_ms(self) -> float | None:
        return self.total_ms / self.count if self.count else None


def _pct(sorted_ms: list[float], q: float) -> float:
    if not sorted_ms:
        return 0.0
    i = min(len(sorted_ms) - 1, int(q * len(sorted_ms)))
    return sorted_ms[i]


def _coerce_query_spec(item: str | Mapping[str, Any]) -> LoadQuery:
    if isinstance(item, str):
        return LoadQuery(sql=item)
    sql = str(item["sql"])
    key = item.get("key") or item.get("id")
    activation = item.get("activation_weight")
    return LoadQuery(
        sql=sql,
        weight=max(0, int(item.get("weight", 1))),
        group=str(item.get("group", "default")),
        phase=str(item.get("phase", "base")),
        activation_weight=int(activation) if activation is not None else None,
        key=str(key) if key is not None else None,
        role=str(item.get("role", item.get("category", "default"))),
        category=str(item.get("category", item.get("role", "default"))),
    )


def _normalize_queries(queries: Sequence[str] | Iterable[Mapping[str, Any]]) -> list[LoadQuery]:
    specs = getattr(queries, "query_specs", None)
    if specs is not None:
        return [_coerce_query_spec(q) for q in specs]
    return [_coerce_query_spec(q) for q in queries]


def _expand_weighted(specs: Iterable[LoadQuery]) -> list[str]:
    sqls: list[str] = []
    for spec in specs:
        if spec.weight > 0:
            sqls.extend([spec.sql] * spec.weight)
    return sqls


def _tier_for(spec: LoadQuery) -> str:
    text = " ".join((spec.role, spec.category, spec.group)).lower()
    if "heavy" in text or "expensive" in text:
        return "heavy"
    if "row" in text:
        return "mid"
    if "moderate" in text or "mid" in text or "uncacheable" in text:
        return "mid"
    return "cheap"


def _tier_plan(total_workers: int, active_tiers: set[str]) -> dict[str, int]:
    if total_workers <= 0 or not active_tiers:
        return {}
    weights = {"cheap": 0.25, "mid": 0.12, "row": 0.0, "heavy": 0.63}
    tiers = sorted(active_tiers, key=lambda t: weights.get(t, 0.1), reverse=True)
    if total_workers < len(tiers):
        return {tier: 1 for tier in tiers[:total_workers]}

    total_weight = sum(weights.get(t, 0.1) for t in tiers)
    raw = {tier: total_workers * weights.get(tier, 0.1) / total_weight for tier in tiers}
    plan = {tier: max(1, int(raw[tier])) for tier in tiers}
    while sum(plan.values()) < total_workers:
        tier = max(tiers, key=lambda t: raw[t] - plan[t])
        plan[tier] += 1
    while sum(plan.values()) > total_workers:
        tier = min((t for t in tiers if plan[t] > 1), key=lambda t: raw[t] - plan[t])
        plan[tier] -= 1
    return plan


class PathLoad:
    """Runs a worker pool against one DSN, accumulating latencies per window."""

    def __init__(self, path: str, dsn: dict, queries: Sequence[str] | Iterable[Mapping[str, Any]]):
        self.path = path
        self.dsn = dsn
        self._query_specs = _normalize_queries(queries)
        self._query_lock = threading.Lock()
        self.queries = _expand_weighted(self._query_specs)
        if not self.queries:
            raise ValueError("PathLoad requires at least one active query")
        self._tier_queries: dict[str, list[LoadQuery]] = {}
        self._rebuild_tier_queries()
        self._lock = threading.Lock()
        self._samples: list[float] = []
        self._errors = 0
        self._retried = 0
        self._transient_errors = 0
        self._last_errors: list[str] = []
        self._query_stats: dict[str, QueryStat] = {}
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._tier_thread_counts: dict[str, int] = {}

    def _rebuild_tier_queries(self) -> None:
        rng = Random(f"{self.path}:tiered-load")
        tiered: dict[str, list[LoadQuery]] = {}
        for spec in self._query_specs:
            if spec.weight <= 0:
                continue
            tiered.setdefault(_tier_for(spec), []).extend([spec] * spec.weight)
        for items in tiered.values():
            rng.shuffle(items)
        self._tier_queries = tiered

    def _next_query(self, idx: int, tier: str) -> tuple[str, str] | None:
        with self._query_lock:
            tier_queries = self._tier_queries.get(tier) or [
                spec for specs in self._tier_queries.values() for spec in specs
            ]
            if not tier_queries:
                return None
            spec = tier_queries[idx % len(tier_queries)]
            return spec.sql, spec.key or spec.sql

    @staticmethod
    def _connect(psycopg2, dsn: dict):
        conn = psycopg2.connect(connect_timeout=5, **dsn)
        conn.autocommit = True
        return conn

    def _execute_once(self, psycopg2, conn, sql: str):
        if conn is None:
            conn = self._connect(psycopg2, self.dsn)
        with conn.cursor() as cur:
            cur.execute(sql)
            cur.fetchall()
        return conn

    def _record_error(self, error: Exception) -> None:
        with self._lock:
            self._errors += 1
            self._last_errors.append(str(error)[:240])
            self._last_errors = self._last_errors[-12:]

    def _worker(self, start_idx: int, tier: str):
        import psycopg2

        conn = None
        i = start_idx
        while not self._stop.is_set():
            q = self._next_query(i, tier)
            i += 1
            if q is None:
                time.sleep(0.05)
                continue
            sql, key = q
            t0 = time.perf_counter()
            try:
                conn = self._execute_once(psycopg2, conn, sql)
            except Exception:
                with self._lock:
                    self._retried += 1
                try:
                    if conn:
                        conn.close()
                except Exception:
                    pass
                conn = None
                try:
                    time.sleep(0.10)
                    conn = self._execute_once(psycopg2, conn, sql)
                    with self._lock:
                        self._transient_errors += 1
                except Exception as retry_error:
                    self._record_error(retry_error)
                    try:
                        if conn:
                            conn.close()
                    except Exception:
                        pass
                    conn = None
                    time.sleep(0.05)
                    continue
            dt = (time.perf_counter() - t0) * 1000.0
            with self._lock:
                self._samples.append(dt)
                stat = self._query_stats.setdefault(key, QueryStat())
                stat.count += 1
                stat.total_ms += dt
            think = TIER_THINK_S.get(tier, 0.0)
            if think and dt > FAST_PATH_MS:
                time.sleep(think)
        if conn:
            try:
                conn.close()
            except Exception:
                pass

    def _start_one(self, tier: str, worker_idx: int) -> None:
        th = threading.Thread(target=self._worker, args=(worker_idx, tier), daemon=True)
        th.start()
        self._threads.append(th)
        self._tier_thread_counts[tier] = self._tier_thread_counts.get(tier, 0) + 1

    def start(self, workers: int):
        if workers <= 0:
            return
        with self._query_lock:
            active = set(self._tier_queries)
        current_total = sum(self._tier_thread_counts.values())
        desired = _tier_plan(current_total + workers, active)
        additions: dict[str, int] = {}
        for tier, count in desired.items():
            additions[tier] = max(0, count - self._tier_thread_counts.get(tier, 0))
        missing = workers - sum(additions.values())
        while missing > 0 and desired:
            tier = max(desired, key=lambda t: desired[t] - self._tier_thread_counts.get(t, 0))
            additions[tier] = additions.get(tier, 0) + 1
            missing -= 1
        for tier, count in additions.items():
            for _ in range(count):
                self._start_one(tier, len(self._threads))

    def drain_window(self, elapsed_s: float) -> WindowStat:
        with self._lock:
            samples = self._samples
            self._samples = []
            errors = self._errors
            self._errors = 0
            retried = self._retried
            self._retried = 0
            transient_errors = self._transient_errors
            self._transient_errors = 0
        samples.sort()
        n = len(samples)
        return WindowStat(
            path=self.path, count=n, errors=errors, retried=retried,
            transient_errors=transient_errors,
            qps=n / elapsed_s if elapsed_s > 0 else 0.0,
            p50_ms=_pct(samples, 0.50), p95_ms=_pct(samples, 0.95),
            p99_ms=_pct(samples, 0.99),
            avg_ms=sum(samples) / n if n else 0.0,
        )

    def query_stats(self) -> dict[str, dict[str, float | int | None]]:
        with self._lock:
            return {
                key: {"hits": stat.count, "avg_ms": stat.avg_ms}
                for key, stat in self._query_stats.items()
            }

    def reset_query_stats(self, key: str | None = None) -> None:
        # Restart the per-query comparison stats. With a key, only that one query
        # restarts (used when a single query is cached, so its average rebuilds
        # from cached executions instead of inching down through the old slow
        # samples); without a key, all restart (policy / QueryPilot change).
        # The throughput sample buffer and error counters are left alone.
        with self._lock:
            if key is None:
                self._query_stats = {}
            else:
                self._query_stats.pop(key, None)

    def recent_errors(self) -> list[str]:
        with self._lock:
            return list(self._last_errors)

    def stop(self):
        self._stop.set()

    def activate_group(self, name: str, weight: int | None = None) -> int:
        """Activate an inactive weighted group and rebuild the query mix.

        ``name`` matches either ``group`` or ``phase`` so callers can use
        ``activate_group("late_arriving")`` or ``activate_group("late")``.
        Returns the number of query specs whose weight changed.
        """
        changed = 0
        with self._query_lock:
            for spec in self._query_specs:
                if spec.group != name and spec.phase != name:
                    continue
                new_weight = weight
                if new_weight is None:
                    new_weight = spec.activation_weight or spec.weight or 1
                new_weight = max(0, int(new_weight))
                if spec.weight != new_weight:
                    spec.weight = new_weight
                    changed += 1
            self.queries = _expand_weighted(self._query_specs)
            self._rebuild_tier_queries()
        return changed

    def query_weights(self) -> dict[str, int]:
        with self._query_lock:
            return {
                spec.key or spec.sql: spec.weight
                for spec in self._query_specs
            }


class DualPathLoadDriver:
    """Drive both paths at once, yielding paired window stats."""

    def __init__(self, direct_dsn: dict, sqp_dsn: dict,
                 queries: Sequence[str] | Iterable[Mapping[str, Any]]):
        if not hasattr(queries, "query_specs") and not isinstance(queries, Sequence):
            queries = list(queries)
        self.direct = PathLoad("direct", direct_dsn, queries)
        self.sqp = PathLoad("sqp", sqp_dsn, queries)

    def run(self, workers: int, duration_s: float, window_s: float = 1.0,
            on_window: Callable[[WindowStat, WindowStat], None] | None = None
            ) -> list[tuple[WindowStat, WindowStat]]:
        self.direct.start(workers)
        self.sqp.start(workers)
        windows: list[tuple[WindowStat, WindowStat]] = []
        deadline = time.monotonic() + duration_s
        try:
            while time.monotonic() < deadline:
                t0 = time.monotonic()
                time.sleep(window_s)
                el = time.monotonic() - t0
                d = self.direct.drain_window(el)
                s = self.sqp.drain_window(el)
                windows.append((d, s))
                if on_window:
                    on_window(d, s)
        finally:
            self.direct.stop()
            self.sqp.stop()
        return windows

    def set_workers(self, workers: int):
        """Adjust intensity mid-run by spinning up more workers on both paths."""
        self.direct.start(workers)
        self.sqp.start(workers)

    def activate_group(self, name: str, weight: int | None = None) -> int:
        """Activate a workload group on both paths.

        This is intentionally additive; the existing service can keep passing
        ``load_sqls()`` and another layer can wire this method through PATCH
        /load when the UI is ready.
        """
        changed = self.direct.activate_group(name, weight)
        self.sqp.activate_group(name, weight)
        return changed

    def query_stats(self) -> dict[str, dict[str, dict[str, float | int | None]]]:
        return {"direct": self.direct.query_stats(), "router": self.sqp.query_stats()}

    def reset_query_stats(self, key: str | None = None) -> None:
        self.direct.reset_query_stats(key)
        self.sqp.reset_query_stats(key)

    def recent_errors(self) -> dict[str, list[str]]:
        return {"direct": self.direct.recent_errors(), "router": self.sqp.recent_errors()}
