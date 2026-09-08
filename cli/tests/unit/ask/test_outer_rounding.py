from decimal import Decimal
import pytest
import sqlglot
from sqlglot import exp
from features.ask.outer_rounding import (
    plan_outer_rounding,
    proved_unrounded_value,
    accepts_unrounded,
)

SQL = "SELECT ROUND(CAST(100*SUM(CASE WHEN s.delivered=1 THEN 1 ELSE 0 END) AS DOUBLE)/COUNT(*),2) AS pct FROM shipments s JOIN depots d ON s.depot_id=d.id WHERE d.region='West'"


def test_only_outer_round_is_removed_and_alias_population_preserved():
    p = plan_outer_rounding(SQL, "mysql")
    assert p and p.decimals == 2
    a = sqlglot.parse_one(SQL, read="mysql")
    b = sqlglot.parse_one(p.candidate_sql, read="mysql")
    proof = sqlglot.parse_one(p.proof_sql, read="mysql")
    assert b.expressions[0].alias == a.expressions[0].alias
    assert b.expressions[0].this == a.expressions[0].this.this
    assert proof.expressions == [a.expressions[0].this, a.expressions[0].this.this]
    for k in ["from_", "joins", "where"]:
        assert a.args[k] == b.args[k] == proof.args[k]


@pytest.mark.parametrize(
    "sql",
    [
        SQL + " GROUP BY d.id",
        SQL + " ORDER BY 1",
        SQL + " LIMIT 1",
        SQL + " FOR UPDATE",
        SQL.replace("SELECT ROUND", "SELECT DISTINCT ROUND"),
        SQL.replace(" AS pct", " AS pct, d.id"),
        SQL.replace(",2)", ",-1)"),
        SQL.replace(",2)", ",s.delivered)"),
        SQL.replace(",2)", ",'2')"),
        SQL.replace(",2)", ",13)"),
        SQL.replace("COUNT(*)", "RAND()"),
        SQL.replace("COUNT(*)", "SLEEP(1)"),
        SQL.replace("COUNT(*)", "ROUND(COUNT(*))"),
        SQL.replace("COUNT(*)", "(SELECT COUNT(*) FROM depots)"),
        SQL.replace("shipments s", "elsewhere.shipments s"),
        "SELECT ROUND(depth,2) FROM gauges",
        "SELECT AVG(depth) FROM gauges",
    ],
)
def test_unsupported_queries_abstain(sql):
    assert plan_outer_rounding(sql, "mysql") is None


def test_postgresql_compatibility_is_no_op():
    assert sqlglot.parse_one("SELECT ROUND(AVG(depth),2) FROM gauges", read="postgres")
    assert (
        plan_outer_rounding("SELECT ROUND(AVG(depth),2) FROM gauges", "postgres")
        is None
    )


@pytest.mark.parametrize(
    "old,proof",
    [
        ([[Decimal("14.29")]], [[Decimal("14.29"), 100 / 7]]),
        ([[Decimal("2.5")]], [[Decimal("2.5"), Decimal("2.49")]]),
        ([[0]], [[0, Decimal("0.004")]]),
        ([[-2.5]], [[-2.5, -2.49]]),
    ],
)
def test_exact_simultaneous_witness_selects_inner_value(old, proof):
    expected = proved_unrounded_value(old, proof)
    assert expected is not None
    assert accepts_unrounded(expected, [[proof[0][1]]])
    assert not accepts_unrounded(expected, old)
    assert not accepts_unrounded(expected, [[proof[0][1]], [proof[0][1]]])


@pytest.mark.parametrize(
    "old,proof",
    [
        ([[14.29]], [[14.28, 100 / 7]]),
        ([[14.29]], [[14.29, None]]),
        ([[None]], [[None, 1]]),
        ([[14.29]], [[14.29, float("nan")]]),
        ([[14.29]], [[14.29, float("inf")]]),
        ([[14.29]], [[14.29, "14.2857"]]),
        ([[14.29]], [[14.29, True]]),
        ([[14.29]], [[14.29, 14.29]]),
        ([[14.29]], [[14.29, 14.2857], [14.29, 14.2857]]),
        ([], [[14.29, 14.2857]]),
        ([[14.29, 2]], [[14.29, 14.2857]]),
    ],
)
def test_failed_ambiguous_non_numeric_and_no_change_witnesses_abstain(old, proof):
    assert proved_unrounded_value(old, proof) is None
    assert not accepts_unrounded(None, [[1]])


import asyncio, json
from types import SimpleNamespace as NS
from features.ask.service import AskService
from features.ask.engine.ask3.context import Ask3Context
from features.ask.engine.ask3.types import ExecutionResult


class Decision:
    def __init__(self, **overrides):
        self.overrides = overrides

    def generate_response(self, **kwargs):
        return {
            "response": json.dumps(
                {
                    "output_request": "numeric_value",
                    "output_kind": "percentage",
                    "precision_requirement": "unspecified",
                    "source_excerpt": "percentage of delivered shipments",
                    **self.overrides,
                }
            ),
            "model": "scripted",
            "tokens_used": 20,
        }


def context():
    schema = NS(
        tables={
            "shipments": NS(
                columns={n: NS(data_type="int") for n in ["delivered", "depot_id"]}
            ),
            "depots": NS(
                columns={"id": NS(data_type="int"), "region": NS(data_type="text")}
            ),
        }
    )
    return Ask3Context(
        question="Give the percentage of delivered shipments.",
        target="local",
        db_type="mysql",
        target_config={"engine": "mysql"},
        schema_info=schema,
        sql=SQL,
        execution_result=ExecutionResult(
            rows=[[Decimal("14.29")]], columns=["pct"], row_count=1
        ),
        enforce_result_limit=False,
    )


@pytest.mark.parametrize(
    "candidate,status",
    [(100 / 7, "normalized"), (14.2, "reverted"), (None, "reverted")],
)
def test_service_witness_result_acceptance_and_restoration(candidate, status):
    calls = []

    def execute(sql, config):
        calls.append(sql)
        return {
            "success": True,
            "rows": [[Decimal("14.29"), 100 / 7]] if len(calls) == 1 else [[candidate]],
            "columns": ["rounded", "unrounded"] if len(calls) == 1 else ["pct"],
        }

    ctx = asyncio.run(
        AskService(llm_manager=Decision(), db_executor=execute)._apply_outer_rounding(
            context()
        )
    )
    assert ctx.outer_rounding["status"] == status
    assert len(calls) == 2 and ctx.outer_rounding_probe_diagnostics["max_calls"] == 2
    assert (ctx.sql != SQL) == (status == "normalized")
    assert ctx.execution_result.rows == (
        [[candidate]] if status == "normalized" else [[Decimal("14.29")]]
    )
    assert len(ctx.llm_calls) == 1
    restored = Ask3Context.from_dict(ctx.to_dict())
    assert (
        restored.outer_rounding == ctx.outer_rounding
        and restored.outer_rounding_probe_diagnostics
        == ctx.outer_rounding_probe_diagnostics
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"output_request": "entity"},
        {"output_kind": "money"},
        {"precision_requirement": "fixed"},
        {"precision_requirement": "unknown"},
        {"source_excerpt": "made up"},
    ],
)
def test_semantic_abstention_avoids_database_access(changes):
    def forbidden(*args):
        raise AssertionError("No query permitted")

    ctx = asyncio.run(
        AskService(
            llm_manager=Decision(**changes), db_executor=forbidden
        )._apply_outer_rounding(context())
    )
    assert ctx.sql == SQL and ctx.outer_rounding["reason"] == "model-abstained"


def test_missing_schema_prevents_database_access():
    def forbidden(*args):
        raise AssertionError("No query permitted")

    ctx = context()
    ctx.schema_info = None
    actual = asyncio.run(
        AskService(llm_manager=Decision(), db_executor=forbidden)._apply_outer_rounding(
            ctx
        )
    )
    assert (
        actual.sql == SQL
        and actual.outer_rounding["reason"] == "candidate-validation-failed"
    )


@pytest.mark.parametrize(
    "proof",
    [
        {"success": False, "error": "timeout"},
        {"success": True, "rows": [[14.28, 100 / 7]]},
        {"success": True, "rows": [[14.29, None]]},
    ],
)
def test_failed_witness_stops_after_first_query(proof):
    calls = []

    def execute(*args):
        calls.append(args)
        return proof

    ctx = asyncio.run(
        AskService(llm_manager=Decision(), db_executor=execute)._apply_outer_rounding(
            context()
        )
    )
    assert (
        len(calls) == 1
        and ctx.sql == SQL
        and ctx.outer_rounding["reason"] == "unsafe-or-unavailable-proof"
    )


def test_malformed_classification_is_recorded_before_restoration():
    class Malformed:
        def generate_response(self, **kwargs):
            return {"response": "invalid JSON", "model": "scripted", "tokens_used": 3}

    def forbidden(*args):
        raise AssertionError("No query permitted")

    ctx = asyncio.run(
        AskService(
            llm_manager=Malformed(), db_executor=forbidden
        )._apply_outer_rounding(context())
    )
    assert ctx.sql == SQL and len(ctx.llm_calls) == 1
    assert ctx.outer_rounding["status"] == "reverted"
