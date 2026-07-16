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

import os
import threading
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from random import Random
from typing import Any, Callable


def _think(tier: str, default: float) -> float:
    return float(os.environ.get(f"QPDEMO_THINK_{tier.upper()}_S", default))


# Per-tier think time paces UNCACHED queries so the shared upstream Postgres
# stays stable and the demo runs identically on a small (2-core) machine. It is a
# ceiling on the uncached path only: a query that returns faster than
# FAST_PATH_MS was served from Readyset's cache, so its worker runs open-loop and
# the cached tier surges. That is what lets caching multiply completions while the
# uncached baseline stays low and steady. Deliberately generous: the demo targets
# a small baseline (~tens of QPS) that any laptop sustains without overloading a
# container, and the cache win shows as the ratio, not the absolute number.
# Env-overridable (QPDEMO_THINK_<TIER>_S) for tuning without a code change.
TIER_THINK_S = {
    "cheap": _think("cheap", 0.05),
    "mid": _think("mid", 0.15),
    "row": _think("row", 0.05),
    # Heavy analytical aggregations run fast enough on a 2-core Postgres to peg
    # its cap if unpaced; a longer think keeps the upstream comfortably off the
    # cap. Cached queries skip think entirely, so this never limits the surge.
    "heavy": _think("heavy", 0.25),
}
# A query under this wall-clock latency was served from cache; skip its think so
# cached tiers surge. Uncached queries (direct Postgres, or router pass-through)
# land well above it and stay paced.
FAST_PATH_MS = 2.0

# Warm gate for freshly cached queries: a sample at or under this latency means
# the cache is serving (cached executions land in single-digit-to-low-tens of ms
# even on capped containers; pass-throughs land in the hundreds). Samples above
# it are dropped from the per-query comparison stats while the gate is armed, up
# to the cap, so a just-reset average never blends in pre-warm pass-throughs.
WARM_GATE_MS = 100.0
WARM_GATE_MAX_SAMPLES = 30


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
        # Keys whose per-query stats are held until a warm (cache-speed) sample
        # arrives; value counts the samples dropped while waiting. See
        # defer_stats_until_warm.
        self._stats_gate: dict[str, int] = {}
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._tier_thread_counts: dict[str, int] = {}
        # Keys currently served from ReadySet's cache. The router path runs those
        # open-loop (skips think) so its line surges as queries get cached, while
        # everything else stays paced. The direct path never surges: it is the
        # steady baseline. Fed by the service from live cache state.
        self._cached_keys: frozenset[str] = frozenset()

    def set_cached_keys(self, keys: Iterable[str]) -> None:
        self._cached_keys = frozenset(keys or ())

    def _should_pace(self, key: str, dt_ms: float, think: float) -> bool:
        """Whether this completed query should sleep its think time. Router cache
        hits run open-loop (never pace) so ReadySet's line surges as queries get
        cached; the direct path and uncached router queries stay paced, holding a
        steady baseline. A genuinely fast uncached query (under FAST_PATH_MS)
        also skips its pace."""
        if not think:
            return False
        if self.path != "direct" and key in self._cached_keys:
            return False
        return dt_ms > FAST_PATH_MS

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
                if self._stats_record_allowed(key, dt):
                    stat = self._query_stats.setdefault(key, QueryStat())
                    stat.count += 1
                    stat.total_ms += dt
            think = TIER_THINK_S.get(tier, 0.0)
            if self._should_pace(key, dt, think):
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

    def defer_stats_until_warm(self, key: str) -> None:
        """Hold this key's per-query average until its cache is serving.

        Executions that complete before a freshly created cache warms still run
        at pass-through speed; recording them into a just-reset average makes
        the cache win look far smaller than it is. While the gate is armed,
        samples above WARM_GATE_MS are dropped from the comparison stats (the
        throughput window is untouched); the first warm sample records and
        lifts the gate. The gate also lifts after WARM_GATE_MAX_SAMPLES so a
        cache that never materializes cannot suppress the average forever.
        """
        with self._lock:
            self._stats_gate[key] = 0

    def _stats_record_allowed(self, key: str, dt_ms: float) -> bool:
        """Caller must hold self._lock."""
        dropped = self._stats_gate.get(key)
        if dropped is None:
            return True
        if dt_ms <= WARM_GATE_MS or dropped + 1 >= WARM_GATE_MAX_SAMPLES:
            del self._stats_gate[key]
            return True
        self._stats_gate[key] = dropped + 1
        return False

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
        # The throughput sample buffer and error counters are left alone. Any
        # armed warm gate is disarmed too: a reset means the caller is starting
        # this comparison over, not waiting out a cache fill.
        with self._lock:
            if key is None:
                self._query_stats = {}
                self._stats_gate = {}
            else:
                self._query_stats.pop(key, None)
                self._stats_gate.pop(key, None)

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

    def set_cached_keys(self, keys: Iterable[str]) -> None:
        """Tell the router path which query keys ReadySet is serving from cache
        so it runs them open-loop and its throughput surges."""
        self.sqp.set_cached_keys(keys)

    def defer_router_stats_until_warm(self, key: str) -> None:
        """Manual cache: hold the router average for this key until the cache
        is serving, so the rebuilt number shows cache speed from its first
        sample. The direct (Postgres) path is untouched."""
        self.sqp.defer_stats_until_warm(key)

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

    def reset_query_stats(self, key: str | None = None, router_only: bool = False) -> None:
        # router_only keeps the direct (Postgres) average in place while
        # restarting the router (ReadySet) one: used when a query is cached by
        # hand, so ReadySet's number rebuilds instantly from cached executions
        # and the Postgres baseline stays put instead of blanking.
        if not router_only:
            self.direct.reset_query_stats(key)
        self.sqp.reset_query_stats(key)

    def recent_errors(self) -> dict[str, list[str]]:
        return {"direct": self.direct.recent_errors(), "router": self.sqp.recent_errors()}
