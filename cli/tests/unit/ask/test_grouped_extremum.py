import asyncio
import json

import pytest
import sqlglot
from features.ask.grouped_extremum import (
    plan_grouped_extremum,
    safe_grouped_extremum_proof,
    route_grouped_extremum,
)
from features.ask.engine.ask3.context import Ask3Context
from features.ask.engine.ask3.types import (
    ExecutionResult,
    SchemaInfo,
    TableInfo,
    ColumnInfo,
)
from features.ask.service import AskService

BASE = "SELECT d.name FROM depots d JOIN crates c ON c.depot_id=d.id WHERE c.active=1 GROUP BY d.id,d.name ORDER BY COUNT(c.id) DESC LIMIT 1"


@pytest.mark.parametrize("dialect", ["mysql", "postgres"])
def test_only_grouped_ranking_changes(dialect):
    plan = plan_grouped_extremum(BASE, dialect)
    assert plan
    before = sqlglot.parse_one(BASE, read=dialect)
    after = sqlglot.parse_one(plan.candidate_sql, read=dialect)
    inner = after.args["from_"].this.this
    for key in ["from_", "joins", "where", "group"]:
        assert (
            before.args[key].sql(dialect=dialect, identify=True)
            == inner.args[key].sql(dialect=dialect, identify=True)
            if key != "joins"
            else [j.sql(dialect=dialect, identify=True) for j in before.args[key]]
            == [j.sql(dialect=dialect, identify=True) for j in inner.args[key]]
        )
    assert "DENSE_RANK" in plan.candidate_sql
    assert "LIMIT 101" in plan.proof_sql


@pytest.mark.parametrize(
    "sql",
    [
        BASE.replace("LIMIT 1", "LIMIT 2"),
        BASE + " OFFSET 1",
        BASE.replace("COUNT(c.id)", "RAND()"),
        BASE.replace("COUNT(c.id)", "COUNT(DISTINCT c.id)"),
        BASE.replace("d.name FROM", "d.name,d.id FROM"),
        BASE.replace("GROUP BY", "HAVING COUNT(c.id)>0 GROUP BY"),
        BASE + "; DELETE FROM depots",
        BASE.replace("GROUP BY d.id,d.name", "GROUP BY d.id"),
        BASE.replace("GROUP BY d.id,d.name", "GROUP BY d.id,d.name WITH ROLLUP"),
    ],
)
def test_unsupported_shape(sql):
    assert plan_grouped_extremum(sql, "mysql") is None


@pytest.mark.parametrize(
    "rows",
    [
        [["a", 2]],
        [["a", 2], ["b", 3]],
        [["a", None], ["b", None]],
        [["a", float("nan")], ["b", float("nan")]],
        [["a", True], ["b", True]],
        [["a", 2]] * 101,
    ],
)
def test_rejects_unproven_or_unbounded_ties(rows):
    assert not safe_grouped_extremum_proof(rows)


class Route:
    def generate_response(self, **kwargs):
        return {
            "response": json.dumps(
                {
                    "entity_only": True,
                    "explicit_result_bound": False,
                    "metric_and_direction_match": True,
                    "source_excerpt": "Which depot has most crates?",
                }
            ),
            "model": "scripted",
        }


def context():
    ctx = Ask3Context(
        question="Which depot has most crates?", target="warehouse", db_type="mysql"
    )
    ctx.sql = ctx.generated_sql = (
        "SELECT name FROM depots GROUP BY name ORDER BY SUM(crates) DESC LIMIT 1"
    )
    ctx.schema_info = SchemaInfo(
        target="warehouse",
        db_type="mysql",
        tables={
            "depots": TableInfo(
                name="depots",
                columns={
                    "name": ColumnInfo(name="name", data_type="varchar"),
                    "crates": ColumnInfo(name="crates", data_type="int"),
                },
            )
        },
    )
    ctx.target_config = {"engine": "mysql"}
    ctx.enforce_result_limit = False
    ctx.max_rows = 1000
    ctx.execution_result = ExecutionResult(
        columns=["name"], rows=[["alpha"]], row_count=1
    )
    return ctx


@pytest.mark.parametrize(
    "proof,candidate,status",
    [
        ([["alpha", 4], ["beta", 4]], [["alpha"], ["beta"]], "normalized"),
        ([["alpha", 4]], [["alpha"]], "unchanged"),
        ([["alpha", 4], ["beta", 4]], [["alpha"]], "reverted"),
        ([["alpha", 4], ["beta", 3]], [["alpha"], ["beta"]], "unchanged"),
        ([["gamma", 4], ["beta", 4]], [["gamma"], ["beta"]], "unchanged"),
        ([["alpha", 4], ["alpha", 4]], [["alpha"], ["alpha"]], "normalized"),
    ],
)
def test_exact_tie_acceptance_and_restore(proof, candidate, status):
    calls = []

    def execute(sql, config):
        calls.append(sql)
        return {
            "success": True,
            "rows": proof if len(calls) == 1 else candidate,
            "columns": ["name", "metric"] if len(calls) == 1 else ["name"],
        }

    ctx = context()
    original = ctx.sql
    service = AskService(llm_manager=Route(), db_executor=execute)
    ctx = asyncio.run(service._apply_grouped_extremum(ctx))
    assert ctx.grouped_extremum["status"] == status
    assert len(calls) <= 2
    assert ctx.grouped_extremum_probe_diagnostics["timeout_seconds"] == 4
    if status != "normalized":
        assert ctx.sql == original
    restored = Ask3Context.from_dict(ctx.to_dict())
    assert restored.grouped_extremum == ctx.grouped_extremum
    assert (
        restored.grouped_extremum_probe_diagnostics
        == ctx.grouped_extremum_probe_diagnostics
    )
    assert ctx.llm_calls[-1]["phase"] == "grouped_request_routing"


@pytest.mark.parametrize(
    "error,expected",
    [
        ("(1064, 'syntax error')", "normalized"),
        ("(1049, 'unknown database')", "unchanged"),
    ],
)
def test_only_syntax_failure_allows_proven_recovery(error, expected):
    ctx = context()
    ctx.execution_result = ExecutionResult(error=error)
    calls = []

    def execute(sql, config):
        calls.append(sql)
        return {
            "success": True,
            "rows": [["alpha", 4], ["beta", 4]]
            if len(calls) == 1
            else [["alpha"], ["beta"]],
            "columns": ["name", "metric"] if len(calls) == 1 else ["name"],
        }

    ctx = asyncio.run(
        AskService(llm_manager=Route(), db_executor=execute)._apply_grouped_extremum(
            ctx
        )
    )
    assert ctx.grouped_extremum["status"] == expected


@pytest.mark.parametrize(
    "change",
    [
        {"explicit_result_bound": True},
        {"entity_only": False},
        {"metric_and_direction_match": False},
        {"source_excerpt": "not in question"},
        {"entity_only": "true"},
    ],
)
def test_semantic_contract_is_fail_closed(change):
    class Changed(Route):
        def generate_response(self, **kwargs):
            raw = super().generate_response(**kwargs)
            v = json.loads(raw["response"])
            v.update(change)
            raw["response"] = json.dumps(v)
            return raw

    ctx = context()
    assert not route_grouped_extremum(ctx.question, ctx.sql, "mysql", Changed())[
        "activate"
    ]


@pytest.mark.parametrize(
    "mode", ["context", "conversation", "truncated", "timeout", "exception"]
)
def test_unavailable_inputs_and_probe_failure_preserve_primary(mode):
    ctx = context()
    original = ctx.sql
    calls = []
    if mode == "context":
        ctx.provided_context = "User supplies a definition"
    if mode == "conversation":
        ctx.conversation_context = "Earlier request"
    if mode == "truncated":
        ctx.execution_result.truncated = True

    def execute(sql, config):
        calls.append(sql)
        if mode == "exception":
            raise RuntimeError("unavailable")
        return {"success": False, "error": "timeout"}

    ctx = asyncio.run(
        AskService(llm_manager=Route(), db_executor=execute)._apply_grouped_extremum(
            ctx
        )
    )
    assert ctx.sql == original
    assert ctx.grouped_extremum["status"] != "normalized"
    if mode in {"context", "conversation", "truncated"}:
        assert not calls
