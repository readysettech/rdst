from types import SimpleNamespace as NS
from decimal import Decimal

import pytest
import sqlglot
from sqlglot import exp

from features.ask.fraction_precision import (
    plan_fraction_precision,
    fraction_candidate,
    accepts_fraction_precision,
)


def schema():
    return NS(
        tables={
            "batches": NS(
                columns={
                    name: NS(data_type=kind)
                    for name, kind in (
                        ("completion_fraction", "double"),
                        ("completed", "int"),
                        ("item_count", "int"),
                        ("region", "text"),
                    )
                }
            )
        }
    )


PROOF = [[20, 8, 0.0, 0.8, 0, 1, 0, 1, 0, 0, 5, 0]]


def plan(sql="SELECT completion_fraction FROM batches"):
    return plan_fraction_precision(sql, "mysql", schema())


def test_projection_only_with_full_population_proof():
    sql = "SELECT b.completion_fraction AS rate FROM batches b WHERE region='West' ORDER BY item_count DESC LIMIT 2 OFFSET 3"
    p = plan(sql)
    assert p
    assert "West" not in p.proof_sql
    new = sqlglot.parse_one(fraction_candidate(p, PROOF), read="mysql")
    old = sqlglot.parse_one(sql, read="mysql")
    assert isinstance(new.expressions[0].this, exp.Case)
    assert new.expressions[0].alias == "rate"
    new.set("expressions", [old.expressions[0].copy()])
    assert new == old
    assert "THEN NULL" in fraction_candidate(p, PROOF)
    assert "CAST(`b`.`completed` AS DOUBLE)" in fraction_candidate(p, PROOF)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT completion_fraction, region FROM batches",
        "SELECT AVG(completion_fraction) FROM batches",
        "SELECT DISTINCT completion_fraction FROM batches",
        "SELECT completion_fraction FROM batches GROUP BY completion_fraction",
        "SELECT completion_fraction FROM batches ORDER BY RAND()",
        "SELECT completion_fraction FROM batches FOR UPDATE",
        "SELECT completion_fraction FROM external.batches",
        "SELECT other.completion_fraction FROM batches",
        "SELECT completion_fraction FROM batches b JOIN batches c ON b.region=c.region",
        "SELECT completion_fraction FROM batches WHERE completed>(SELECT AVG(completed) FROM batches)",
        "SELECT completion_fraction FROM batches; DELETE FROM batches",
        "SELECT item_count FROM batches",
        "SELECT region FROM batches",
        "SELECT * FROM batches",
    ],
)
def test_unsupported_shapes(sql):
    assert plan(sql) is None


def test_postgres_no_op():
    sql = (
        'SELECT "completion_fraction" FROM "batches" ORDER BY "item_count" DESC LIMIT 2'
    )
    assert sqlglot.parse_one(sql, read="postgres")
    assert plan_fraction_precision(sql, "postgres", schema()) is None


@pytest.mark.parametrize(
    "bad",
    [
        [20, 8, 0, 0.8, 0, 1, 0, 1, 0, 0, 0, 0],
        [20, 8, 0, 80, 0, 1, 0, 1, 0, 0, 4, 0],
        [20, 8, 0, 0.8, 0, 1, 0, 1, 0.01, 0, 5, 0],
        [20, 8, 0, 0.8, 0, 1, 0, 0, 0, 0, 5, 0],
    ],
)
def test_unproven_witness_abstains(bad):
    assert fraction_candidate(plan(), [bad]) is None


def test_unordered_precision_preserves_nulls_and_duplicates():
    p = plan()
    old = [[None], [Decimal("0.33333333333333")], [0.5], [0.5]]
    new = [[0.5], [1 / 3], [None], [0.5]]
    assert accepts_fraction_precision(p, old, new)
    assert not accepts_fraction_precision(p, old, new[:-1])
    assert not accepts_fraction_precision(p, old, [[0.5], [1 / 3], [0], [0.5]])


@pytest.mark.parametrize(
    "new",
    [
        [],
        [[0.34]],
        [[float("nan")]],
        [[float("inf")]],
        [[True]],
        [["0.33333333333333"]],
        [[33.3333]],
        [[-0.1]],
        [[None]],
        [[1 / 3, 1]],
    ],
)
def test_invalid_result_restoration_contract(new):
    assert not accepts_fraction_precision(plan(), [[0.33333333333333]], new)


def test_ordered_sequence_preserved():
    p = plan("SELECT completion_fraction FROM batches ORDER BY item_count DESC")
    assert accepts_fraction_precision(p, [[0.33333333333333], [0.5]], [[1 / 3], [0.5]])
    assert not accepts_fraction_precision(
        p, [[0.33333333333333], [0.5]], [[0.5], [1 / 3]]
    )


import asyncio
import json
from features.ask.service import AskService
from features.ask.engine.ask3.context import Ask3Context
from features.ask.engine.ask3.types import ExecutionResult
from features.ask.value_probe import create_fraction_precision_probe_executor


class Decision:
    def __init__(self, **overrides):
        self.overrides = overrides

    def generate_response(self, **kwargs):
        return {
            "response": json.dumps(
                {
                    "requested_rate": True,
                    "stored_values_requested": False,
                    "rounding_or_rendering_requested": False,
                    "source_excerpt": "completion rate",
                    **self.overrides,
                }
            ),
            "model": "scripted",
            "tokens_used": 12,
        }


def context():
    return Ask3Context(
        question="Show the completion rate of each batch",
        target="local",
        db_type="mysql",
        target_config={"engine": "mysql"},
        schema_info=schema(),
        sql="SELECT completion_fraction FROM batches",
        execution_result=ExecutionResult(
            rows=[[0.33333333333333]], columns=["completion_fraction"], row_count=1
        ),
        enforce_result_limit=False,
    )


@pytest.mark.parametrize(
    "candidate,expected",
    [(1 / 3, "normalized"), (0.34, "reverted"), (None, "reverted")],
)
def test_service_proof_acceptance_restoration_and_serialization(candidate, expected):
    calls = []

    def execute(sql, config):
        calls.append(sql)
        return {
            "success": True,
            "rows": PROOF if len(calls) == 1 else [[candidate]],
            "columns": ["rate"],
        }

    ctx = context()
    original = ctx.sql
    ctx = asyncio.run(
        AskService(
            llm_manager=Decision(), db_executor=execute
        )._apply_fraction_precision(ctx)
    )
    assert ctx.fraction_precision["status"] == expected and len(calls) == 2
    assert ctx.fraction_precision_probe_diagnostics["calls"] == 2
    assert len(ctx.llm_calls) == 1
    assert (ctx.sql != original) == (expected == "normalized")
    assert ctx.execution_result.rows == [
        [candidate if expected == "normalized" else 0.33333333333333]
    ]
    restored = Ask3Context.from_dict(ctx.to_dict())
    assert restored.fraction_precision == ctx.fraction_precision
    assert (
        restored.fraction_precision_probe_diagnostics
        == ctx.fraction_precision_probe_diagnostics
    )


@pytest.mark.parametrize(
    "decision",
    [
        {"requested_rate": False},
        {"stored_values_requested": True},
        {"rounding_or_rendering_requested": True},
        {"source_excerpt": "invented"},
    ],
)
def test_semantic_abstention_has_no_queries(decision):
    def forbidden(*args):
        raise AssertionError("No query is allowed")

    ctx = context()
    old = ctx.sql
    ctx = asyncio.run(
        AskService(
            llm_manager=Decision(**decision), db_executor=forbidden
        )._apply_fraction_precision(ctx)
    )
    assert ctx.sql == old and ctx.fraction_precision["reason"] == "model-abstained"


def test_unproven_relation_keeps_original():
    ctx = context()
    old = ctx.sql
    ctx = asyncio.run(
        AskService(
            llm_manager=Decision(), db_executor=lambda *a: {"success": True, "rows": []}
        )._apply_fraction_precision(ctx)
    )
    assert ctx.sql == old
    assert ctx.fraction_precision["reason"] == "fractional-storage-not-uniquely-proven"


def test_strict_read_only_and_two_query_budget():
    calls = []
    bounded = create_fraction_precision_probe_executor(
        context(), lambda *a: calls.append(a) or {"success": True, "rows": [[1]]}
    )
    assert not bounded("DELETE FROM batches", {})["success"]
    assert calls == []
    # A rejected request consumes the bounded attempt; subsequent calls cannot
    # make the total exceed two.
    bounded("SELECT 1", {})
    bounded("SELECT 1", {})
    bounded("SELECT 1", {})
    assert len(calls) <= 2


def joined_schema():
    s = schema()
    s.tables["regions"] = NS(
        columns={"label": NS(data_type="text"), "tier": NS(data_type="text")}
    )
    return s


@pytest.mark.parametrize("reverse", [False, True])
def test_joined_fraction_changes_only_projection_and_proves_whole_source(reverse):
    source = "regions r JOIN batches b" if reverse else "batches b JOIN regions r"
    sql = f"SELECT b.completion_fraction AS rate FROM {source} ON b.region=r.label WHERE r.tier='priority' ORDER BY b.item_count DESC LIMIT 3"
    p = plan_fraction_precision(sql, "mysql", joined_schema())
    assert p and "regions" not in p.proof_sql and "priority" not in p.proof_sql
    new = sqlglot.parse_one(fraction_candidate(p, PROOF), read="mysql")
    old = sqlglot.parse_one(sql, read="mysql")
    assert "CAST(`b`.`completed` AS DOUBLE)" in new.sql(dialect="mysql")
    new.set("expressions", old.expressions)
    assert new == old


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT completion_fraction FROM batches b JOIN regions r ON b.region=r.label",
        "SELECT b.completion_fraction FROM batches b LEFT JOIN regions r ON b.region=r.label",
        "SELECT b.completion_fraction FROM batches b CROSS JOIN regions r",
        "SELECT b.completion_fraction FROM batches b NATURAL JOIN regions r",
        "SELECT b.completion_fraction FROM batches b JOIN regions r USING(region)",
        "SELECT b.completion_fraction FROM batches b JOIN regions r ON b.region<>r.label",
        "SELECT b.completion_fraction FROM batches b JOIN regions r ON b.region=b.region",
        "SELECT b.completion_fraction FROM batches b JOIN regions r ON b.region=r.missing",
        "SELECT b.completion_fraction FROM batches b JOIN regions b ON b.region=b.label",
        "SELECT b.completion_fraction FROM batches b JOIN regions r ON b.region=r.label ORDER BY 1",
        "SELECT b.completion_fraction AS rate FROM batches b JOIN regions r ON b.region=r.label ORDER BY rate",
        "SELECT b.completion_fraction FROM batches b JOIN regions r ON b.region=r.label WHERE tier='priority'",
        "SELECT b.completion_fraction FROM batches b JOIN regions r ON b.region=r.label JOIN regions s ON b.region=s.label",
        "SELECT b.completion_fraction FROM batches b JOIN (SELECT * FROM regions) r ON b.region=r.label",
        "SELECT b.completion_fraction FROM batches b JOIN elsewhere.regions r ON b.region=r.label",
        "SELECT b.completion_fraction FROM batches b JOIN missing r ON b.region=r.label",
    ],
)
def test_joined_fraction_rejects_ambiguous_or_unsupported_reference(sql):
    assert plan_fraction_precision(sql, "mysql", joined_schema()) is None


def test_joined_postgres_is_unchanged():
    sql = (
        "SELECT b.completion_fraction FROM batches b JOIN regions r ON b.region=r.label"
    )
    assert sqlglot.parse_one(sql, read="postgres")
    assert plan_fraction_precision(sql, "postgres", joined_schema()) is None


def test_joined_service_preserves_duplicate_rows_and_restores_failed_candidate():
    ctx = context()
    ctx.schema_info = joined_schema()
    ctx.sql = (
        "SELECT b.completion_fraction FROM batches b JOIN regions r ON b.region=r.label"
    )
    ctx.execution_result.rows = [[0.33333333333333], [0.33333333333333]]
    ctx.execution_result.row_count = 2
    original = ctx.sql
    calls = []

    def execute(sql, config):
        calls.append(sql)
        return {
            "success": True,
            "rows": PROOF if len(calls) == 1 else [[1 / 3]],
            "columns": ["rate"],
        }

    ctx = asyncio.run(
        AskService(
            llm_manager=Decision(), db_executor=execute
        )._apply_fraction_precision(ctx)
    )
    assert ctx.sql == original and ctx.fraction_precision["status"] == "reverted"
    assert len(ctx.execution_result.rows) == 2 and len(calls) == 2
