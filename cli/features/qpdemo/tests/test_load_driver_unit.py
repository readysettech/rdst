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

