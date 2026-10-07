"""Pure checks for the app-sim scenarios: schema shape, seed determinism, expected records."""

from __future__ import annotations

import itertools
import random
import string
from datetime import datetime

import pytest

from tests.app_sim.scenarios import RECORD_TYPES, SCENARIOS
from tests.app_sim.scenarios.base import ANCHOR, Scenario, expand

NAMES = sorted(SCENARIOS)
SMALL = 0.02


def app(name: str, engine: str = "postgresql") -> Scenario:
    return SCENARIOS[name].App(scale=SMALL, engine=engine)


def head(scenario: Scenario, table: str, seed: int = 7, count: int = 300) -> list[tuple]:
    return list(itertools.islice(scenario.rows(table, seed), count))


@pytest.mark.parametrize("name", NAMES)
def test_rows_match_columns(name: str) -> None:
    scenario = app(name)
    for table in scenario.tables:
        rows = head(scenario, table)
        assert rows, table
        width = len(scenario.columns(table))
        assert all(len(row) == width for row in rows), table


@pytest.mark.parametrize("name", NAMES)
def test_seed_is_deterministic(name: str) -> None:
    for table in app(name).tables:
        first, second = head(app(name), table), head(app(name), table)
        assert first == second, table
        # Engine never changes the data, only the DDL.
        assert first == head(app(name, "mysql"), table), table


@pytest.mark.parametrize("name", NAMES)
def test_seed_value_changes_random_columns(name: str) -> None:
    scenario = app(name)
    changed = [table for table in scenario.tables
               if head(scenario, table, 7) != head(scenario, table, 8)]
    assert changed


def test_iot_live_anchor_shifts_timestamps_only() -> None:
    module = SCENARIOS["iot"]
    a = module.App(scale=SMALL, anchor=datetime(2026, 10, 7, 12, 0))
    b = module.App(scale=SMALL, anchor=datetime(2026, 10, 7, 13, 0))
    rows_a, rows_b = head(a, "readings"), head(b, "readings")
    assert [r[:7] for r in rows_a] == [r[:7] for r in rows_b]
    assert all(r[7] <= a.anchor for r in rows_a)
    assert module.App.live_anchor and not SCENARIOS["saas"].App.live_anchor
    assert SCENARIOS["saas"].App(scale=SMALL).anchor == ANCHOR


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("engine", ["postgresql", "mysql"])
def test_ddl_renders(name: str, engine: str) -> None:
    scenario = app(name, engine)
    for table in scenario.tables:
        ddl = scenario.ddl(table, engine)
        assert ddl.startswith(f"CREATE TABLE {table} (") and "{" not in ddl
    for index, table, cols, _unique, _plant in scenario.indexes:
        assert set(cols) <= set(scenario.columns(table)), index


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("engine", ["postgresql", "mysql"])
def test_statements_are_ascii_and_engine_complete(name: str, engine: str) -> None:
    scenario = app(name, engine)
    for statement in scenario.queries:
        sql = scenario.sql(statement, engine)
        assert sql.isascii()
        assert sql.split(None, 1)[0].upper() in ("SELECT", "INSERT", "UPDATE", "DELETE")


@pytest.mark.parametrize("name", NAMES)
def test_workload_methods_exist(name: str) -> None:
    scenario = app(name)
    for journey in scenario.journeys:
        assert callable(getattr(scenario, f"j_{journey}")), journey
    for job, interval in scenario.jobs:
        assert callable(getattr(scenario, f"b_{job}")) and interval > 0, job
    picks = {scenario.pick_journey(random.Random(i)) for i in range(500)}
    assert picks <= set(scenario.journeys)


def _check_record(name: str, scenario: Scenario, record: dict, must_not: bool) -> None:
    assert record["type"] in RECORD_TYPES, record
    assert all(isinstance(k, str) and k.isascii() for k in record)
    for engine in record.get("engines", ("postgresql", "mysql")):
        assert engine in ("postgresql", "mysql")
    if "table" in record:
        assert record["table"] in scenario.tables, record
    for statement in record.get("statements", []):
        assert statement in scenario.queries, record
    indexes = {index[0]: index for index in scenario.indexes}
    if record["type"] in ("drop_index", "keep_index") and "index" in record:
        assert record["index"] in indexes, record
        if "table" in record:
            assert indexes[record["index"]][1] == record["table"]
    if record["type"] == "add_index" and "columns" in record and not record.get("expression"):
        assert set(record["columns"]) <= set(scenario.columns(record["table"])), record
    if not must_not:
        assert record["plant"] and record.get("why"), record
        assert set(string.printable).issuperset(record["why"]), record


@pytest.mark.parametrize("name", NAMES)
def test_expected_records(name: str) -> None:
    module, scenario = SCENARIOS[name], app(name)
    assert module.EXPECTED
    for record in module.EXPECTED:
        _check_record(name, scenario, record, must_not=False)
    for record in module.MUST_NOT:
        _check_record(name, scenario, record, must_not=True)


@pytest.mark.parametrize("name", NAMES)
def test_docstring_names_every_plant(name: str) -> None:
    module = SCENARIOS[name]
    plants = {record["plant"] for record in module.EXPECTED}
    for plant in plants:
        assert plant in module.__doc__ or plant[0] + "1-" in module.__doc__, plant


def test_tiny_is_tiny() -> None:
    scenario = app("tiny")
    assert len(scenario.tables) == 3
    assert len(scenario.queries) < 8
    assert SCENARIOS["tiny"].App.default_rps <= 2


def test_iot_unused_indexes() -> None:
    module = SCENARIOS["iot"]
    secondary = [i for i in module.App.indexes if i[1] == "readings"]
    assert len(secondary) == 7
    dropped = {r["index"] for r in module.EXPECTED if r["type"] == "drop_index"}
    assert dropped == set(module.UNUSED) and len(dropped) == 4
    readers = " ".join(sql for sql in module.App.queries.values()
                       if sql.startswith(("SELECT", "DELETE")))
    # metric is filtered alongside device_id, whose (device_id, recorded_at) index wins.
    for column in ("quality", "batch_id", "received_at"):
        assert f"readings.{column}" not in readers


def test_messy_customers_has_40_columns() -> None:
    assert len(app("messy").columns("customers")) == 40


def test_healthy_expects_all_clear() -> None:
    module = SCENARIOS["healthy"]
    assert module.EXPECTED[0]["type"] == "no_action"
    kept = {r["index"] for r in module.EXPECTED if r["type"] == "keep_index"}
    assert kept == {i[0] for i in module.App.indexes}


def test_expand_lists_and_rows() -> None:
    sql, params = expand("SELECT x FROM t WHERE id IN ({values})", [1, 2, 3])
    assert sql.endswith("IN (%s, %s, %s)") and params == [1, 2, 3]
    sql, params = expand("INSERT INTO t (a, b) VALUES {values}", [(1, 2), (3, 4)])
    assert sql.endswith("VALUES (%s, %s), (%s, %s)") and params == [1, 2, 3, 4]
