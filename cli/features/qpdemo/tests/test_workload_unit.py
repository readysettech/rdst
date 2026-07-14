from pathlib import Path

from features.qpdemo.workload import WORKLOAD, load_sqls, workload_dicts


QPDIR = Path(__file__).resolve().parents[1]


def test_workload_has_expected_groups_and_stable_keys():
    rows = workload_dicts()
    ids = [q["id"] for q in rows]

    assert len(rows) == 44
    assert len(ids) == len(set(ids))
    assert all(q["key"] == q["id"] for q in rows)

    required = {"title", "what", "why", "category", "sql", "weight", "group", "phase"}
    assert all(required <= set(q) for q in rows)

    counts: dict[str, int] = {}
    for q in rows:
        counts[q["group"]] = counts.get(q["group"], 0) + 1

    assert counts == {
        "cheap_point_lookup": 10,
        "expensive_aggregate": 10,
        "mid_tier": 7,
        "row_heavy": 4,
        "decline_examples": 6,
        "late_arriving": 7,
    }


def test_late_queries_start_inactive_but_have_activation_weights():
    late = [q for q in WORKLOAD if q.group == "late_arriving"]

    assert len(late) == 7
    assert all(q.weight == 0 for q in late)
    assert all((q.activation_weight or 0) > 0 for q in late)


def test_denylist_examples_are_explicit_not_implicit():
    denylisted = {q.id for q in WORKLOAD if q.denylist}

    assert denylisted == {"D01", "D02"}
    assert "now()" in next(q.sql for q in WORKLOAD if q.id == "D01")
    assert "random()" in next(q.sql for q in WORKLOAD if q.id == "D02")


def test_load_sqls_excludes_inactive_late_queries_by_default():
    sqls = load_sqls()

    assert len(sqls) == sum(q.weight for q in WORKLOAD if q.weight > 0)
    assert hasattr(sqls, "query_specs")
    assert not any(q.sql in sqls for q in WORKLOAD if q.group == "late_arriving")


def test_manual_guide_picks_excluded_from_count_star_top_20():
    """G3: the two manual "easy wins" (M01/M02) must never be auto-selected by
    the most-frequent policy (count_star, top 20). count_star ranks by call
    count, which is proportional to weight, so at least 20 other eligible base
    queries must out-rank them by weight."""
    eligible = [
        q for q in WORKLOAD
        if q.weight > 0 and not q.denylist and q.phase == "base"
    ]
    ranked = sorted(eligible, key=lambda q: q.weight, reverse=True)
    top20 = {q.id for q in ranked[:20]}

    assert "M01" not in top20
    assert "M02" not in top20
    # And enough queries clearly out-rank them, so the exclusion is not fragile.
    m_weight = max(
        next(q.weight for q in WORKLOAD if q.id == "M01"),
        next(q.weight for q in WORKLOAD if q.id == "M02"),
    )
    assert sum(1 for q in eligible if q.weight > m_weight) >= 20


def test_seed_and_schema_share_no_secondary_index_strategy():
    seed = (QPDIR / "assets" / "seed.sql").read_text()
    schema = (QPDIR / "dataset" / "schema.sql").read_text()

    forbidden = [
        "idx_orders_customer",
        "idx_order_items_order",
        "CREATE INDEX",
        "orders (customer_id)",
        "order_items (order_id)",
        "order_items (product_id)",
    ]
    for sql in (seed, schema):
        for needle in forbidden:
            assert needle not in sql

    for table in ("customers", "products", "orders", "order_items"):
        assert f"CREATE TABLE {table}" in seed
        assert f"CREATE TABLE {table}" in schema
