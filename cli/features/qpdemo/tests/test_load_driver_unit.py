from features.qpdemo.load_driver import PathLoad, _tier_plan
from features.qpdemo.workload import load_sqls


def test_path_load_accepts_legacy_sql_list():
    load = PathLoad("direct", {}, ["SELECT 1", "SELECT 2"])

    assert load.queries == ["SELECT 1", "SELECT 2"]
    assert load.query_weights() == {"SELECT 1": 1, "SELECT 2": 1}


def test_path_load_expands_weighted_query_metadata():
    specs = [
        {"id": "a", "sql": "SELECT 1", "weight": 3, "group": "base"},
        {"id": "b", "sql": "SELECT 2", "weight": 2, "group": "base"},
        {"id": "late", "sql": "SELECT 3", "weight": 0, "group": "late_arriving", "activation_weight": 4},
    ]
    load = PathLoad("direct", {}, specs)

    assert load.queries.count("SELECT 1") == 3
    assert load.queries.count("SELECT 2") == 2
    assert "SELECT 3" not in load.queries

    changed = load.activate_group("late_arriving")

    assert changed == 1
    assert load.queries.count("SELECT 3") == 4
    assert load.query_weights()["late"] == 4


def test_path_load_can_activate_by_phase_or_override_weight():
    specs = [
        {"id": "base", "sql": "SELECT 1", "weight": 1, "phase": "base"},
        {"id": "late", "sql": "SELECT 2", "weight": 0, "phase": "late", "activation_weight": 5},
    ]
    load = PathLoad("direct", {}, specs)

    changed = load.activate_group("late", weight=2)

    assert changed == 1
    assert load.queries.count("SELECT 2") == 2
    assert load.query_weights()["late"] == 2


def test_load_sqls_metadata_lets_driver_activate_late_group():
    load = PathLoad("direct", {}, load_sqls())
    before = len(load.queries)

    changed = load.activate_group("late_arriving")

    assert changed == 7
    assert len(load.queries) == before + sum(
        spec["activation_weight"]
        for spec in load_sqls().query_specs
        if spec["group"] == "late_arriving"
    )


def test_tier_plan_keeps_cheap_queries_from_starving():
    plan = _tier_plan(8, {"cheap", "mid", "heavy"})

    assert plan["cheap"] >= 1
    assert plan["mid"] >= 1
    assert plan["heavy"] >= 1
    assert sum(plan.values()) == 8
    assert plan["heavy"] > plan["cheap"]



def test_router_paces_uncached_but_surges_cached():
    # The router (non-direct) path skips its think time for keys ReadySet is
    # serving from cache, so its throughput surges as queries get cached; slow
    # uncached queries stay paced.
    router = PathLoad("sqp", {}, ["SELECT 1"])
    router.set_cached_keys({"C01"})
    # cached key -> open-loop (no pace) even though it's slow
    assert router._should_pace("C01", dt_ms=50.0, think=0.05) is False
    # uncached slow key -> paced
    assert router._should_pace("H01", dt_ms=50.0, think=0.05) is True


def test_direct_path_never_surges_on_cache():
    # The direct path is the steady baseline: it always paces a slow query, even
    # if that key happens to be cached, so ReadySet's advantage shows as the
    # router pulling ahead rather than the baseline moving.
    direct = PathLoad("direct", {}, ["SELECT 1"])
    direct.set_cached_keys({"C01"})
    assert direct._should_pace("C01", dt_ms=50.0, think=0.05) is True


def test_a_genuinely_fast_query_skips_its_pace():
    router = PathLoad("sqp", {}, ["SELECT 1"])
    # under FAST_PATH_MS -> no pace regardless of cache membership
    assert router._should_pace("H01", dt_ms=1.0, think=0.05) is False


def test_warm_gate_holds_stats_until_cache_speed_sample():
    # A freshly cached query's average must rebuild from cached executions:
    # pass-through samples that land before the cache warms are dropped, the
    # first cache-speed sample records and lifts the gate.
    load = PathLoad("sqp", {}, ["SELECT 1"])
    load.defer_stats_until_warm("M01")
    with load._lock:
        assert load._stats_record_allowed("M01", 350.0) is False
        assert load._stats_record_allowed("M01", 22.0) is True
        # gate lifted: later slow samples record normally
        assert load._stats_record_allowed("M01", 350.0) is True


def test_warm_gate_only_affects_the_deferred_key():
    load = PathLoad("sqp", {}, ["SELECT 1"])
    load.defer_stats_until_warm("M01")
    with load._lock:
        assert load._stats_record_allowed("M02", 350.0) is True


def test_warm_gate_lifts_after_sample_cap():
    # A cache that never materializes cannot suppress the average forever.
    from features.qpdemo.load_driver import WARM_GATE_MAX_SAMPLES

    load = PathLoad("sqp", {}, ["SELECT 1"])
    load.defer_stats_until_warm("M01")
    with load._lock:
        for _ in range(WARM_GATE_MAX_SAMPLES - 1):
            assert load._stats_record_allowed("M01", 350.0) is False
        assert load._stats_record_allowed("M01", 350.0) is True


def test_reset_query_stats_disarms_warm_gate():
    # A reset means the caller is starting the comparison over, not waiting
    # out a cache fill.
    load = PathLoad("sqp", {}, ["SELECT 1"])
    load.defer_stats_until_warm("M01")
    load.reset_query_stats("M01")
    with load._lock:
        assert load._stats_record_allowed("M01", 350.0) is True
