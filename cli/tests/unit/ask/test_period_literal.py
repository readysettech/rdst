from types import SimpleNamespace
import pytest
import sqlglot
from features.ask.period_literal import (
    plan_period_literal,
    prove_period_storage,
    accepts_period_result,
)

SQL = "SELECT CAST(100.0*SUM(CASE WHEN units>45 THEN 1 ELSE 0 END) AS DOUBLE)/COUNT(*) AS pct FROM dispatches WHERE period_key='2024-07' AND active=1"


def schema(kind="varchar(8)"):
    return SimpleNamespace(
        tables={
            "dispatches": SimpleNamespace(
                columns={
                    n: SimpleNamespace(data_type=t)
                    for n, t in [
                        ("period_key", kind),
                        ("units", "int"),
                        ("active", "int"),
                        ("other", "varchar(8)"),
                    ]
                }
            )
        }
    )


def test_preserves_entire_aggregate_source_and_population():
    p = plan_period_literal(SQL, "mysql", schema())
    assert p
    a, b = [sqlglot.parse_one(s, read="mysql") for s in [SQL, p.candidate_sql]]
    assert a.expressions == b.expressions and a.args["from_"] == b.args["from_"]
    assert b.args["where"].this.expression == a.args["where"].this.expression
    assert "period_key = '202407'" in p.candidate_sql
    assert (
        "CONCAT(SUBSTRING(period_key, 1, 4), '-', SUBSTRING(period_key, 5, 2)) = '2024-07'"
        in p.witness_sql
    )
    assert (
        "WHERE active = 1 GROUP BY CAST(period_key AS BINARY) LIMIT 1201" in p.proof_sql
    )
    assert prove_period_storage(p, [[b"202407", 2], [b"202406", 3], [None, 1]])
    assert accepts_period_result([[None]], [[50.0]], [[50.0]])


@pytest.mark.parametrize(
    "projection",
    [
        "COUNT(*)",
        "SUM(units)",
        "AVG(units)",
        "MIN(units)",
        "MAX(units)",
        "COUNT(DISTINCT units)",
        "ROUND(AVG(units),2)",
    ],
)
def test_scalar_aggregate_family(projection):
    assert plan_period_literal(
        "SELECT " + projection + " FROM dispatches WHERE period_key='2024-07'",
        "mysql",
        schema(),
    )


@pytest.mark.parametrize(
    "sql",
    [
        SQL + " GROUP BY units",
        SQL + " ORDER BY 1",
        SQL + " LIMIT 1",
        SQL + " FOR UPDATE",
        SQL + ";SELECT 1",
        SQL.replace("dispatches WHERE", "dispatches JOIN peers USING(active) WHERE"),
        SQL.replace("COUNT(*)", "COUNT(*)+RAND()"),
        SQL.replace("COUNT(*)", "COUNT(*)+SLEEP(1)"),
        SQL.replace("'2024-07'", "'2024'"),
        SQL.replace("'2024-07'", "'2024-13'"),
        SQL.replace("'2024-07'", "'0000-07'"),
        SQL.replace("'2024-07'", "'２０２４-０７'"),
        SQL.replace("period_key=", "other="),
        SQL.replace("AND active=1", "AND other='2024-06'"),
        SQL.replace("AND active=1", "OR active=1"),
        SQL.replace("AND active=1", "AND period_key <> '202301'"),
        SQL.replace("AND active=1", "AND NOT(active=0)"),
        SQL.replace("units>45", "period_key>45"),
        SQL.replace("units>45", "missing>45"),
        SQL.replace("COUNT(*)", "COUNT(*)+units"),
        SQL.replace("COUNT(*)", "COUNT(*) OVER()"),
        "SELECT units FROM dispatches WHERE period_key='2024-07'",
    ],
)
def test_unsupported_or_ambiguous_shapes(sql):
    # A second text field can be a valid period column; do not infer roles in host.
    if "WHERE other=" in sql:
        assert plan_period_literal(sql, "mysql", schema())
    else:
        assert plan_period_literal(sql, "mysql", schema()) is None


@pytest.mark.parametrize("kind", ["int", "date", "datetime", "double"])
def test_only_text_storage(kind):
    assert plan_period_literal(SQL, "mysql", schema(kind)) is None


@pytest.mark.parametrize("dialect", ["postgres", "postgresql"])
def test_postgresql_is_unchanged(dialect):
    assert sqlglot.parse_one(SQL, read="postgres")
    assert plan_period_literal(SQL, dialect, schema()) is None


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [[b"202407", 0]],
        [[b"202407", True]],
        [[b"202407", "2"]],
        [[b"202407", 1], [b"202407", 2]],
        [[b"202408", 3]],
        [[b"202407", 1], [b"202413", 2]],
        [[b"202407", 1], [b"2024-08", 2]],
        [[b"202407", 1], [b"000007", 2]],
        [[b"202407", 1], [b"20240 ", 2]],
        [[b"202407", 1], [b"\xff", 2]],
        [["202407", 1]],
        [[202407, 1]],
        [[None, 3]],
        [[b"202407", 1]] * 1201,
    ],
)
def test_storage_rejects_missing_target_mixed_or_unbounded_values(rows):
    assert not prove_period_storage(plan_period_literal(SQL, "mysql", schema()), rows)


@pytest.mark.parametrize(
    "old,witness,new",
    [
        ([[1]], [[1]], [[1]]),
        ([[None]], [[None]], [[None]]),
        ([[None]], [[1]], [[2]]),
        ([[None]], [[1]], [[1], [1]]),
        ([[None]], [[1, 2]], [[1, 2]]),
        ([[None]], [], []),
    ],
)
def test_independent_result_required(old, witness, new):
    assert not accepts_period_result(old, witness, new)


import asyncio
import json
from features.ask.service import AskService
from features.ask.engine.ask3.context import Ask3Context
from features.ask.engine.ask3.types import ExecutionResult


class Decision:
    def __init__(self, **changes):
        self.changes = changes

    def generate_response(self, **kwargs):
        v = {
            "requested_month": "2024-07",
            "period_kind": "calendar_month",
            "column_role_matches": "yes",
            "requires_literal_spelling": False,
            "question_quote": "July 2024",
        }
        v.update(self.changes)
        return {"response": json.dumps(v), "model": "fixture", "tokens_used": 20}


def context():
    return Ask3Context(
        question="What percentage of active dispatches had over45units in July 2024?",
        target="local",
        db_type="mysql",
        target_config={"engine": "mysql"},
        schema_info=schema(),
        sql=SQL,
        execution_result=ExecutionResult(rows=[[None]], columns=["pct"], row_count=1),
        enforce_result_limit=False,
    )


@pytest.mark.parametrize(
    "answer,status", [(50.0, "normalized"), (0.0, "reverted"), (None, "reverted")]
)
def test_service_independent_result_acceptance_and_restore(answer, status):
    calls = []

    def db(sql, config):
        calls.append(sql)
        return {
            "success": True,
            "rows": [[b"202407", 2], [b"202406", 3]]
            if len(calls) == 1
            else [[50.0 if len(calls) == 2 else answer]],
            "columns": ["value", "n"] if len(calls) == 1 else ["pct"],
        }

    ctx = asyncio.run(
        AskService(llm_manager=Decision(), db_executor=db)._apply_period_literal(
            context()
        )
    )
    assert ctx.period_literal["status"] == status
    assert len(calls) == 3 and ctx.period_literal_probe_diagnostics["max_calls"] == 3
    assert (ctx.sql != SQL) == (status == "normalized")
    assert ctx.execution_result.rows == (
        [[50.0]] if status == "normalized" else [[None]]
    )
    assert len(ctx.llm_calls) == 1
    restored = Ask3Context.from_dict(ctx.to_dict())
    assert (
        restored.period_literal == ctx.period_literal
        and restored.period_literal_probe_diagnostics
        == ctx.period_literal_probe_diagnostics
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"requested_month": "2024-08"},
        {"period_kind": "opaque_code"},
        {"period_kind": "fiscal_period"},
        {"column_role_matches": "no"},
        {"column_role_matches": "unknown"},
        {"requires_literal_spelling": True},
        {"question_quote": "invented"},
    ],
)
def test_semantic_rejection_never_queries(changes):
    calls = []
    ctx = asyncio.run(
        AskService(
            llm_manager=Decision(**changes), db_executor=lambda *a: calls.append(a)
        )._apply_period_literal(context())
    )
    assert (
        not calls
        and ctx.sql == SQL
        and ctx.period_literal["reason"] == "model-abstained"
    )


@pytest.mark.parametrize("stage", [1, 2, 3])
def test_query_failure_restores_original(stage):
    calls = []

    def db(sql, config):
        calls.append(sql)
        if len(calls) == stage:
            return {"success": False, "error": "timeout"}
        return {
            "success": True,
            "rows": [[b"202407", 2]] if len(calls) == 1 else [[50.0]],
            "columns": ["value", "n"] if len(calls) == 1 else ["pct"],
        }

    ctx = asyncio.run(
        AskService(llm_manager=Decision(), db_executor=db)._apply_period_literal(
            context()
        )
    )
    assert (
        ctx.sql == SQL and ctx.execution_result.rows == [[None]] and len(calls) == stage
    )


def test_malformed_model_response_is_recorded():
    class Bad:
        def generate_response(self, **kwargs):
            return {"response": "{broken", "model": "fixture", "tokens_used": 2}

    ctx = asyncio.run(AskService(llm_manager=Bad())._apply_period_literal(context()))
    assert (
        ctx.sql == SQL
        and ctx.period_literal["status"] == "reverted"
        and len(ctx.llm_calls) == 1
    )


def test_missing_schema_and_extra_context_stop_before_model():
    class Forbidden:
        def generate_response(self, **kwargs):
            raise AssertionError("No model call expected")

    for field, value in [
        ("schema_info", None),
        ("provided_context", "Custom fiscal definitions."),
    ]:
        ctx = context()
        setattr(ctx, field, value)
        ctx = asyncio.run(
            AskService(llm_manager=Forbidden())._apply_period_literal(ctx)
        )
        assert ctx.sql == SQL and not ctx.llm_calls


def test_readonly_and_shared_query_budget():
    from features.ask.value_probe import create_period_literal_probe_executor

    calls = []

    def db(sql, config):
        calls.append(sql)
        return {"success": True, "rows": [[1]], "columns": ["n"]}

    ctx = context()
    bounded = create_period_literal_probe_executor(ctx, db)
    assert not bounded("DELETE FROM dispatches", ctx.target_config)["success"]
    assert bounded("SELECT 1", ctx.target_config)["success"]
    assert bounded("SELECT 2", ctx.target_config)["success"]
    assert not bounded("SELECT 3", ctx.target_config)["success"]
    assert len(calls) == 2 and ctx.period_literal_probe_diagnostics["calls"] == 3
