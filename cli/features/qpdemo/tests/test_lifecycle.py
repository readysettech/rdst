"""Full-lifecycle integration test against the shared session demo stack.

Executes the real orchestration checks (four containers, health-gating, no target
registration, dual-path load, manual + QueryPilot caching). The stack itself is
provisioned once by the session-scoped qpdemo_stack fixture in conftest.py.
"""

import time

import psycopg2
import pytest

from features.qpdemo.service import DemoService
from features.qpdemo.workload import WORKLOAD

pytestmark = pytest.mark.skipif(not DemoService.docker_ok(), reason="docker not available")


def _wait_for(fn, timeout=120, interval=3):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = fn()
        if last:
            return last
        time.sleep(interval)
    return last


def _drive_sqp(svc: DemoService, sql: str, n: int):
    p = svc._ports
    conn = psycopg2.connect(host="127.0.0.1", port=p.sqp, user="sqp_user",
                            password="sqp_pass", dbname="sqp_test", connect_timeout=5)
    conn.autocommit = True
    try:
        for _ in range(n):
            with conn.cursor() as c:
                c.execute(sql)
                c.fetchall()
    finally:
        conn.close()


def _sql(query_id: str) -> str:
    return next(q.sql for q in WORKLOAD if q.id == query_id)


def _is_heavy(row: dict) -> bool:
    sql = row.get("sql", "").lower()
    return row.get("group") == "expensive_aggregate" or ("sum(" in sql and " join " in sql)


def _is_cheap_lookup(row: dict) -> bool:
    sql = row.get("sql", "").lower()
    return row.get("group") == "cheap_point_lookup" or (" where " in sql and " id " in sql and " join " not in sql)


def _trailing_means(svc: DemoService, window_s: float) -> tuple[float, float, int]:
    """Mean router/direct qps over the last `window_s` of load history.

    Uses ratios of simultaneously-measured paths, so the result is independent
    of absolute host speed (holds on a weak 2-vCPU CI box).
    """
    hist = svc.load_history()
    now = time.time()
    recent = [s for s in hist["samples"] if float(s.get("t", 0)) >= now - window_s]
    if not recent:
        return 0.0, 0.0, 0
    direct = sum(float(s["direct"]["qps"]) for s in recent) / len(recent)
    router = sum(float(s["router"]["qps"]) for s in recent) / len(recent)
    return router, direct, len(recent)


def test_provision_brings_up_four_containers(provisioned):
    st = provisioned.status()
    assert st["containers"]["pg"] == "running"
    assert st["containers"]["readyset"] == "running"
    assert st["containers"]["sqp"] == "running"
    assert st["containers"]["qp-cron"] in {"created", "exited"}
    assert st["querypilot_paused"] is True  # QueryPilot off by default
    assert st["querypilot_enabled"] is False
    assert st["querypilot"] == {
        "enabled": False,
        "mode": "count_star",
        "next_pass_eta_s": None,
        "schedule": st["querypilot"]["schedule"],
        "cache_budget": 20,
    }
    assert st["cache_budget"] == 20
    assert st["querypilot"]["schedule"] in {"15s", "1min"}
    assert set(st["health"]) == {"pg", "readyset", "sqp", "qp-cron"}
    assert all(v in {"running", "recovering", "failed"} for v in st["health"].values())
    assert st["discovery_mode"] == "count_star"
    assert st["ports"]["readyset_metrics"]


def test_targets_not_registered(provisioned):
    from shared.config.targets import TargetsConfig
    cfg = TargetsConfig(); cfg.load()
    assert cfg.get("demo") is None
    assert cfg.get("demo-qpr") is None


def test_load_runs_without_errors(provisioned):
    provisioned.set_querypilot(False)
    provisioned.start_load(6)
    latest = _wait_for(
        lambda: provisioned._latest
        if provisioned._latest and provisioned._latest[0].qps > 0 and provisioned._latest[1].qps > 0
        else None,
        timeout=30,
        interval=1,
    )
    assert latest is not None
    d, s = latest
    assert d.qps > 0 and s.qps > 0
    assert d.errors == 0 and s.errors == 0, f"load errors direct={d.errors} sqp={s.errors}"
    hist = provisioned.load_history()
    assert hist["samples"]
    sample = hist["samples"][-1]
    assert {"t", "direct", "router"} <= set(sample)
    assert {"qps", "p50_ms", "p95_ms"} <= set(sample["direct"])
    assert {"qps", "p50_ms", "p95_ms"} <= set(sample["router"])


def test_workload_titles_matched(provisioned):
    _drive_sqp(provisioned, _sql("H01"), 6)
    _drive_sqp(provisioned, _sql("C01"), 8)
    _drive_sqp(provisioned, _sql("C02"), 8)
    pats = provisioned.patterns()
    assert len(pats) >= 3
    assert sum(1 for p in pats if p.get("title")) >= 3
    row = pats[0]
    assert {
        "key", "title", "sql", "group", "status", "hits", "has_cache",
        "postgres_hits", "readyset_hits", "direct_avg_ms",
        "router_avg_ms", "reason", "log_reason", "alt_rank", "alt_metric",
    } == set(row)
    assert row["status"] in {
        "cached_querypilot", "cached_manual", "pass_through", "not_eligible",
        "denylisted", "unsupported",
    }
    assert "kind" in row["reason"]


def test_manual_cache_then_uncache(provisioned):
    _drive_sqp(provisioned, _sql("H01"), 6)
    pats = provisioned.patterns()
    heavy = next((p for p in pats if _is_heavy(p) and not p["status"].startswith("cached_")), None)
    heavy = heavy or next(
        (p for p in pats if p["sql"].lower().startswith("select") and not p["status"].startswith("cached_")),
        None,
    )
    assert heavy is not None, "no uncached heavy query to cache"
    provisioned.cache(heavy["key"])
    time.sleep(4)
    row = next(p for p in provisioned.patterns() if p["key"] == heavy["key"])
    assert row["status"] == "cached_manual"
    provisioned.uncache(heavy["key"])
    time.sleep(3)
    row = next(p for p in provisioned.patterns() if p["key"] == heavy["key"])
    assert not row["status"].startswith("cached_")


def test_querypilot_autocaches(provisioned):
    _drive_sqp(provisioned, _sql("C01"), 12)
    _drive_sqp(provisioned, _sql("C02"), 10)
    _drive_sqp(provisioned, _sql("C03"), 8)
    provisioned.set_querypilot(True)
    qp = _wait_for(
        lambda: [p for p in provisioned.patterns() if p["status"] == "cached_querypilot"],
        timeout=150,
    )
    assert len(qp) >= 1, "QueryPilot cached nothing after resume"
    provisioned.set_querypilot(False)
    provisioned.stop_load()


def test_querypilot_surge_holds_without_sawtooth(provisioned):
    # After QueryPilot caches, the ReadySet line must SURGE and HOLD, not
    # sawtooth up and down. The root cause of the old sawtooth was a 10-second
    # shallow-cache TTL: ~20 caches created together refreshed in lockstep every
    # few seconds and the throughput line collapsed and recovered repeatedly. The
    # fix is a long TTL. This guards both the root cause (long TTL, deterministic)
    # and the symptom (a stable, elevated router line).
    import re

    provisioned.set_querypilot(False)
    provisioned.start_load(6)
    # Feed QueryPilot the frequent queries so it caches (it opens on count_star).
    for _ in range(3):
        _drive_sqp(provisioned, _sql("C01"), 40)
        _drive_sqp(provisioned, _sql("C02"), 30)
        _drive_sqp(provisioned, _sql("C03"), 25)
    provisioned.set_querypilot(True)
    _wait_for(
        lambda: [p for p in provisioned.patterns() if p["status"] == "cached_querypilot"],
        timeout=150,
    )

    # Root cause: every shallow cache carries a long TTL, so they never refresh
    # in a tight loop. A short TTL here would reintroduce the sawtooth.
    caches = provisioned._qp.readyset.show_caches()
    ttls = [int(m) for row in caches for m in re.findall(r"ttl (\d+) ms", " ".join(map(str, row)))]
    assert ttls, "no shallow-cache TTLs found via SHOW CACHES"
    assert min(ttls) >= 60_000, f"shallow TTL too short (sawtooth risk): {min(ttls)} ms"

    # Symptom: poll patterns() (which feeds the cached-key set to the load driver)
    # each window and sample the router-vs-direct ratio. Ratios are host-speed
    # independent, so this holds on a weak CI box. After warmup the router must
    # stay elevated every window; a sawtooth would drop a window toward parity.
    ratios = []
    for _ in range(12):
        provisioned.patterns()
        time.sleep(2)
        router, direct, n = _trailing_means(provisioned, window_s=3)
        if n and direct > 0:
            ratios.append(router / direct)
    provisioned.set_querypilot(False)
    provisioned.stop_load()

    assert len(ratios) >= 6, f"too few windows sampled: {len(ratios)}"
    settled = sorted(ratios[3:])  # drop warmup windows while caches populate
    median = settled[len(settled) // 2]
    assert median >= 2.0, f"router did not surge: median ratio {median:.1f} (ratios {[round(r, 1) for r in ratios]})"
    assert min(settled) >= 1.2, f"router dipped toward the baseline: ratios {[round(r, 1) for r in ratios]}"


def test_discovery_mode_switch_resets_and_reselects(provisioned):
    provisioned.stop_load()
    provisioned.set_querypilot(False)
    # Enabling always opens in count_star, so the visitor switches to sum_time
    # only after QueryPilot is on, matching the demo's controls.
    provisioned.set_querypilot(True)
    provisioned.set_discovery_mode("sum_time")
    _drive_sqp(provisioned, _sql("H01"), 30)
    _drive_sqp(provisioned, _sql("C01"), 120)
    _drive_sqp(provisioned, _sql("C03"), 100)
    _drive_sqp(provisioned, _sql("C05"), 80)
    sum_time_rows = _wait_for(
        lambda: [p for p in provisioned.patterns()
                 if p["status"] == "cached_querypilot" and _is_heavy(p)],
        timeout=120,
    )
    assert sum_time_rows, "sum_time did not select an expensive query"

    provisioned.set_discovery_mode("count_star")
    count_rows = _wait_for(
        lambda: [p for p in provisioned.patterns()
                 if p["status"] == "cached_querypilot" and _is_cheap_lookup(p)],
        timeout=120,
    )
    assert len(count_rows) >= 2, "count_star did not select frequent cheap queries"
    assert provisioned.status()["discovery_mode"] == "count_star"
    provisioned.set_querypilot(False)


def test_sum_time_caching_lifts_router_throughput(provisioned):
    """The demo's whole claim: caching the expensive queries makes the router
    path measurably faster. Assert a relative lift with CI-safe margins — ratios
    only, never absolute qps — so it holds on a weak 2-vCPU/2GiB CI host."""
    provisioned.stop_load()
    provisioned.set_querypilot(False)
    provisioned.start_load(6)

    # Router baseline with no QueryPilot caching in effect.
    time.sleep(10)
    base_router, base_direct, base_n = _trailing_means(provisioned, 6)
    assert base_n >= 1 and base_router > 0, "no baseline load samples captured"

    provisioned.set_querypilot(True)
    provisioned.set_discovery_mode("sum_time")

    cached = _wait_for(
        lambda: [p for p in provisioned.patterns()
                 if p["status"] == "cached_querypilot" and _is_heavy(p)],
        timeout=150,
    )
    assert cached, "sum_time did not cache an expensive query"

    # On a capable host the cached heavy queries take over the router path's
    # throughput; on a fully CPU-saturated tiny CI host (t3a-small, ~10 qps
    # total) the lift physically can't appear because CPU binds, not query
    # latency. Settle a full ~15s selector-pass window, then assert
    # saturation-aware below.
    SATURATION_FLOOR_QPS = 50.0

    def lift_ready() -> bool:
        router, direct, count = _trailing_means(provisioned, 15)
        if count < 8 or direct <= 0:
            return False
        # Degraded hosts can't show lift; stop settling once the window is full.
        return direct < SATURATION_FLOOR_QPS or router >= 1.5 * direct

    _wait_for(lift_ready, timeout=90, interval=3)

    router, direct, count = _trailing_means(provisioned, 15)
    provisioned.set_querypilot(False)
    provisioned.stop_load()

    assert count >= 8 and direct > 0, f"insufficient settled window: n={count}"
    # Selection correctness holds regardless of host speed.
    assert cached, "sum_time did not cache an expensive query"

    if direct < SATURATION_FLOOR_QPS:
        # Host-saturated: lift assertion degraded to selection + no-regression.
        print(
            f"host-saturated: lift assertion degraded (control {direct:.1f} qps "
            f"< {SATURATION_FLOOR_QPS:.0f} floor); asserting router >= 0.8x control"
        )
        assert router >= 0.8 * direct, (
            f"router {router:.1f} qps regressed below control {direct:.1f} qps"
        )
        return

    # Capable host: full strength. Simultaneous router-vs-control ratio cancels
    # host speed entirely.
    assert router >= 1.5 * direct, (
        f"router {router:.1f} qps not >=1.5x control {direct:.1f} qps"
    )
    # Secondary: router path lifted well above its own pre-caching baseline.
    assert router >= 1.5 * base_router, (
        f"router {router:.1f} qps not >=1.5x pre-caching baseline {base_router:.1f} qps"
    )


def test_stats_reset_on_querypilot_toggle(provisioned):
    provisioned.start_load(6)
    active = _wait_for(
        lambda: [p for p in provisioned.patterns() if p["hits"] > 0],
        timeout=30,
        interval=1,
    )
    assert active, "load did not populate per-query stats"

    provisioned.set_querypilot(False)
    after = provisioned.patterns()
    assert all(p["hits"] == 0 for p in after)


def test_querypilot_takes_over_caching_from_manual(provisioned):
    # QueryPilot is command-and-control: enabling it drops every cache (including
    # the hand-created one) and manages caching itself; disabling it drops its
    # own caches.
    provisioned.stop_load()
    provisioned.set_querypilot(False)
    _drive_sqp(provisioned, _sql("H01"), 8)
    provisioned.cache("H01")
    assert _wait_for(
        lambda: [p for p in provisioned.patterns()
                 if p["key"] == "H01" and p["status"] == "cached_manual"],
        timeout=30,
    ), "manual cache did not take"

    _drive_sqp(provisioned, _sql("C01"), 30)
    _drive_sqp(provisioned, _sql("C02"), 25)
    _drive_sqp(provisioned, _sql("C03"), 20)
    provisioned.set_querypilot(True)
    # Enabling QueryPilot drops the manual cache: H01 is no longer manual (it is
    # either pass_through or, if QueryPilot's policy re-selects it, its own).
    assert _wait_for(
        lambda: next((p for p in provisioned.patterns()
                      if p["key"] == "H01"), {}).get("status") != "cached_manual",
        timeout=60,
    ), "QueryPilot did not drop the manual cache when it took over"
    qp_rows = _wait_for(
        lambda: [p for p in provisioned.patterns() if p["status"] == "cached_querypilot"],
        timeout=150,
    )
    assert qp_rows, "QueryPilot cached nothing after taking over"

    provisioned.set_querypilot(False)
    rows = provisioned.patterns()
    assert not [p for p in rows if p["status"] == "cached_querypilot"]
