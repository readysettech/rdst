from types import SimpleNamespace as NS
import asyncio
import json

import pytest
import sqlglot
from sqlglot import exp
from features.ask.state_lookup import (
    plan_state_lookup,
    proven_state_key,
    proven_state_count,
    accepts_state_count,
)
from features.ask.service import AskService
from features.ask.engine.ask3.context import Ask3Context
from features.ask.engine.ask3.types import ExecutionResult

SQL = "SELECT COUNT(DISTINCT j.id) AS jobs FROM jobs j WHERE j.region='Harbor' AND j.closed_at IS NOT NULL"


def schema():
    return NS(
        tables={
            "jobs": NS(
                columns={
                    n: NS(data_type=k)
                    for n, k in [
                        ("id", "int"),
                        ("state_id", "int"),
                        ("closed_at", "datetime"),
                        ("region", "text"),
                    ]
                },
                relationships=[
                    {
                        "target": "states",
                        "join": "jobs.state_id = states.id",
                        "type": "many_to_one",
                    }
                ],
            ),
            "states": NS(
                columns={"id": NS(data_type="int"), "label": NS(data_type="varchar")},
                relationships=[],
            ),
        }
    )


def plan(sql=SQL):
    return plan_state_lookup(sql, "mysql", schema())


def test_preserves_grain_alias_and_other_filters():
    p = plan()
    assert p
    candidate = sqlglot.parse_one(p.candidate_sql(7), read="mysql")
    original = sqlglot.parse_one(SQL, read="mysql")
    assert candidate.expressions == original.expressions
    assert candidate.args["from_"] == original.args["from_"]
    assert (
        candidate.args["where"].this.expression
        == original.args["where"].this.expression
    )
    assert candidate.args["where"].this.this.this == original.args["where"].this.this
    assert candidate.args["where"].this.this.expression.expression.this == "7"
    proof = sqlglot.parse_one(p.scope_proof_sql(7), read="mysql")
    assert proof.args["where"].this == candidate.args["where"].this.this
    assert proof.expressions[0] == original.expressions[0].this
    assert proof.expressions[-1].this.sql() == "j.closed_at"
    lookup = sqlglot.parse_one(p.lookup_sql("com'pleted"), read="mysql")
    assert lookup.args["limit"].expression.this == "2"
    assert any(l.this == "com'pleted" for l in lookup.find_all(exp.Literal))


@pytest.mark.parametrize(
    "sql",
    [
        SQL.replace("COUNT(DISTINCT j.id)", "SUM(j.id)"),
        SQL.replace("COUNT(DISTINCT j.id)", "COUNT(j.id + 1)"),
        SQL.replace("COUNT(DISTINCT j.id)", "COUNT(DISTINCT j.id, j.state_id)"),
        SQL.replace("COUNT(DISTINCT j.id)", "COUNT(*), j.id"),
        SQL.replace("j.closed_at", "j.region"),
        SQL.replace("j.closed_at IS NOT NULL", "j.closed_at IS NULL"),
        SQL.replace("j.closed_at IS NOT NULL", "j.closed_at IS NOT NULL OR j.id=1"),
        SQL.replace(
            "j.closed_at IS NOT NULL", "j.closed_at IS NOT NULL AND j.id IS NOT NULL"
        ),
        SQL.replace("j.closed_at IS NOT NULL", "other.j.closed_at IS NOT NULL"),
        SQL.replace("jobs j", "outside.jobs j"),
        SQL + " GROUP BY j.region",
        SQL + " LIMIT 1",
        SQL + " FOR UPDATE",
        SQL + "; DELETE FROM jobs",
    ],
)
def test_unsupported_queries(sql):
    assert plan(sql) is None


def test_postgres_parser_and_no_op():
    assert sqlglot.parse_one(SQL, read="postgres")
    assert plan_state_lookup(SQL, "postgres", schema()) is None


@pytest.mark.parametrize("condition", ["e.job_id=j.id", "e.job_id=j.id AND e.kind=2"])
def test_exists_scope_is_preserved_in_every_population_query(condition):
    sql = SQL + f" AND EXISTS (SELECT 1 FROM events e WHERE {condition})"
    p = plan(sql)
    assert p
    original = sqlglot.parse_one(sql, read="mysql")
    for candidate in (p.candidate_sql(7), p.scope_proof_sql(7), p.state_only_sql(7)):
        tree = sqlglot.parse_one(candidate, read="mysql")
        assert tree.find(exp.Exists) == original.find(exp.Exists)
        assert len(list(tree.find_all(exp.Select))) == 2
    assert "j.closed_at" in p.structural_facts()["non_null_predicate"]


@pytest.mark.parametrize(
    "suffix",
    [
        " AND EXISTS (SELECT 1 FROM events j WHERE j.job_id=j.id)",
        " AND EXISTS (SELECT 1 FROM events e WHERE e.job_id=j.id AND e.created_at IS NOT NULL)",
        " AND EXISTS (SELECT 1 FROM events e WHERE e.job_id=j.id OR e.kind=2)",
        " AND NOT EXISTS (SELECT 1 FROM events e WHERE e.job_id=j.id)",
        " AND EXISTS (SELECT 1 FROM events e WHERE e.job_id=j.id)=0",
        " OR EXISTS (SELECT 1 FROM events e WHERE e.job_id=j.id)",
        " AND EXISTS (SELECT COUNT(*) FROM events e WHERE e.job_id=j.id)",
        " AND EXISTS (SELECT 1 FROM events e WHERE e.job_id=j.id LIMIT 1)",
        " AND EXISTS (SELECT 1 FROM events e WHERE e.job_id=j.id AND RAND()>0.5)",
        " AND EXISTS (SELECT 1 FROM events e WHERE e.job_id=(SELECT id FROM jobs LIMIT 1))",
    ],
)
def test_ambiguous_or_unsupported_exists_abstains(suffix):
    assert plan(SQL + suffix) is None


def test_ambiguous_or_nonstructural_relationships_abstain():
    s = schema()
    s.tables["jobs"].relationships *= 2
    assert plan_state_lookup(SQL, "mysql", s) is None
    s = schema()
    s.tables["jobs"].relationships[0]["type"] = "many_to_many"
    assert plan_state_lookup(SQL, "mysql", s) is None
    s = schema()
    s.tables["states"].columns["notes"] = NS(data_type="text")
    assert plan_state_lookup(SQL, "mysql", s) is None
    s = schema()
    s.tables["jobs"].relationships = [NS()]
    assert plan_state_lookup(SQL, "mysql", s) is None


@pytest.mark.parametrize(
    "rows,state,expected",
    [
        ([[7, "Completed", 1]], "completed", 7),
        ([[0, "Completed", 1]], "completed", 0),
        ([[7, "Completed", 2]], "completed", None),
        ([[7, "Completed", 1], [8, "completed", 1]], "completed", None),
        ([[7, "Completed ", 1]], "completed", None),
        ([[7, "Compléted", 1]], "completed", None),
        ([[7, "Completed", 1]], "complete", None),
        ([[True, "Completed", 1]], "completed", None),
        ([[7, "Completed", True]], "completed", None),
        ([[None, "Completed", 1]], "completed", None),
        ([], "completed", None),
    ],
)
def test_unique_exact_label_and_key(rows, state, expected):
    assert proven_state_key(rows, state) == expected


@pytest.mark.parametrize(
    "rows,old,expected",
    [
        ([[2, 3, 3]], [[4]], 2),
        ([[0, 0, 0]], [[4]], 0),
        ([[2, 3, 2]], [[4]], None),
        ([[5, 5, 5]], [[4]], None),
        ([[4, 3, 3]], [[4]], None),
        ([[True, 3, 3]], [[4]], None),
        ([[2, 3, 3]], [[True]], None),
        ([[2, 3, 3], [2, 3, 3]], [[4]], None),
    ],
)
def test_scoped_subset_preserves_count_grain(rows, old, expected):
    assert proven_state_count(rows, old) == expected


class Decision:
    def __init__(self, **overrides):
        self.overrides = overrides

    def generate_response(self, **kwargs):
        return {
            "response": json.dumps(
                {
                    "lookup_describes_requested_state": True,
                    "nonnull_value_requested": False,
                    "state_is_name_or_title": False,
                    "state_excerpt": "completed",
                    **self.overrides,
                }
            ),
            "model": "scripted",
            "tokens_used": 12,
        }


def context():
    return Ask3Context(
        question="How many completed jobs are in Harbor?",
        target="local",
        db_type="mysql",
        target_config={"engine": "mysql"},
        schema_info=schema(),
        sql=SQL,
        execution_result=ExecutionResult(rows=[[4]], columns=["jobs"], row_count=1),
        enforce_result_limit=False,
    )


@pytest.mark.parametrize(
    "candidate,status", [(2, "normalized"), (3, "reverted"), (None, "reverted")]
)
def test_service_three_proofs_and_restoration(candidate, status):
    calls = []

    def execute(sql, config):
        calls.append(sql)
        rows = (
            [[7, "Completed", 1]]
            if len(calls) == 1
            else [[2, 3, 3]]
            if len(calls) == 2
            else [[candidate]]
        )
        return {"success": True, "rows": rows, "columns": ["jobs"]}

    ctx = asyncio.run(
        AskService(llm_manager=Decision(), db_executor=execute)._apply_state_lookup(
            context()
        )
    )
    assert len(calls) == 3 and ctx.state_lookup_probe_diagnostics["calls"] == 3
    assert ctx.state_lookup["status"] == status
    assert ctx.execution_result.rows == [[candidate if status == "normalized" else 4]]
    assert (ctx.sql != SQL) == (status == "normalized")
    assert len(ctx.llm_calls) == 1
    restored = Ask3Context.from_dict(ctx.to_dict())
    assert restored.state_lookup == ctx.state_lookup
    assert restored.state_lookup_probe_diagnostics == ctx.state_lookup_probe_diagnostics


@pytest.mark.parametrize(
    "decision",
    [
        {"lookup_describes_requested_state": False},
        {"state_is_name_or_title": True},
        {"state_is_name_or_title": None},
        {"state_excerpt": "invented"},
    ],
)
def test_semantic_abstention_has_no_queries(decision):
    def forbidden(*args):
        raise AssertionError("No query allowed")

    ctx = asyncio.run(
        AskService(
            llm_manager=Decision(**decision), db_executor=forbidden
        )._apply_state_lookup(context())
    )
    assert ctx.sql == SQL and ctx.state_lookup["reason"] == "model-abstained"


def test_incomplete_subset_proof_preserves_primary():
    calls = []

    def execute(*args):
        calls.append(args)
        return {
            "success": True,
            "rows": [[7, "Completed", 1]] if len(calls) == 1 else [[2, 3, 2]],
            "columns": [],
        }

    ctx = asyncio.run(
        AskService(llm_manager=Decision(), db_executor=execute)._apply_state_lookup(
            context()
        )
    )
    assert len(calls) == 2 and ctx.sql == SQL
    assert ctx.state_lookup["reason"] == "state-subset-not-proven"


def test_explicit_presence_can_coexist_with_an_added_state_filter():
    calls = []

    def execute(*args):
        calls.append(args)
        return {
            "success": True,
            "rows": [[7, "Completed", 1]]
            if len(calls) == 1
            else [[2, 3, 3]]
            if len(calls) == 2
            else [[2]],
            "columns": ["jobs"],
        }

    ctx = asyncio.run(
        AskService(
            llm_manager=Decision(nonnull_value_requested=True), db_executor=execute
        )._apply_state_lookup(context())
    )
    assert ctx.state_lookup["status"] == "normalized"
    original = sqlglot.parse_one(SQL, read="mysql")
    after = sqlglot.parse_one(ctx.sql, read="mysql")
    assert original.find(exp.Not) == after.find(exp.Not)
    assert len(calls) == 3
