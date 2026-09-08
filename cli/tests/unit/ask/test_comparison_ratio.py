from types import SimpleNamespace as NS
from decimal import Decimal
import asyncio
import json

import pytest
import sqlglot
from sqlglot import exp

from features.ask.comparison_ratio import (
    plan_comparison_ratio,
    proven_comparison_ratio,
    accepts_comparison_ratio,
)
from features.ask.service import AskService
from features.ask.engine.ask3.context import Ask3Context
from features.ask.engine.ask3.types import ExecutionResult

SQL = "SELECT COUNT(*) FROM estimates a JOIN estimates b ON a.category=b.category WHERE a.project='Pavilion' AND b.project='Fountain' AND a.category='Lighting' AND a.cost>b.cost"


def schema():
    return NS(
        tables={
            "estimates": NS(
                columns={
                    name: NS(data_type=kind)
                    for name, kind in (
                        ("cost", "decimal(12,2)"),
                        ("project", "text"),
                        ("category", "text"),
                    )
                }
            )
        }
    )


def plan(sql=SQL):
    return plan_comparison_ratio(sql, "mysql", schema())


def test_preserves_every_other_filter_and_join():
    p = plan()
    assert p
    proof = sqlglot.parse_one(p.proof_sql, read="mysql")
    assert proof.args["limit"].expression.this == "2"
    assert [c.sql() for c in proof.expressions] == ["a.cost", "b.cost"]
    expected = sqlglot.parse_one(SQL.rsplit(" AND ", 1)[0], read="mysql")
    assert proof.args["where"] == expected.args["where"]
    assert proof.args["joins"] == expected.args["joins"]
    for direction, numerator in [("left", "a.cost"), ("right", "b.cost")]:
        candidate = sqlglot.parse_one(p.candidate_sql(direction), read="mysql")
        assert candidate.args.get("limit") is None
        assert candidate.args["where"] == expected.args["where"]
        assert candidate.args["joins"] == expected.args["joins"]
        assert candidate.find(exp.Cast).this.sql() == numerator


@pytest.mark.parametrize(
    "sql",
    [
        SQL.replace("COUNT(*)", "COUNT(a.cost)"),
        SQL.replace("COUNT(*)", "COUNT(DISTINCT a.cost)"),
        SQL.replace("COUNT(*)", "COUNT(*), a.cost"),
        SQL.replace("a.cost>b.cost", "a.cost<b.cost"),
        SQL.replace("a.cost>b.cost", "a.project>b.project"),
        SQL.replace("a.cost>b.cost", "a.cost>b.cost OR a.cost=0"),
        SQL.replace("a.cost>b.cost", "a.cost>b.cost AND a.cost=b.cost"),
        SQL.replace("a.cost>b.cost", "a.cost>0"),
        SQL.replace("a.cost>b.cost", "a.cost>a.cost"),
        SQL.replace("a.cost>b.cost", "a.cost>RAND()"),
        SQL.replace("estimates a", "external.estimates a"),
        SQL + " GROUP BY a.project",
        SQL + " LIMIT 1",
        SQL + " FOR UPDATE",
        SQL + "; DELETE FROM estimates",
    ],
)
def test_unsupported_shapes(sql):
    assert plan(sql) is None


def test_postgres_parser_and_no_op():
    assert sqlglot.parse_one(SQL, read="postgres")
    assert plan_comparison_ratio(SQL, "postgres", schema()) is None


@pytest.mark.parametrize(
    "pair,count,side,expected",
    [
        ([[60, 20]], [[1]], "left", 3),
        ([[60, 20]], [[1]], "right", 1 / 3),
        ([[20, 60]], [[0]], "left", 1 / 3),
        ([[0, 60]], [[0]], "left", 0),
        ([[Decimal("63.25"), Decimal("12.50")]], [[1]], "left", 5.06),
        ([[60, 20], [80, 20]], [[2]], "left", None),
        ([[60, 20]], [[0]], "left", None),
        ([[60, 0]], [[1]], "left", None),
        ([[None, 20]], [[0]], "left", None),
        ([["60", 20]], [[1]], "left", None),
        ([[60, True]], [[1]], "left", None),
        ([[float("inf"), 20]], [[1]], "left", None),
        ([[2**54, 20]], [[1]], "left", None),
        ([[60, 20]], [[True]], "left", None),
        ([[60, 20]], [[1]], "unknown", None),
        ([], [[0]], "left", None),
    ],
)
def test_pair_uniqueness_count_agreement_and_orientation(pair, count, side, expected):
    actual = proven_comparison_ratio(pair, count, side)
    assert actual == expected


@pytest.mark.parametrize(
    "rows",
    [[], [[1]], [[None]], [[True]], [["3"]], [[float("nan")]], [[3], [3]], [[3, 1]]],
)
def test_candidate_must_equal_proven_ratio(rows):
    assert not accepts_comparison_ratio(3, rows)


class Decision:
    def __init__(self, **overrides):
        self.overrides = overrides

    def generate_response(self, **kwargs):
        return {
            "response": json.dumps(
                {
                    "ratio_only": True,
                    "compared_measure_matches": True,
                    "numerator_operand": "left",
                    "source_excerpt": "How many times",
                    "question_measure": "money",
                    "operand_measure": "money",
                    **self.overrides,
                }
            ),
            "model": "scripted",
            "tokens_used": 12,
        }


def context():
    return Ask3Context(
        question="How many times is Pavilion lighting cost that of Fountain?",
        target="local",
        db_type="mysql",
        target_config={"engine": "mysql"},
        schema_info=schema(),
        sql=SQL,
        execution_result=ExecutionResult(rows=[[1]], columns=["count"], row_count=1),
        enforce_result_limit=False,
    )


@pytest.mark.parametrize(
    "candidate,status", [(3, "normalized"), (4, "reverted"), (None, "reverted")]
)
def test_service_proof_candidate_restoration_and_serialization(candidate, status):
    calls = []

    def execute(sql, config):
        calls.append(sql)
        return {
            "success": True,
            "rows": [[60, 20]] if len(calls) == 1 else [[candidate]],
            "columns": ["left", "right"] if len(calls) == 1 else ["ratio"],
        }

    ctx = context()
    ctx = asyncio.run(
        AskService(llm_manager=Decision(), db_executor=execute)._apply_comparison_ratio(
            ctx
        )
    )
    assert len(calls) == 2 and ctx.comparison_ratio_probe_diagnostics["calls"] == 2
    assert ctx.comparison_ratio["status"] == status
    assert (ctx.sql != SQL) == (status == "normalized")
    assert ctx.execution_result.rows == [[candidate if status == "normalized" else 1]]
    assert len(ctx.llm_calls) == 1
    restored = Ask3Context.from_dict(ctx.to_dict())
    assert restored.comparison_ratio == ctx.comparison_ratio
    assert (
        restored.comparison_ratio_probe_diagnostics
        == ctx.comparison_ratio_probe_diagnostics
    )


@pytest.mark.parametrize(
    "decision",
    [
        {"ratio_only": False},
        {"compared_measure_matches": False},
        {"numerator_operand": "unknown"},
        {"source_excerpt": "invented"},
    ],
)
def test_semantic_abstention_prevents_queries(decision):
    def forbidden(*args):
        raise AssertionError("No probe allowed")

    ctx = asyncio.run(
        AskService(
            llm_manager=Decision(**decision), db_executor=forbidden
        )._apply_comparison_ratio(context())
    )
    assert ctx.sql == SQL and ctx.comparison_ratio["reason"] == "model-abstained"


def test_ambiguous_pair_stops_after_proof():
    calls = []

    def execute(*args):
        calls.append(args)
        return {
            "success": True,
            "rows": [[60, 20], [80, 20]],
            "columns": ["left", "right"],
        }

    ctx = asyncio.run(
        AskService(llm_manager=Decision(), db_executor=execute)._apply_comparison_ratio(
            context()
        )
    )
    assert len(calls) == 1 and ctx.sql == SQL
    assert ctx.comparison_ratio["reason"] == "single-comparison-pair-not-proven"


SCALAR_SQL = "SELECT COUNT(*) FROM estimates a WHERE a.project='Pavilion' AND a.category='Lighting' AND a.cost > (SELECT b.cost FROM estimates b WHERE b.project='Fountain' AND b.category='Lighting')"


def test_scalar_reference_keeps_both_populations_and_reference_cardinality():
    p = plan(SCALAR_SQL)
    assert p and p.scalar_reference
    before = sqlglot.parse_one(SCALAR_SQL, read="mysql")
    pair = sqlglot.parse_one(p.proof_sql, read="mysql")
    candidate = sqlglot.parse_one(p.candidate_sql("left"), read="mysql")
    assert pair.find(exp.Subquery) == before.find(exp.Subquery)
    assert candidate.find(exp.Subquery) == before.find(exp.Subquery)
    assert pair.args["limit"].expression.this == "2"
    assert candidate.args.get("limit") is None
    assert pair.expressions[0].sql() == "a.cost"
    assert candidate.args["where"] == pair.args["where"]
    assert "a.project = 'Pavilion'" in pair.sql()
    assert "a.category = 'Lighting'" in pair.sql()
    assert candidate.find(exp.Cast).this.sql() == "a.cost"
    reverse = sqlglot.parse_one(p.candidate_sql("right"), read="mysql")
    assert reverse.find(exp.Cast).this == before.find(exp.Subquery)
    assert reverse.find(exp.Nullif).this.sql() == "a.cost"


@pytest.mark.parametrize(
    "sql",
    [
        SCALAR_SQL.replace("b.cost FROM", "b.category FROM"),
        SCALAR_SQL.replace("b.cost FROM", "SUM(b.cost) FROM"),
        SCALAR_SQL.replace("b.cost FROM", "cost FROM"),
        SCALAR_SQL.replace("b.category='Lighting'", "b.category=a.category"),
        SCALAR_SQL.replace("b.category='Lighting'", "b.category='Lighting' LIMIT 1"),
        SCALAR_SQL.replace(
            "b.category='Lighting'", "b.category='Lighting' ORDER BY b.cost"
        ),
        SCALAR_SQL.replace(
            "b.category='Lighting'", "b.category='Lighting' GROUP BY b.cost"
        ),
        SCALAR_SQL.replace("SELECT b.cost", "SELECT DISTINCT b.cost"),
        SCALAR_SQL.replace("SELECT b.cost", "SELECT b.cost, b.project"),
        SCALAR_SQL.replace("b.cost FROM", "(SELECT c.cost FROM estimates c) FROM"),
        SCALAR_SQL.replace("a.cost >", "a.category >"),
        SCALAR_SQL.replace("a.cost >", "a.cost <"),
        SCALAR_SQL.replace("a.cost >", "a.cost + 1 >"),
        SCALAR_SQL.replace("AND a.cost >", "OR a.cost >"),
        SCALAR_SQL.replace("AND a.cost >", "AND NOT a.cost >"),
        SCALAR_SQL.replace("a.project='Pavilion'", "a.project=unknown.project"),
        SCALAR_SQL.replace("estimates b", "elsewhere.estimates b"),
        SCALAR_SQL.replace("b.project='Fountain'", "b.cost>RAND()"),
        SCALAR_SQL + " LIMIT 1",
        SCALAR_SQL + " GROUP BY a.project",
    ],
)
def test_scalar_reference_rejects_correlations_ambiguity_and_changed_grain(sql):
    assert plan(sql) is None


def test_scalar_reference_preserves_postgresql_no_op():
    assert sqlglot.parse_one(SCALAR_SQL, read="postgres")
    assert plan_comparison_ratio(SCALAR_SQL, "postgres", schema()) is None


@pytest.mark.parametrize(
    "proof",
    [
        {"success": False, "error": "Subquery returns more than 1 row"},
        {"success": True, "rows": [[60, None]]},
        {"success": True, "rows": [[60, 20], [60, 20]]},
    ],
)
def test_scalar_reference_unavailable_or_nonunique_pair_preserves_primary(proof):
    calls = []

    def execute(sql, config):
        calls.append(sql)
        return proof

    ctx = context()
    ctx.sql = SCALAR_SQL
    actual = asyncio.run(
        AskService(llm_manager=Decision(), db_executor=execute)._apply_comparison_ratio(
            ctx
        )
    )
    assert actual.sql == SCALAR_SQL
    assert actual.execution_result.rows == [[1]]
    assert len(calls) == 1
    assert actual.comparison_ratio["status"] != "normalized"


@pytest.mark.parametrize(
    "question_measure,operand_measure,expected",
    [
        ("money", "money", True),
        ("quantity", "quantity", True),
        ("money", "quantity", False),
        ("quantity", "money", False),
        ("unknown", "unknown", False),
        (None, "money", False),
    ],
)
def test_scalar_reference_checks_independent_measure_kinds(
    question_measure, operand_measure, expected
):
    from features.ask.comparison_ratio import route_comparison_ratio

    result = route_comparison_ratio(
        context().question,
        SCALAR_SQL,
        "mysql",
        Decision(question_measure=question_measure, operand_measure=operand_measure),
    )
    assert result["activate"] == expected


def test_joined_comparison_keeps_legacy_request_shape():
    from features.ask.comparison_ratio import route_comparison_ratio, CATALOG, SCHEMA

    class Capture(Decision):
        def generate_response(self, **kw):
            assert json.loads(kw["prompt"])["trigger_catalog"] == CATALOG
            assert kw["extra"]["response_format"]["json_schema"]["schema"] == SCHEMA
            return super().generate_response(**kw)

    assert route_comparison_ratio(context().question, SQL, "mysql", Capture())[
        "activate"
    ]
