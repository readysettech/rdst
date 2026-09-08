from decimal import Decimal
from types import SimpleNamespace
import asyncio
import json

import pytest
import sqlglot
from sqlglot import exp

from features.ask.mean_precision import (
    plan_integer_mean,
    safe_mean_proof,
    accepts_mean_result,
)
from features.ask.engine.ask3.context import Ask3Context
from features.ask.engine.ask3.types import ExecutionResult
from features.ask.service import AskService


def schema(kind="int"):
    return SimpleNamespace(
        tables={
            "shipments": SimpleNamespace(
                columns={
                    "id": SimpleNamespace(data_type="int"),
                    "units": SimpleNamespace(data_type=kind),
                    "depot_id": SimpleNamespace(data_type="int"),
                }
            ),
            "depots": SimpleNamespace(columns={"id": SimpleNamespace(data_type="int")}),
        }
    )


def test_mean_plan_preserves_join_population_and_null_denominator():
    sql = "SELECT AVG(s.units) AS mean_units FROM shipments s JOIN depots d ON s.depot_id=d.id WHERE d.id > 2"
    plan = plan_integer_mean(sql, "mysql", schema())
    assert plan is not None
    tree = sqlglot.parse_one(plan.candidate_sql, read="mysql")
    original = sqlglot.parse_one(sql, read="mysql")
    assert tree.args["joins"] == original.args["joins"]
    assert tree.args["where"] == original.args["where"]
    assert tree.find(exp.Count).this == exp.column("units", table="s")
    assert tree.find(exp.Nullif) is not None
    assert safe_mean_proof(((Decimal(7), 3),))
    assert accepts_mean_result(((Decimal("2.3333"),),), ((7 / 3,),))


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT AVG(DISTINCT units) FROM shipments",
        "SELECT AVG(units) OVER () FROM shipments",
        "SELECT depot_id, AVG(units) FROM shipments GROUP BY depot_id",
        "SELECT ROUND(AVG(units), 2) FROM shipments",
        "SELECT AVG(CAST(units AS DECIMAL(20, 2))) FROM shipments",
        "SELECT AVG(units) FROM shipments WHERE RAND() > 0.5",
        "SELECT AVG(units) FROM shipments; DELETE FROM shipments",
        "SELECT AVG(id) FROM shipments s JOIN depots d ON s.depot_id=d.id",
    ],
)
def test_unsupported_or_ambiguous_shapes_abstain(sql):
    assert plan_integer_mean(sql, "mysql", schema()) is None


@pytest.mark.parametrize("kind", ["decimal(12,2)", "double", "text", "unknown"])
def test_noninteger_measures_abstain(kind):
    assert (
        plan_integer_mean("SELECT AVG(units) FROM shipments", "mysql", schema(kind))
        is None
    )


def test_postgresql_keeps_native_numeric_average():
    assert (
        plan_integer_mean(
            'SELECT AVG("units") FROM "shipments"', "postgresql", schema()
        )
        is None
    )


@pytest.mark.parametrize(
    "rows",
    [((None, 0),), ((2**53, 2),), ((5, 2**53),), ((5, 0),), ((Decimal("1.5"), 2),), ()],
)
def test_unsafe_or_empty_proof_rejected(rows):
    assert not safe_mean_proof(rows)


def test_candidate_must_agree_with_native_average_and_be_complete():
    assert not accepts_mean_result(((Decimal("2.3333"),),), ((3.5,),))
    assert not accepts_mean_result(((Decimal("2.3333"),),), ((float("nan"),),))
    assert not accepts_mean_result(((None,),), ((0.0,),))
    assert not accepts_mean_result(((Decimal("2.3333"),),), ((2.33333,), (2.33333,)))


class MeanDecision:
    def __init__(self, decision="activate"):
        self.decision = decision
        self.prompts = []

    def generate_response(self, **kwargs):
        self.prompts.append(json.loads(kwargs["prompt"]))
        return {
            "response": json.dumps(
                {
                    "decision": self.decision,
                    "measure_kind": "quantity",
                    "source_excerpt": "Average units",
                }
            ),
            "model": "scripted",
            "tokens_used": 12,
        }


def context():
    return Ask3Context(
        question="Average units shipped",
        target="logistics",
        db_type="mysql",
        target_config={"engine": "mysql"},
        schema_info=schema(),
        sql="SELECT AVG(units) AS mean_units FROM shipments",
        execution_result=ExecutionResult(
            rows=[[Decimal("2.3333")]], columns=["mean_units"], row_count=1
        ),
        enforce_result_limit=False,
    )


@pytest.mark.parametrize(
    "candidate,expected_status", [(7 / 3, "normalized"), (10.0, "reverted")]
)
def test_canonical_service_checks_candidate_and_restores_on_disagreement(
    candidate, expected_status
):
    calls = []

    def execute(sql, config):
        calls.append(sql)
        if len(calls) == 1:
            return {
                "success": True,
                "rows": [[Decimal(7), 3]],
                "columns": ["sum", "count"],
            }
        return {"success": True, "rows": [[candidate]], "columns": ["mean_units"]}

    ctx = context()
    original = ctx.sql
    llm = MeanDecision()
    service = AskService(
        llm_manager=llm, db_executor=execute, integer_mean_precision_enabled=True
    )
    result = asyncio.run(service._apply_integer_mean_precision(ctx))
    assert result.integer_mean_precision["status"] == expected_status
    assert len(calls) == 2
    assert len(result.llm_calls) == 1
    assert result.integer_mean_probe_diagnostics["calls"] == 2
    assert "COUNT(units)" in calls[0]
    if expected_status == "reverted":
        assert result.sql == original
        assert result.execution_result.rows == [[Decimal("2.3333")]]
    else:
        assert result.sql != original
        assert result.execution_result.rows == [[candidate]]


def test_model_abstention_does_not_probe_or_change_sql():
    ctx = context()

    def forbidden(*args):
        raise AssertionError("No database call expected")

    service = AskService(llm_manager=MeanDecision("abstain"), db_executor=forbidden)
    result = asyncio.run(service._apply_integer_mean_precision(ctx))
    assert result.integer_mean_precision["reason"] == "model-abstained"
    assert result.sql == "SELECT AVG(units) AS mean_units FROM shipments"


def test_mean_diagnostics_survive_context_serialization():
    ctx = context()
    ctx.integer_mean_precision = {"status": "normalized"}
    ctx.integer_mean_probe_diagnostics = {"calls": 2}
    restored = Ask3Context.from_dict(ctx.to_dict())
    assert restored.integer_mean_precision == ctx.integer_mean_precision
    assert restored.integer_mean_probe_diagnostics == ctx.integer_mean_probe_diagnostics


def test_pure_calendar_and_boolean_filters_preserve_mean_population():
    sql = "SELECT AVG(units) FROM shipments WHERE depot_id = 4 AND DATE(created_at) BETWEEN '2024-01-01' AND '2024-12-31'"
    plan = plan_integer_mean(sql, "mysql", schema())
    assert plan is not None
    original = sqlglot.parse_one(sql, read="mysql")
    candidate = sqlglot.parse_one(plan.candidate_sql, read="mysql")
    proof = sqlglot.parse_one(plan.proof_sql, read="mysql")
    assert candidate.args["where"] == proof.args["where"] == original.args["where"]
    assert candidate.find(exp.Count).this == exp.column("units")


@pytest.mark.parametrize(
    "predicate",
    [
        "DATE(CURRENT_TIMESTAMP()) = '2024-01-01'",
        "depot_id=4 AND RAND()>0.5",
        "DATE(custom_timestamp(created_at))='2024-01-01'",
    ],
)
def test_nonconstant_calendar_or_unknown_predicate_functions_still_abstain(predicate):
    assert (
        plan_integer_mean(
            f"SELECT AVG(units) FROM shipments WHERE {predicate}", "mysql", schema()
        )
        is None
    )


def test_postgresql_filtered_mean_keeps_native_numeric_semantics():
    assert (
        plan_integer_mean(
            "SELECT AVG(units) FROM shipments WHERE depot_id=4 AND DATE(created_at)='2024-01-01'",
            "postgresql",
            schema(),
        )
        is None
    )


@pytest.mark.parametrize("kind", ["money", "identifier", "unknown", None])
def test_nonquantity_classification_cannot_activate_even_with_positive_decision(kind):
    from features.ask.mean_precision import route_mean_precision

    class ConflictingManager:
        def generate_response(self, **kwargs):
            return {
                "response": json.dumps(
                    {
                        "decision": "activate",
                        "measure_kind": kind,
                        "source_excerpt": "Average loan amount",
                    }
                ),
                "model": "scripted",
            }

    assert not route_mean_precision(
        "Average loan amount",
        "SELECT AVG(amount) FROM advances",
        "mysql",
        ConflictingManager(),
    )["activate"]


@pytest.mark.parametrize("field", ["provided_context", "conversation_context"])
def test_additional_precision_context_abstains_before_routing(field):
    ctx = context()
    setattr(ctx, field, "Keep the native precision specified earlier.")
    original = ctx.sql
    result = asyncio.run(AskService()._apply_integer_mean_precision(ctx))
    assert (
        result.integer_mean_precision["reason"]
        == "additional-context-requires-abstention"
    )
    assert result.sql == original
    assert not result.llm_calls


@pytest.mark.parametrize("factor", ["", "100 * ", "100.0 * "])
def test_binary_mean_preserves_case_and_population(factor):
    sql = f"SELECT {factor}AVG(CASE WHEN s.units>2 THEN 1 ELSE 0 END) AS share FROM shipments s JOIN depots d ON s.depot_id=d.id WHERE d.id>3"
    plan = plan_integer_mean(sql, "mysql", schema())
    assert plan is not None and plan.binary
    assert plan.scale == (100 if factor else 1)
    before = sqlglot.parse_one(sql, read="mysql")
    after = sqlglot.parse_one(plan.candidate_sql, read="mysql")
    assert (
        before.args["joins"] == after.args["joins"]
        and before.args["where"] == after.args["where"]
    )
    case = before.find(exp.Case)
    assert after.find(exp.Sum).this == case and after.find(exp.Count).this == case
    proof = sqlglot.parse_one(plan.proof_sql, read="mysql")
    assert len(proof.expressions) == 2
    assert (
        isinstance(proof.expressions[0], exp.Sum) and proof.expressions[0].this == case
    )
    assert (
        isinstance(proof.expressions[1], exp.Count)
        and proof.expressions[1].this == case
    )


@pytest.mark.parametrize(
    "expression",
    [
        "AVG(CASE WHEN units>2 THEN 1 ELSE 0 END)*100",
        "AVG(CASE WHEN units>2 THEN 0 ELSE 1 END)",
        "AVG(CASE WHEN units IS NULL THEN 1 ELSE 0 END)",
    ],
)
def test_reverse_scale_membership_and_missing_values_supported(expression):
    plan = plan_integer_mean(
        "SELECT " + expression + " FROM shipments", "mysql", schema()
    )
    assert plan is not None and plan.binary


@pytest.mark.parametrize(
    "expression",
    [
        "AVG(CASE WHEN units>2 THEN 1 END)",
        "AVG(CASE WHEN units>2 THEN 1 ELSE NULL END)",
        "AVG(CASE WHEN units>2 THEN 2 ELSE 0 END)",
        "AVG(CASE WHEN units>2 THEN 1.0 ELSE 0 END)",
        "AVG(CASE WHEN units>2 THEN units ELSE 0 END)",
        "AVG(CASE WHEN RAND()>0.5 THEN 1 ELSE 0 END)",
        "AVG(CASE units WHEN 2 THEN 1 ELSE 0 END)",
        "AVG(CASE WHEN units>2 THEN 1 ELSE 0 END)*5",
        "ROUND(100*AVG(CASE WHEN units>2 THEN 1 ELSE 0 END),2)",
        "AVG(CASE WHEN units>2 THEN 1 ELSE 0 END) OVER ()",
        "AVG(DISTINCT CASE WHEN units>2 THEN 1 ELSE 0 END)",
        "100*AVG(units)",
    ],
)
def test_unsupported_binary_and_rendered_means_abstain(expression):
    assert (
        plan_integer_mean("SELECT " + expression + " FROM shipments", "mysql", schema())
        is None
    )


def test_binary_precision_does_not_change_postgresql_or_grouped_shape():
    sql = "SELECT AVG(CASE WHEN units>2 THEN 1 ELSE 0 END) FROM shipments"
    assert plan_integer_mean(sql, "postgres", schema()) is None
    assert plan_integer_mean(sql + " GROUP BY depot_id", "mysql", schema()) is None
    assert plan_integer_mean(sql + " ORDER BY 1 LIMIT 1", "mysql", schema()) is None


def test_binary_sum_and_scaled_rounding_proofs():
    assert safe_mean_proof([[1, 7]], binary=True)
    assert safe_mean_proof([[0, 7]], binary=True)
    assert not safe_mean_proof([[-1, 7]], binary=True)
    assert not safe_mean_proof([[8, 7]], binary=True)
    assert not safe_mean_proof([[0, 0]], binary=True)
    assert accepts_mean_result([[Decimal("14.2900")]], [[100 / 7]], scale=100)
    assert not accepts_mean_result([[Decimal("14.2900")]], [[100 / 7]])
    assert not accepts_mean_result([[Decimal("14.2900")]], [[14.2]], scale=100)
    assert not accepts_mean_result([[Decimal("14.2900")]], [[100 / 7]], scale=1000)


@pytest.mark.parametrize(
    "units,scale,expected",
    [
        ("percentage", 100, True),
        ("fraction", 1, True),
        ("percentage", 1, False),
        ("fraction", 100, False),
        ("unknown", 100, False),
        (None, 100, False),
    ],
)
def test_binary_units_are_checked_independently_of_model_activation(
    units, scale, expected
):
    from features.ask.mean_precision import route_mean_precision

    class Decision:
        def generate_response(self, **kw):
            assert (
                "output_units"
                in kw["extra"]["response_format"]["json_schema"]["schema"]["required"]
            )
            return {
                "response": json.dumps(
                    {
                        "decision": "activate",
                        "measure_kind": "quantity",
                        "source_excerpt": "percentage of rows",
                        "output_units": units,
                    }
                )
            }

    sql = ("100*" if scale == 100 else "") + "AVG(CASE WHEN units>2 THEN 1 ELSE 0 END)"
    result = route_mean_precision(
        "Give percentage of rows",
        "SELECT " + sql + " FROM shipments",
        "mysql",
        Decision(),
    )
    assert result["activate"] == expected
