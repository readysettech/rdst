"""Pure checks for the app simulator (tests/app_sim): no Docker needed."""

from __future__ import annotations

import random
import re
from collections import Counter

import pytest

from tests.app_sim import dal, expected, schema, traffic

TINY = schema.sizes(0.01)


def _all_rows(seed_value: int) -> dict[str, list[tuple]]:
    return {table: list(schema.rows(table, TINY, seed_value)) for table in schema.TABLES}


def test_seed_is_deterministic_and_seed_sensitive():
    first, again, other = _all_rows(7), _all_rows(7), _all_rows(8)
    assert first == again
    assert first["orders"] != other["orders"]


def test_seed_rows_match_columns_and_sizes():
    for table in schema.TABLES:
        rows = list(schema.rows(table, TINY))
        assert len(rows) == TINY[table]
        assert all(len(row) == len(schema.columns(table)) for row in rows)


def test_seed_references_stay_in_range():
    rows = _all_rows(7)
    n_customers, n_orders = TINY["customers"], TINY["orders"]
    assert all(1 <= row[1] <= n_customers for row in rows["orders"])
    assert all(1 <= row[1] <= n_orders for row in rows["order_items"])
    assert all(1 <= row[1] <= TINY["carts"] for row in rows["cart_items"])
    tokens = [row[2] for row in rows["sessions"]]
    assert len(set(tokens)) == len(tokens)
    slugs = [row[3] for row in rows["products"]]
    assert len(set(slugs)) == len(slugs)


def test_traffic_helpers_agree_with_the_seed():
    rows = {row[0]: row for row in schema.rows("sessions", TINY)}
    for session_id in (1, 5, TINY["sessions"]):
        assert rows[session_id][1] == schema.session_customer(session_id, TINY["customers"])
        assert rows[session_id][2] == schema.session_token(session_id)
    products = {row[0]: row for row in schema.rows("products", TINY)}
    assert products[3][3] == schema.product_slug(3)
    customers = {row[0]: row for row in schema.rows("customers", TINY)}
    assert customers[4][1] == schema.customer_email(4) == schema.customer_email(4).lower()


@pytest.mark.parametrize("engine", ["postgresql", "mysql"])
def test_ddl_renders_for_both_engines(engine):
    for table in schema.TABLES:
        sql = schema.ddl(table, engine)
        assert "{" not in sql and sql.startswith(f"CREATE TABLE {table} (")


def test_full_scale_stays_small():
    assert sum(schema.sizes(1.0).values()) < 2_000_000


def test_journey_weights_and_methods():
    assert all(weight > 0 for weight in traffic.JOURNEYS.values())
    reads = sum(traffic.JOURNEYS[name] for name in ("browse_category", "view_product", "search",
                                                    "view_cart", "my_orders", "order_detail"))
    assert reads / sum(traffic.JOURNEYS.values()) > 0.7
    for name in traffic.JOURNEYS:
        assert callable(getattr(traffic.Driver, f"j_{name}"))
    for name, interval in traffic.JOBS:
        assert interval > 0 and callable(getattr(traffic.Driver, f"b_{name}"))


def test_journey_picks_follow_weights_and_repeat():
    picks = [traffic.pick_journey(random.Random(42)) for _ in range(3)]
    assert len(set(picks)) == 1
    rng = random.Random(1)
    counts = Counter(traffic.pick_journey(rng) for _ in range(20000))
    total = sum(traffic.JOURNEYS.values())
    for name, weight in traffic.JOURNEYS.items():
        assert abs(counts[name] / 20000 - weight / total) < 0.015


def test_statements_reference_real_tables_and_columns():
    known = {table: set(schema.columns(table)) for table in schema.TABLES}
    for name, sql in dal.Q.items():
        assert dal.verb(name) in ("SELECT", "INSERT", "UPDATE", "DELETE"), name
        for table, column in re.findall(r"\b([a-z_]+)\.([a-z_]+)\b", sql):
            if table in known:
                assert column in known[table], f"{name}: {table}.{column}"


def test_statements_are_distinct():
    assert len(set(dal.Q.values())) == len(dal.Q)


def test_expected_records_are_well_formed():
    indexes = {name: (table, cols) for name, table, cols, _u, _p in schema.INDEXES}
    plants = set()
    for record in expected.EXPECTED:
        plants.add(record["plant"])
        assert set(record["engines"]) <= set(expected.BOTH)
        assert record["why"] and "—" not in record["why"]
        for statement in record.get("statements", []):
            assert statement in dal.Q
        if "table" in record:
            assert record["table"] in schema.TABLES
        if record["type"] in ("add_index", "skip_index"):
            existing = [cols for table, cols in indexes.values() if table == record["table"]]
            columns = tuple(record["columns"])
            assert set(columns) <= set(schema.columns(record["table"]))
            # A recommended index isn't already served by an existing leading prefix.
            assert not any(cols[:len(columns)] == columns for cols in existing)
        if record["type"] in ("drop_index", "keep_index"):
            assert indexes[record["index"]][0] == record["table"]
        if record.get("reason") == "duplicate":
            short = indexes[record["index"]][1]
            wide = indexes[record["covered_by"]][1]
            assert wide[:len(short)] == short and len(wide) > len(short)
        if record["type"] == "rewrite_query":
            assert all(record["query_contains"] in dal.Q[s] for s in record["statements"])
    assert plants == {f"P{i}" for i in range(1, 11)}


def test_planted_index_tags_match_expected():
    tagged = {name: plant for name, _t, _c, _u, plant in schema.INDEXES if plant}
    by_index = {r["index"]: r["plant"] for r in expected.EXPECTED if "index" in r}
    assert tagged == by_index


def test_must_not_contradicts_nothing_expected():
    for bad in expected.MUST_NOT:
        for good in expected.EXPECTED:
            if bad["type"] != good["type"]:
                continue
            assert bad.get("index") is None or bad["index"] != good.get("index")
            if "columns" in bad and "priority" not in bad:
                assert (bad["table"], bad["columns"]) != (good.get("table"), good.get("columns"))


def test_for_engine_splits_settings():
    pg = {r["name"] for r in expected.for_engine("postgresql") if r["type"] == "change_setting"}
    my = {r["name"] for r in expected.for_engine("mysql") if r["type"] == "change_setting"}
    assert pg == {"work_mem"} and my == {"tmp_table_size"}
