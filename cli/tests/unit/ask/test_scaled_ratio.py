import importlib.util
import sys
from pathlib import Path
import math
import pytest
import sqlglot
from sqlglot import exp
from features.ask import scaled_ratio as m


@pytest.mark.parametrize(
    "formula",
    [
        "CAST(SUM(part) AS DOUBLE) / SUM(total) * 100",
        "100 * (CAST(SUM(part) AS DOUBLE) / SUM(total))",
        "(CAST(SUM(CASE WHEN active=1 THEN value ELSE 0 END) AS DOUBLE) / SUM(value)) * 100.0",
        "CAST((SELECT SUM(s.amount) FROM samples s WHERE s.kind='target') AS DOUBLE) / SUM(amount) * 100",
    ],
)
def test_only_scalar_multiplication_placement_changes(formula):
    sql = "SELECT " + formula + " AS share FROM samples WHERE approved=1"
    p = m.plan_scaled_ratio(sql, "mysql")
    assert p
    before = sqlglot.parse_one(sql, read="mysql")
    after = sqlglot.parse_one(p.candidate_sql, read="mysql")
    root = after.expressions[0].this
    assert isinstance(root, exp.Div) and isinstance(root.this, exp.Mul)
    original_div = next(before.expressions[0].find_all(exp.Div))
    assert (
        root.this.this == original_div.this
        and root.expression == original_div.expression
    )
    assert root.this.expression.sql(dialect="mysql") in ("100", "100.0")
    after.set("expressions", before.expressions)
    assert before == after


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT SUM(part)/SUM(total)*100 FROM samples",
        "SELECT CAST(SUM(part) AS DECIMAL(10,2))/SUM(total)*100 FROM samples",
        "SELECT CAST(part AS DOUBLE)/total*100 FROM samples",
        "SELECT ROUND(CAST(SUM(part) AS DOUBLE)/SUM(total)*100,2) FROM samples",
        "SELECT CAST(SUM(part) AS DOUBLE)*100/SUM(total) FROM samples",
        "SELECT CAST(SUM(part) AS DOUBLE)/SUM(total)*100.00000000000000001 FROM samples",
        "SELECT CAST(SUM(part) AS DOUBLE)/SUM(total)*10 FROM samples",
        "SELECT CAST(SUM(part) AS DOUBLE)/SUM(total)*100 FROM samples GROUP BY region",
        "SELECT CAST(SUM(part) AS DOUBLE)/SUM(total)*100 AS p FROM samples ORDER BY p LIMIT 1",
        "SELECT CAST(SUM(part) AS DOUBLE)/SUM(total)*100,COUNT(*) FROM samples",
        "SELECT CAST(SUM(part) AS DOUBLE)/SUM(total)*100 FROM samples WHERE RAND()>0.5",
        "SELECT CAST((SELECT SUM(amount) FROM samples GROUP BY region) AS DOUBLE)/SUM(total)*100 FROM samples",
        "SELECT CAST(SUM(part) AS DOUBLE)/SUM(total)*100 FROM samples; DELETE FROM samples",
    ],
)
def test_unsupported_or_explicit_math_abstains(sql):
    assert m.plan_scaled_ratio(sql, "mysql") is None


def test_postgres_noop():
    sql = "SELECT CAST(SUM(part) AS DOUBLE PRECISION)/SUM(total)*100 FROM samples"
    assert sqlglot.parse_one(sql, read="postgres")
    assert m.plan_scaled_ratio(sql, "postgres") is None


from decimal import Decimal
from fractions import Fraction

YEAR_SQL = "SELECT (CAST((SUM(CASE WHEN YEAR(recorded_at)=2024 THEN amount ELSE 0 END)-SUM(CASE WHEN YEAR(recorded_at)=2023 THEN amount ELSE 0 END)) AS DOUBLE)/SUM(CASE WHEN YEAR(recorded_at)=2023 THEN amount ELSE 0 END))*100 AS growth FROM measurements WHERE YEAR(recorded_at) IN (2023,2024)"


def test_year_filters_and_conditional_sums_unchanged():
    p = m.plan_scaled_ratio(YEAR_SQL, "mysql")
    assert p
    a = sqlglot.parse_one(YEAR_SQL, read="mysql")
    b = sqlglot.parse_one(p.candidate_sql, read="mysql")
    b.set("expressions", a.expressions)
    assert a == b
    proof = sqlglot.parse_one(p.proof_sql, read="mysql")
    assert len(proof.expressions) == 2
    proof.set("expressions", a.expressions)
    assert proof == a


@pytest.mark.parametrize(
    "n,d", [(1, 3), (1, 7), (2, 3), (-1, 3), (1, -3), (7, 11), (23, 13)]
)
def test_proof_requires_strictly_closer_exact_rational(n, d):
    old = float(n) / float(d) * 100
    expected = float(Fraction(n * 100, d))
    proof = m.prove_scaled_ratio([[old]], [[Decimal(n), Decimal(d)]])
    if old == expected:
        assert proof is None
    else:
        assert (
            proof
            and proof["expected"] == expected
            and proof["nearest_binary64_verified"]
        )
        assert Fraction(proof["candidate_error"]) < Fraction(proof["original_error"])
        assert m.accepts_scaled_ratio(proof, [[expected]])
        assert not m.accepts_scaled_ratio(proof, [[old]])


@pytest.mark.parametrize(
    "operands",
    [
        [[1.0, 3]],
        [[1, 3.0]],
        [[True, 3]],
        [["1", 3]],
        [[Decimal("1.1"), 3]],
        [[1, 0]],
        [[1, None]],
        [[None, 3]],
        [[Decimal("NaN"), 3]],
        [[2**53 // 100 + 1, 3]],
        [[1, 2**53 + 1]],
        [],
        [[1, 3], [1, 3]],
        [[1, 3, 5]],
    ],
)
def test_inexact_unavailable_or_unsafe_range_operands_rejected(operands):
    assert m.prove_scaled_ratio([[100 / 3]], operands) is None


@pytest.mark.parametrize(
    "before",
    [
        [[None]],
        [[True]],
        [[float("inf")]],
        [[Decimal("33.3")]],
        [],
        [[33.3], [33.3]],
        [[33.3, 1]],
        [[34.0]],
    ],
)
def test_incomplete_or_inconsistent_original_rejected(before):
    assert m.prove_scaled_ratio(before, [[1, 3]]) is None


def test_already_nearest_and_zero_abstain():
    for n, d in [(1, 4), (0, 7), (1, 2), (2**53 // 100, 1)]:
        old = float(n) / float(d) * 100
        assert m.prove_scaled_ratio([[old]], [[n, d]]) is None


def test_result_must_equal_proved_nearest_float():
    proof = m.prove_scaled_ratio([[1 / 3 * 100]], [[1, 3]])
    assert proof
    for rows in [
        [],
        [[None]],
        [[Decimal(str(proof["expected"]))]],
        [[float("inf")]],
        [[proof["expected"], 0]],
        [[proof["expected"]], [proof["expected"]]],
        [[str(proof["expected"])]],
    ]:
        assert not m.accepts_scaled_ratio(proof, rows)


class Adapter:
    def __init__(self, value):
        self.value = value
        self.calls = []

    def generate_response(self, **kwargs):
        import json

        self.calls.append(kwargs)
        return {"response": json.dumps(self.value), "model": "fixture"}


@pytest.mark.parametrize(
    "decision",
    [
        {"output_kind": "other", "representation": "free", "source_excerpt": "growth"},
        {
            "output_kind": "scalar_numeric",
            "representation": "constrained",
            "source_excerpt": "growth",
        },
        {
            "output_kind": "scalar_numeric",
            "representation": "uncertain",
            "source_excerpt": "growth",
        },
        {
            "output_kind": "scalar_numeric",
            "representation": "free",
            "source_excerpt": "invented",
        },
        {},
    ],
)
def test_presentation_guard_rejects_nonfree_or_unbound_requests(decision):
    assert not m.route_scaled_ratio("growth", Adapter(decision))["activate"]


def test_router_has_no_sql_or_operand_values():
    import json

    a = Adapter(
        {
            "output_kind": "scalar_numeric",
            "representation": "free",
            "source_excerpt": "growth",
        }
    )
    assert m.route_scaled_ratio("growth", a)["activate"]
    assert set(json.loads(a.calls[0]["prompt"])) == {
        "effective_question",
        "trigger_catalog",
    }


import asyncio
from features.ask.service import AskService
from features.ask.engine.ask3.context import Ask3Context
from features.ask.engine.ask3.types import (
    ExecutionResult,
    SchemaInfo,
    TableInfo,
    ColumnInfo,
)
from features.ask.value_probe import create_scaled_ratio_probe_executor

SQL = "SELECT CAST(SUM(part) AS DOUBLE)/SUM(total)*100 AS metric FROM measurements"
QUESTION = "What is the growth rate?"
DECISION = {
    "output_kind": "scalar_numeric",
    "representation": "free",
    "source_excerpt": "growth rate",
}


LOADED_SCHEMA = SchemaInfo(
    "unrelated",
    "mysql",
    {
        "measurements": TableInfo(
            "measurements",
            {"part": ColumnInfo("part", "int"), "total": ColumnInfo("total", "int")},
        )
    },
)


def context():
    return Ask3Context(
        question=QUESTION,
        target="local",
        schema_info=LOADED_SCHEMA,
        db_type="mysql",
        target_config={"engine": "mysql"},
        sql=SQL,
        execution_result=ExecutionResult(
            rows=[[1 / 3 * 100]], columns=["metric"], row_count=1
        ),
        enforce_result_limit=False,
    )


@pytest.mark.parametrize(
    "candidate_rows,stats_truncated,candidate_truncated,status,calls_expected",
    [
        ([[100 / 3]], False, False, "normalized", 2),
        ([[1 / 3 * 100]], False, False, "reverted", 2),
        ([[100 / 3], [100 / 3]], False, False, "reverted", 2),
        ([[100 / 3]], True, False, "unchanged", 1),
        ([[100 / 3]], False, True, "reverted", 2),
    ],
)
def test_service_proof_candidate_and_restoration(
    candidate_rows, stats_truncated, candidate_truncated, status, calls_expected
):
    calls = []

    def execute(sql, config):
        calls.append(sql)
        if len(calls) == 1:
            return {
                "success": True,
                "rows": [[Decimal(1), Decimal(3)]],
                "columns": ["n", "d"],
                "truncated": stats_truncated,
            }
        return {
            "success": True,
            "rows": candidate_rows,
            "columns": ["metric"],
            "truncated": candidate_truncated,
        }

    c = asyncio.run(
        AskService(
            llm_manager=Adapter(DECISION), db_executor=execute
        )._apply_scaled_ratio(context())
    )
    assert (
        c.scaled_ratio["status"] == status
        and len(calls) == calls_expected
        and len(c.llm_calls) == 1
    )
    assert c.scaled_ratio_probe_diagnostics["calls"] == calls_expected
    if status == "normalized":
        assert c.sql != SQL and c.execution_result.rows == candidate_rows
    else:
        assert c.sql == SQL and c.execution_result.rows == [[1 / 3 * 100]]
    restored = Ask3Context.from_dict(c.to_dict())
    assert (
        restored.scaled_ratio == c.scaled_ratio
        and restored.scaled_ratio_probe_diagnostics == c.scaled_ratio_probe_diagnostics
    )


@pytest.mark.parametrize(
    "operands",
    [
        [[1.0, 3]],
        [[Decimal("1.1"), 3]],
        [[1, 0]],
        [[2**53, 3]],
        [[1, 4]],
        [],
        [[None, None]],
    ],
)
def test_bad_or_unnecessary_proof_never_runs_candidate(operands):
    calls = []

    def execute(sql, config):
        calls.append(sql)
        return {"success": True, "rows": operands, "columns": ["n", "d"]}

    c = asyncio.run(
        AskService(
            llm_manager=Adapter(DECISION), db_executor=execute
        )._apply_scaled_ratio(context())
    )
    assert len(calls) == 1 and c.sql == SQL and c.scaled_ratio["status"] == "unchanged"


def test_constrained_presentation_never_queries_database():
    def forbidden(*args):
        raise AssertionError("No database call expected")

    c = asyncio.run(
        AskService(
            llm_manager=Adapter(DECISION | {"representation": "constrained"}),
            db_executor=forbidden,
        )._apply_scaled_ratio(context())
    )
    assert (
        c.sql == SQL
        and c.scaled_ratio["reason"] == "numeric-presentation-constrained-or-uncertain"
    )


def test_preflight_rejection_restores_original(monkeypatch):
    async def reject(self, ctx, candidate):
        return False, ["fixture rejection"]

    monkeypatch.setattr(AskService, "_preflight_correction_candidate", reject)

    def forbidden(*args):
        raise AssertionError("No database call expected")

    c = asyncio.run(
        AskService(
            llm_manager=Adapter(DECISION), db_executor=forbidden
        )._apply_scaled_ratio(context())
    )
    assert c.sql == SQL and c.scaled_ratio["reason"] == "candidate-validation-failed"


@pytest.mark.parametrize("kind", ["context", "truncated", "error", "oversized"])
def test_incomplete_original_abstains_before_model(kind):
    c = context()
    if kind == "context":
        c.provided_context = "Custom definition"
    elif kind == "truncated":
        c.execution_result.truncated = True
    elif kind == "error":
        c.execution_result.error = "Failure"
    else:
        c.execution_result.rows = [[1.0], [1.0]]
    a = Adapter(DECISION)
    c = asyncio.run(AskService(llm_manager=a)._apply_scaled_ratio(c))
    assert not a.calls and c.sql == SQL


def test_two_call_readonly_budget_and_raw_truncation():
    c = context()
    calls = []

    def execute(*args):
        calls.append(args)
        return {"success": True, "rows": [[1]], "columns": ["n"], "truncated": True}

    bounded = create_scaled_ratio_probe_executor(c, execute)
    assert not bounded("SELECT 1", c.target_config)["success"]
    assert not bounded("DELETE FROM measurements", c.target_config)["success"]
    assert not bounded("SELECT 1", c.target_config)["success"]
    assert len(calls) == 1 and c.scaled_ratio_probe_diagnostics["blocked_calls"] == 1
