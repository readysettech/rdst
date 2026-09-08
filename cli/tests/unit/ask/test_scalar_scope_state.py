from types import SimpleNamespace as NS
from copy import deepcopy
import pytest
import sqlglot
from sqlglot import exp
from features.ask.state_lookup import plan_state_lookup

SQL = "SELECT COUNT(DISTINCT j.id) AS jobs FROM jobs j JOIN jobs k ON k.id=j.id WHERE j.region_id=(SELECT id FROM regions WHERE name='Harbor' AND year=2020) AND j.closed_at IS NOT NULL"


def schema():
    return NS(
        tables={
            "jobs": NS(
                columns={
                    n: NS(data_type=t)
                    for n, t in [
                        ("id", "int"),
                        ("region_id", "int"),
                        ("state_id", "int"),
                        ("closed_at", "datetime"),
                    ]
                },
                relationships=[
                    {
                        "target": "states",
                        "join": "jobs.state_id=states.id",
                        "type": "many_to_one",
                    },
                    {
                        "target": "regions",
                        "join": "jobs.region_id=regions.id",
                        "type": "many_to_one",
                    },
                ],
            ),
            "states": NS(
                columns={"id": NS(data_type="int"), "label": NS(data_type="text")},
                relationships=[],
            ),
            "regions": NS(
                columns={
                    "id": NS(data_type="int", is_primary_key=True),
                    "name": NS(data_type="text"),
                    "year": NS(data_type="int"),
                },
                relationships=[],
            ),
        }
    )


def test_scalar_lookup_and_all_other_row_clauses_preserved_exactly():
    p = plan_state_lookup(SQL, "mysql", schema())
    assert p
    original = sqlglot.parse_one(SQL, read="mysql")
    nested = next(original.find_all(exp.Subquery))
    for sql in [p.state_only_sql(7), p.candidate_sql(7), p.scope_proof_sql(7)]:
        tree = sqlglot.parse_one(sql, read="mysql")
        assert next(tree.find_all(exp.Subquery)) == nested
        assert tree.args["from_"] == original.args["from_"]
        assert tree.args["joins"] == original.args["joins"]
    candidate = sqlglot.parse_one(p.candidate_sql(7), read="mysql")
    assert candidate.expressions == original.expressions
    assert next(candidate.find_all(exp.Not)) == next(original.find_all(exp.Not))
    assert "`j`.`state_id` = 7" in candidate.sql(dialect="mysql")


@pytest.mark.parametrize(
    "replacement",
    [
        "SELECT id FROM regions WHERE name='Harbor' AND year=2020 LIMIT 1",
        "SELECT id FROM regions WHERE name='Harbor' ORDER BY id",
        "SELECT MAX(id) FROM regions WHERE name='Harbor'",
        "SELECT id+0 FROM regions WHERE name='Harbor'",
        "SELECT id AS selected FROM regions WHERE name='Harbor'",
        "SELECT DISTINCT id FROM regions WHERE name='Harbor'",
        "SELECT id FROM regions WHERE name='Harbor' OR year=2020",
        "SELECT id FROM regions WHERE year>2020",
        "SELECT id FROM regions WHERE name IS NOT NULL",
        "SELECT id FROM regions WHERE id=j.region_id",
        "SELECT id FROM regions WHERE name=missing_column",
        "SELECT id FROM regions WHERE RAND()>0.5",
        "SELECT id FROM regions WHERE name='Harbor' FOR UPDATE",
        "SELECT id FROM regions WHERE name='Harbor' GROUP BY id",
        "SELECT id FROM regions r JOIN extra e ON e.id=r.id WHERE r.name='Harbor'",
        "SELECT id FROM outside.regions WHERE name='Harbor'",
        "SELECT id FROM regions",
        "SELECT name FROM regions WHERE year=2020",
        "SELECT id FROM regions j WHERE name='Harbor'",
        "SELECT id FROM regions WHERE id=(SELECT id FROM other)",
    ],
)
def test_ambiguous_volatile_correlated_or_complex_scope_abstains(replacement):
    sql = SQL.replace(
        "SELECT id FROM regions WHERE name='Harbor' AND year=2020", replacement
    )
    assert plan_state_lookup(sql, "mysql", schema()) is None


@pytest.mark.parametrize(
    "sql",
    [
        SQL.replace("j.region_id=", "j.region_id<>"),
        SQL.replace("j.region_id=", "j.region_id IN "),
        SQL.replace(" AND j.closed_at", " OR j.closed_at"),
        SQL.replace(
            "COUNT(DISTINCT j.id)",
            "COUNT(DISTINCT j.id), (SELECT id FROM regions WHERE year=2020)",
        ),
    ],
)
def test_scope_must_be_outer_conjunctive_equality(sql):
    assert plan_state_lookup(sql, "mysql", schema()) is None


@pytest.mark.parametrize("changes", [{"data_type": "text"}])
def test_scalar_output_must_be_integer_relationship_key(changes):
    s = deepcopy(schema())
    for k, v in changes.items():
        setattr(s.tables["regions"].columns["id"], k, v)
    assert plan_state_lookup(SQL, "mysql", s) is None


def test_postgres_preserves_parser_compatibility_and_runtime_noop():
    assert sqlglot.parse_one(SQL, read="postgres")
    assert plan_state_lookup(SQL, "postgres", schema()) is None


def test_qualified_key_and_literal_on_left_supported_without_scope_edits():
    sql = SQL.replace(
        "SELECT id FROM regions WHERE name='Harbor' AND year=2020",
        "SELECT r.id FROM regions r WHERE 'Harbor'=r.name AND 2020=r.year",
    )
    p = plan_state_lookup(sql, "mysql", schema())
    assert p
    assert next(sqlglot.parse_one(sql, read="mysql").find_all(exp.Subquery)) == next(
        sqlglot.parse_one(p.candidate_sql(7), read="mysql").find_all(exp.Subquery)
    )


def test_named_key_uses_declared_relationship_not_id_name_heuristic():
    s = schema()
    s.tables["regions"].columns["id"].is_primary_key = False
    assert plan_state_lookup(SQL, "mysql", s)
    s.tables["jobs"].relationships = s.tables["jobs"].relationships[:1]
    assert plan_state_lookup(SQL, "mysql", s) is None


@pytest.mark.parametrize(
    "join,kind",
    [
        ("jobs.id=regions.id", "many_to_one"),
        ("jobs.region_id=regions.year", "many_to_one"),
        ("jobs.region_id=regions.id", "one_to_many"),
        ("jobs.region_id>regions.id", "many_to_one"),
    ],
)
def test_wrong_key_or_relationship_cardinality_abstains(join, kind):
    s = schema()
    s.tables["jobs"].relationships[-1].update(join=join, type=kind)
    assert plan_state_lookup(SQL, "mysql", s) is None


import asyncio
import json
from features.ask.service import AskService
from features.ask.engine.ask3.context import Ask3Context
from features.ask.engine.ask3.types import ExecutionResult


def context():
    return Ask3Context(
        question="Count completed jobs in the Harbor region for 2020.",
        target="local",
        db_type="mysql",
        target_config={"engine": "mysql"},
        schema_info=schema(),
        sql=SQL,
        enforce_result_limit=False,
        execution_result=ExecutionResult(rows=[[2]], columns=["jobs"], row_count=1),
    )


class Decision:
    def __init__(self):
        self.calls = []

    def generate_response(self, **kw):
        self.calls.append(kw)
        return {
            "response": json.dumps(
                {
                    "lookup_describes_requested_state": True,
                    "nonnull_value_requested": False,
                    "state_excerpt": "completed",
                    "state_is_name_or_title": False,
                }
            ),
            "model": "fixture",
            "tokens_used": 20,
        }


@pytest.mark.parametrize(
    "candidate,expected_status",
    [(1, "normalized"), (2, "reverted"), (None, "reverted")],
)
def test_scalar_scope_canonical_acceptance_and_restoration(candidate, expected_status):
    calls = []

    def db(sql, config):
        calls.append(sql)
        return {
            "success": True,
            "rows": [[7, "Completed", 1]]
            if len(calls) == 1
            else [[1, 1, 1]]
            if len(calls) == 2
            else [[candidate]],
            "columns": ["a", "b", "c"] if len(calls) < 3 else ["jobs"],
        }

    adapter = Decision()
    ctx = asyncio.run(
        AskService(llm_manager=adapter, db_executor=db)._apply_state_lookup(context())
    )
    assert len(adapter.calls) == 1 and len(calls) == 3 and len(ctx.llm_calls) == 1
    assert ctx.state_lookup["status"] == expected_status
    assert (ctx.sql != SQL) == (candidate == 1)
    assert ctx.execution_result.rows == ([[1]] if candidate == 1 else [[2]])
    assert ctx.state_lookup_probe_diagnostics["max_calls"] == 3
    original_scope = next(sqlglot.parse_one(SQL, read="mysql").find_all(exp.Subquery))
    for query in calls[1:]:
        assert (
            next(sqlglot.parse_one(query, read="mysql").find_all(exp.Subquery))
            == original_scope
        )


@pytest.mark.parametrize("truncated_call", [1, 2, 3])
def test_incomplete_lookup_proof_or_candidate_restores_original(truncated_call):
    calls = []

    def db(sql, config):
        calls.append(sql)
        return {
            "success": True,
            "rows": [[7, "Completed", 1]]
            if len(calls) == 1
            else [[1, 1, 1]]
            if len(calls) == 2
            else [[1]],
            "columns": ["a", "b", "c"] if len(calls) < 3 else ["jobs"],
            "truncated": len(calls) == truncated_call,
        }

    ctx = asyncio.run(
        AskService(llm_manager=Decision(), db_executor=db)._apply_state_lookup(
            context()
        )
    )
    assert len(calls) == truncated_call
    assert ctx.sql == SQL and ctx.execution_result.rows == [[2]]
    assert ctx.state_lookup["status"] != "normalized"


def test_unsuccessful_scalar_primary_never_routes_or_probes():
    ctx = context()
    ctx.execution_result = ExecutionResult(
        rows=[], columns=[], row_count=0, error="Subquery returns more than 1 row"
    )
    adapter = Decision()
    probes = []

    def db(*args):
        probes.append(args)
        raise AssertionError("No probe allowed")

    result = asyncio.run(
        AskService(llm_manager=adapter, db_executor=db)._apply_state_lookup(ctx)
    )
    assert not adapter.calls and not probes
    assert (
        result.sql == SQL
        and result.state_lookup["reason"] == "primary-result-unavailable"
    )
