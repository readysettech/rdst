from types import SimpleNamespace as NS
import pytest
import sqlglot
from features.ask.percentage_threshold import (
    plan_percentage_threshold,
    proven_fraction_witness,
    accepts_percentage_threshold,
)


def schema():
    return NS(
        tables={
            "batches": NS(
                columns={
                    n: NS(data_type=t)
                    for n, t in [
                        ("defect_rate", "double"),
                        ("failures", "int"),
                        ("inspected", "int"),
                        ("region", "text"),
                    ]
                }
            )
        }
    )


def test_only_literal_changes():
    sql = "SELECT COUNT(*) FROM batches b WHERE b.region='West' AND b.defect_rate < 0.5"
    p = plan_percentage_threshold(sql, "mysql", schema())
    assert p
    tree = sqlglot.parse_one(sql, read="mysql")
    new = sqlglot.parse_one(p.candidate_sql, read="mysql")
    assert "b.defect_rate < 0.005" in p.candidate_sql
    new.find(sqlglot.exp.LT).set(
        "expression", tree.find(sqlglot.exp.LT).expression.copy()
    )
    assert tree == new
    assert "West" not in p.proof_sql
    assert isinstance(
        sqlglot.parse_one(p.proof_sql, read="mysql").find(sqlglot.exp.Where).this,
        sqlglot.exp.Not,
    )
    assert proven_fraction_witness(p, [[20, 8, 0.0, 0.8, 0, 1, 0, 1, 0, 0, 5, 0]]) == (
        "failures",
        "inspected",
    )
    assert accepts_percentage_threshold(p, [[18]], [[1]])
    assert not accepts_percentage_threshold(p, [[18]], [[19]])


@pytest.mark.parametrize(
    "rows",
    [
        [[9, 5, 0, 1, 0, 1, 0, 1, 0, 0, 5, 0]],
        [[20, 3, 0, 1, 0, 1, 0, 1, 0, 0, 5, 0]],
        [[20, 5, 0, 100, 0, 1, 0, 1, 0, 0, 5, 0]],
        [[20, 5, 0, 1, 0, 1, 0, 1, 0, 0, 0, 0]],
        [[20, 5, 0, 1, 0, 1, 0, 1, 1, 0, 2, 0]],
        [[20, 5, 0, 1, 0, 1, 0, 1, None, 0, None, 0]],
        [[20, 5, 0, float("nan"), 0, 1, 0, 1, 0, 0, 5, 0]],
    ],
)
def test_unproven_rows_abstain(rows):
    p = plan_percentage_threshold(
        "SELECT COUNT(*) FROM batches WHERE defect_rate<1", "mysql", schema()
    )
    assert p
    assert proven_fraction_witness(p, rows) is None


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT defect_rate FROM batches WHERE defect_rate<1",
        "SELECT COUNT(*) FROM batches WHERE defect_rate<1 OR region='West'",
        "SELECT COUNT(*) FROM batches WHERE defect_rate<1 AND defect_rate>0",
        "SELECT COUNT(*) FROM batches WHERE failures<1",
        "SELECT COUNT(*) FROM batches WHERE defect_rate<RAND()",
        "SELECT COUNT(*) FROM batches WHERE defect_rate<1 GROUP BY region",
        "SELECT COUNT(*) FROM batches b JOIN batches c ON b.region=c.region WHERE b.defect_rate<1",
        "SELECT COUNT(*) FROM batches WHERE defect_rate<1; DELETE FROM batches",
    ],
)
def test_unsupported_shape(sql):
    assert plan_percentage_threshold(sql, "mysql", schema()) is None


def test_postgres_keeps_native_sql():
    assert (
        plan_percentage_threshold(
            "SELECT COUNT(*) FROM batches WHERE defect_rate<1", "postgres", schema()
        )
        is None
    )


def test_reversed_bound():
    p = plan_percentage_threshold(
        "SELECT COUNT(*) FROM batches WHERE 5<defect_rate", "mysql", schema()
    )
    assert p
    assert not p.narrows
    assert accepts_percentage_threshold(p, [[0]], [[10]])
    assert not accepts_percentage_threshold(p, [[2]], [[1]])


import asyncio
import json
from features.ask.service import AskService
from features.ask.engine.ask3.context import Ask3Context
from features.ask.engine.ask3.types import ExecutionResult
from features.ask.value_probe import create_percentage_threshold_probe_executor


class Decision:
    def __init__(self, unit="percentage", matches=True):
        self.unit, self.matches = unit, matches

    def generate_response(self, **kwargs):
        value = dict(
            column_binding="yes",
            requested_unit=self.unit,
            number_text="0.5" if self.matches else "5",
            condition_excerpt="defect rate below 0.5%",
            comparison="lt",
            threshold_count=1,
            condition_kind="threshold",
        )
        if kwargs.get("purpose") == "percentage_threshold_unit":
            value = dict(
                rate_unit="percent",
                proportion_multiplier="0.01",
                unit_expression="%",
                interpretation="absolute_threshold",
            )
        return {"response": json.dumps(value), "model": "scripted", "tokens_used": 12}


def context():
    return Ask3Context(
        question="Count batches with a defect rate below 0.5%",
        target="local",
        db_type="mysql",
        target_config={"engine": "mysql"},
        schema_info=schema(),
        sql="SELECT COUNT(*) FROM batches WHERE defect_rate<0.5",
        execution_result=ExecutionResult(rows=[[18]], columns=["count"], row_count=1),
        enforce_result_limit=False,
    )


@pytest.mark.parametrize("candidate,expected", [(1, "normalized"), (19, "reverted")])
def test_service_result_check_and_restoration(candidate, expected):
    calls = []

    def execute(sql, config):
        calls.append(sql)
        return {
            "success": True,
            "rows": [[20, 8, 0.0, 0.8, 0, 1, 0, 1, 0, 0, 5, 0]]
            if len(calls) == 1
            else [[candidate]],
            "columns": ["count", "distinct", "min", "max", "w1", "w2"]
            if len(calls) == 1
            else ["count"],
        }

    ctx = context()
    original = ctx.sql
    ctx = asyncio.run(
        AskService(
            llm_manager=Decision(), db_executor=execute
        )._apply_percentage_threshold(ctx)
    )
    assert ctx.percentage_threshold["status"] == expected and len(calls) == 2
    assert ctx.percentage_threshold_probe_diagnostics["calls"] == 2
    assert len(ctx.llm_calls) == 2
    assert (ctx.sql != original) == (expected == "normalized")
    assert ctx.execution_result.rows == [
        [candidate if expected == "normalized" else 18]
    ]
    restored = Ask3Context.from_dict(ctx.to_dict())
    assert restored.percentage_threshold == ctx.percentage_threshold
    assert (
        restored.percentage_threshold_probe_diagnostics
        == ctx.percentage_threshold_probe_diagnostics
    )


@pytest.mark.parametrize(
    "proof",
    [[20, 8, 0, 0.8, 0, 1, 0, 1, 0, 0, 0, 0], [20, 8, 0, 80, 0, 1, 0, 1, 0, 0, 4, 0]],
)
def test_service_rejects_ambiguous_or_percentage_storage(proof):
    calls = []

    def execute(sql, config):
        calls.append(sql)
        return {
            "success": True,
            "rows": [proof],
            "columns": ["count", "distinct", "min", "max", "w1", "w2"],
        }

    ctx = context()
    original = ctx.sql
    ctx = asyncio.run(
        AskService(
            llm_manager=Decision(), db_executor=execute
        )._apply_percentage_threshold(ctx)
    )
    assert ctx.sql == original and len(calls) == 1
    assert (
        ctx.percentage_threshold["reason"] == "fractional-storage-not-uniquely-proven"
    )


@pytest.mark.parametrize(
    "unit,matches",
    [("fraction", True), ("unknown", True), ("raw", True), ("percentage", False)],
)
def test_semantic_abstention_prevents_probes(unit, matches):
    def forbidden(*a):
        raise AssertionError("No probe allowed")

    ctx = asyncio.run(
        AskService(
            llm_manager=Decision(unit, matches), db_executor=forbidden
        )._apply_percentage_threshold(context())
    )
    assert ctx.percentage_threshold["reason"] == "model-abstained"


def test_budget_enforces_read_only_and_two_calls():
    ctx = context()
    calls = []

    def execute(sql, config):
        calls.append(sql)
        return {"success": True, "rows": [[1]], "columns": ["count"]}

    bounded = create_percentage_threshold_probe_executor(ctx, execute)
    assert not bounded("DELETE FROM batches", ctx.target_config)["success"]
    assert not calls
    bounded = create_percentage_threshold_probe_executor(ctx, execute)
    assert bounded("SELECT 1", ctx.target_config)["success"]
    assert bounded("SELECT 2", ctx.target_config)["success"]
    assert not bounded("SELECT 3", ctx.target_config)["success"]
    assert len(calls) == 2


def test_additional_context_abstains():
    ctx = context()
    ctx.provided_context = "Use raw values."
    ctx = asyncio.run(
        AskService(llm_manager=Decision())._apply_percentage_threshold(ctx)
    )
    assert (
        ctx.percentage_threshold["reason"] == "additional-context-requires-abstention"
    )


def test_budget_stops_after_shared_deadline(monkeypatch):
    from features.ask import value_probe

    now = [0.0]
    monkeypatch.setattr(value_probe.time, "monotonic", lambda: now[0])

    def forbidden(*a):
        raise AssertionError("Expired probe must not execute")

    ctx = context()
    bounded = create_percentage_threshold_probe_executor(ctx, forbidden)
    now[0] = 2.1
    assert not bounded("SELECT 1", ctx.target_config)["success"]
    assert ctx.percentage_threshold_probe_diagnostics["exhausted"]


def test_candidate_error_restores_primary():
    calls = []

    def execute(sql, config):
        calls.append(sql)
        return (
            {
                "success": True,
                "rows": [[20, 8, 0.0, 0.8, 0, 1, 0, 1, 0, 0, 5, 0]],
                "columns": ["n", "nd", "min", "max", "w1", "w2"],
            }
            if len(calls) == 1
            else {"success": False, "error": "test unavailable"}
        )

    ctx = context()
    original = ctx.sql
    ctx = asyncio.run(
        AskService(
            llm_manager=Decision(), db_executor=execute
        )._apply_percentage_threshold(ctx)
    )
    assert ctx.sql == original and ctx.execution_result.rows == [[18]]
    assert ctx.percentage_threshold["status"] == "reverted"


def test_largest_supported_proof_passes_sql_parser_limit():
    from features.ask.sql_validation import validate_sql_for_ask

    info = NS(
        tables={
            "readings": NS(
                columns={
                    **{"ratio": NS(data_type="double")},
                    **{f"n{i}": NS(data_type="int") for i in range(12)},
                }
            )
        }
    )
    plan = plan_percentage_threshold(
        "SELECT COUNT(*) FROM readings WHERE ratio<5", "mysql", info
    )
    assert plan is not None
    assert validate_sql_for_ask(plan.proof_sql, enforce_result_limit=False)["is_valid"]
    info.tables["readings"].columns["n12"] = NS(data_type="int")
    assert (
        plan_percentage_threshold(
            "SELECT COUNT(*) FROM readings WHERE ratio<5", "mysql", info
        )
        is None
    )


def test_multiple_range_thresholds_do_not_reach_unit_classifier():
    assert (
        plan_percentage_threshold(
            "SELECT COUNT(*) FROM batches WHERE defect_rate<5 AND failures>10",
            "mysql",
            schema(),
        )
        is None
    )


@pytest.mark.parametrize("incomplete_call", [1, 2])
def test_incomplete_native_proof_or_candidate_restores_original(incomplete_call):
    calls = []

    def execute(sql, config):
        calls.append(sql)
        return {
            "success": True,
            "rows": [[20, 8, 0, 0.8, 0, 1, 0, 1, 0, 0, 5, 0]]
            if len(calls) == 1
            else [[1]],
            "columns": ["n"],
            "truncated": len(calls) == incomplete_call,
        }

    before = context()
    ctx = asyncio.run(
        AskService(
            llm_manager=Decision(), db_executor=execute
        )._apply_percentage_threshold(before)
    )
    assert ctx.sql == "SELECT COUNT(*) FROM batches WHERE defect_rate<0.5"
    assert ctx.execution_result.rows == [[18]] and len(calls) == incomplete_call
    assert ctx.percentage_threshold["status"] != "normalized"
