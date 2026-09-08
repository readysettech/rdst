import asyncio
import json
from datetime import date
from types import SimpleNamespace as NS

import pytest
import sqlglot
from sqlglot import exp
from features.ask.null_extremum import (
    plan_null_extremum,
    original_missing_proved,
    candidate_measured_projection,
)
from features.ask.service import AskService
from features.ask.engine.ask3.context import Ask3Context
from features.ask.engine.ask3.types import ExecutionResult
from features.ask.value_probe import create_null_extremum_probe_executor


def schema(kind="double"):
    return NS(
        tables={
            "readings": NS(
                columns={
                    "id": NS(data_type="int"),
                    "score": NS(data_type=kind),
                    "label": NS(data_type="text"),
                }
            )
        }
    )


@pytest.mark.parametrize(
    "dialect,direction",
    [("mysql", "ASC"), ("postgres", "DESC"), ("postgres", "ASC NULLS FIRST")],
)
def test_plan_preserves_population_ranking_and_bound(dialect, direction):
    sql = f"SELECT r.id FROM readings r WHERE r.id>0 ORDER BY r.score {direction},r.id LIMIT 1"
    plan = plan_null_extremum(sql, dialect, schema())
    assert plan
    old = sqlglot.parse_one(sql, read=dialect)
    new = sqlglot.parse_one(plan.candidate_sql, read=dialect)
    assert (
        old.args["order"] == new.args["order"]
        and old.args["limit"] == new.args["limit"]
        and old.expressions == new.expressions
    )
    new.set("where", old.args["where"].copy())
    assert new == old
    for sql in (plan.original_proof_sql, plan.candidate_proof_sql):
        assert len(sqlglot.parse_one(sql, read=dialect).expressions) == 2


@pytest.mark.parametrize(
    "sql,dialect",
    [
        ("SELECT id FROM readings ORDER BY score DESC LIMIT 1", "mysql"),
        ("SELECT id FROM readings ORDER BY score ASC LIMIT 1", "postgres"),
        ("SELECT id FROM readings ORDER BY score DESC NULLS LAST LIMIT 1", "postgres"),
        ("SELECT id FROM readings ORDER BY score ASC LIMIT 2", "mysql"),
        ("SELECT id FROM readings ORDER BY score ASC LIMIT 1 OFFSET 1", "mysql"),
        ("SELECT DISTINCT id FROM readings ORDER BY score ASC LIMIT 1", "mysql"),
        ("SELECT id FROM readings ORDER BY RAND() LIMIT 1", "mysql"),
        ("SELECT id FROM readings ORDER BY label ASC LIMIT 1", "mysql"),
        ("SELECT id AS score FROM readings ORDER BY score ASC LIMIT 1", "mysql"),
        (
            "SELECT id,MAX(score) FROM readings GROUP BY id ORDER BY score ASC LIMIT 1",
            "mysql",
        ),
        ("SELECT * FROM readings ORDER BY score ASC LIMIT 1", "mysql"),
        (
            "SELECT id FROM readings WHERE id=(SELECT id FROM readings LIMIT 1) ORDER BY score ASC LIMIT 1",
            "mysql",
        ),
        (
            "SELECT id FROM readings ORDER BY score ASC LIMIT 1;DELETE FROM readings",
            "mysql",
        ),
    ],
)
def test_unsupported_or_unneeded_plan_abstains(sql, dialect):
    assert plan_null_extremum(sql, dialect, schema()) is None


def test_exact_missing_and_measured_result_proof():
    p = plan_null_extremum(
        "SELECT id FROM readings ORDER BY score ASC LIMIT 1", "mysql", schema()
    )
    assert original_missing_proved(p, [[1]], [[1, None]])
    for rows in ([[2, None]], [[1, 0]], [], [[1, None], [2, None]]):
        assert not original_missing_proved(p, [[1]], rows)
    assert candidate_measured_projection(p, [[2, 0]]) == [(2,)]
    assert candidate_measured_projection(p, [[2, date(2001, 1, 1)]]) == [(2,)]
    for rows in (
        [[2, None]],
        [[2, float("nan")]],
        [[2, float("inf")]],
        [[2, True]],
        [[2, "unknown"]],
        [],
    ):
        assert candidate_measured_projection(p, rows) is None


class Decision:
    def __init__(self, active=True):
        self.active = active

    def generate_response(self, **kwargs):
        return {
            "response": json.dumps(
                {
                    "measured_extremum_requested": self.active,
                    "metric_and_direction_match": True,
                    "missing_values_requested": False,
                    "source_excerpt": "lowest score",
                }
            ),
            "model": "scripted",
            "tokens_used": 5,
        }


def context():
    return Ask3Context(
        question="Which reading has the lowest score?",
        target="fixture",
        db_type="mysql",
        target_config={"engine": "mysql"},
        schema_info=schema(),
        sql="SELECT id FROM readings ORDER BY score ASC LIMIT 1",
        execution_result=ExecutionResult(rows=[[1]], columns=["id"], row_count=1),
        enforce_result_limit=False,
    )


@pytest.mark.parametrize(
    "proofs,status,calls",
    [
        ([[[1, None]], [[2, 7]], [[2]]], "normalized", 3),
        ([[[1, 3]]], "unchanged", 1),
        ([[[5, None]]], "unchanged", 1),
        ([[[1, None]], [[2, None]]], "unchanged", 2),
        ([[[1, None]], []], "unchanged", 2),
        ([[[1, None]], [[2, 7]], [[3]]], "reverted", 3),
    ],
)
def test_service_proof_acceptance_and_restoration(proofs, status, calls):
    seen = []

    def execute(sql, config):
        rows = proofs[len(seen)]
        seen.append(sql)
        return {
            "success": True,
            "rows": rows,
            "columns": ["id", "rank"] if rows and len(rows[0]) == 2 else ["id"],
        }

    ctx = context()
    original = ctx.sql
    ctx = asyncio.run(
        AskService(llm_manager=Decision(), db_executor=execute)._apply_null_extremum(
            ctx
        )
    )
    assert ctx.null_extremum["status"] == status and len(seen) == calls
    assert len(ctx.llm_calls) == 1
    if status == "normalized":
        assert ctx.execution_result.rows == [[2]] and ctx.sql != original
    else:
        assert ctx.execution_result.rows == [[1]] and ctx.sql == original
    restored = Ask3Context.from_dict(ctx.to_dict())
    assert (
        restored.null_extremum == ctx.null_extremum
        and restored.null_extremum_probe_diagnostics
        == ctx.null_extremum_probe_diagnostics
    )


@pytest.mark.parametrize(
    "mode", ["model-abstain", "provided-context", "truncated", "empty", "error"]
)
def test_no_database_calls_when_preconditions_fail(mode):
    def forbidden(*a):
        raise AssertionError("No probe expected")

    ctx = context()
    if mode == "provided-context":
        ctx.provided_context = "Keep missing measurements."
    if mode == "truncated":
        ctx.execution_result.truncated = True
    if mode == "empty":
        ctx.execution_result.rows = []
    if mode == "error":
        ctx.execution_result.error = "No result"
    result = asyncio.run(
        AskService(
            llm_manager=Decision(False), db_executor=forbidden
        )._apply_null_extremum(ctx)
    )
    assert result.null_extremum["status"] == "unchanged" and result.sql == ctx.sql


def test_three_query_budget_and_read_only():
    seen = []

    def execute(sql, config):
        seen.append(sql)
        return {"success": True, "rows": [[1]], "columns": ["id"]}

    ctx = context()
    probe = create_null_extremum_probe_executor(ctx, execute)
    for _ in range(3):
        assert probe("SELECT id FROM readings LIMIT 1", ctx.target_config)["success"]
    assert (
        not probe("SELECT id FROM readings LIMIT 1", ctx.target_config)["success"]
        and len(seen) == 3
    )
    probe = create_null_extremum_probe_executor(ctx, execute)
    assert (
        not probe("DELETE FROM readings", ctx.target_config)["success"]
        and len(seen) == 3
    )
