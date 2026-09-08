import asyncio
import copy
from unittest.mock import Mock

import pytest
import sqlglot

from features.ask.engine.ask3.context import Ask3Context
from features.ask.engine.ask3.types import (
    ColumnInfo,
    ExecutionResult,
    SchemaInfo,
    TableInfo,
)
from features.ask.service import AskService, _snapshot_correction_state
from features.ask.stable_first import plan_stable_first, proven_stable_first_rows
from features.ask.value_probe import create_stable_first_probe_executor

SQL = "SELECT i.label FROM items i ORDER BY i.rating DESC LIMIT 1"


def schema():
    return SchemaInfo(
        target="local",
        db_type="mysql",
        tables={
            "items": TableInfo(
                name="items",
                columns={
                    n: ColumnInfo(name=n, data_type=t, is_primary_key=n == "item_key")
                    for n, t in [
                        ("item_key", "int"),
                        ("label", "varchar"),
                        ("rating", "int"),
                    ]
                },
            )
        },
    )


def context():
    return Ask3Context(
        question="Choose a highest-rated item",
        target="local",
        db_type="mysql",
        schema_info=schema(),
        target_config={"engine": "mysql"},
        sql=SQL,
        execution_result=ExecutionResult(
            rows=[["old"]], columns=["label"], row_count=1
        ),
        enforce_result_limit=False,
    )


@pytest.mark.parametrize("dialect", ["mysql", "postgres"])
def test_only_missing_key_order_is_added(dialect):
    p = plan_stable_first(SQL, dialect, schema())
    assert p
    before = sqlglot.parse_one(SQL, read=dialect)
    after = sqlglot.parse_one(p.candidate_sql, read=dialect)
    added = after.args["order"].expressions.pop()
    assert added.this.name == "item_key" and added.args["desc"] is False
    assert before == after
    proof = sqlglot.parse_one(p.candidate_rank_sql, read=dialect)
    assert len(list(proof.find_all(sqlglot.exp.Select))) == 2
    assert "COUNT(*)" in proof.sql()


@pytest.mark.parametrize(
    "sql",
    [
        SQL.replace("LIMIT 1", "LIMIT 2"),
        SQL + " OFFSET 1",
        SQL.replace("i.label", "DISTINCT i.label"),
        SQL.replace("i.label", "MAX(i.rating)"),
        SQL.replace("i.label", "*"),
        SQL.replace("i.label", "label"),
        SQL.replace("i.rating DESC", "RAND()"),
        SQL.replace("i.rating DESC", "1"),
        SQL.replace("i.rating DESC", "i.item_key DESC"),
        SQL.replace("items i", "unknown i"),
        SQL.replace("items i", "items i LEFT JOIN items j ON i.item_key=j.item_key"),
        SQL + "; DELETE FROM items",
        SQL.replace("ORDER BY", "WHERE SLEEP(1)=0 ORDER BY"),
        SQL.replace("items i", "items i USE INDEX (arbitrary)"),
        SQL.replace("i.label", "i.label, i.missing"),
    ],
)
def test_unsupported_shapes(sql):
    assert plan_stable_first(sql, "mysql", schema()) is None


def test_declared_keys_are_required_and_preserve_composite_prefix_direction():
    s = schema()
    s.tables["items"].columns["item_key"].is_primary_key = False
    assert plan_stable_first(SQL, "mysql", s) is None
    s = schema()
    s.tables["items"].columns["rating"].is_primary_key = True
    p = plan_stable_first(SQL, "mysql", s)
    assert p
    t = sqlglot.parse_one(p.candidate_sql, read="mysql")
    assert [e.this.name for e in t.args["order"].expressions] == ["rating", "item_key"]
    assert t.args["order"].expressions[0].args["desc"] is True


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [["new", 9, 1, 1], ["extra", 9, 2, 1]],
        [["new", 8, 1, 1]],
        [["new", 9, 1, 2]],
        [["new", 9, None, 1]],
        [["new", 9, True, 1]],
        [["new", 9, 1, True]],
        [["new", 9, 1, 1, 0]],
        [["new", 9, 1]],
    ],
)
def test_incomplete_or_inconsistent_native_identity_is_rejected(rows):
    p = plan_stable_first(SQL, "mysql", schema())
    assert proven_stable_first_rows(p, [["old", 9, 2]], rows) is None


@pytest.mark.parametrize(
    "failure",
    [
        None,
        "baseline_error",
        "baseline_truncated",
        "proof_error",
        "proof_truncated",
        "rank",
        "duplicate_key",
        "null_key",
        "candidate_error",
        "candidate_truncated",
        "candidate_disagreement",
    ],
)
def test_service_accepts_only_complete_proofs_and_restores(failure):
    calls = []
    responses = [
        {
            "success": True,
            "rows": [["old", 9, 2]],
            "columns": ["label", "rating", "key"],
        },
        {
            "success": True,
            "rows": [["new", 9, 1, 1]],
            "columns": ["label", "rating", "key", "unique"],
        },
        {"success": True, "rows": [["new"]], "columns": ["label"]},
    ]
    if failure:
        if failure.startswith("baseline_"):
            responses[0].update(
                {"success": False, "error": "unavailable"}
                if failure.endswith("error")
                else {"truncated": True}
            )
        elif failure.startswith("proof_"):
            responses[1].update(
                {"success": False, "error": "unavailable"}
                if failure.endswith("error")
                else {"truncated": True}
            )
        elif failure == "rank":
            responses[1]["rows"][0][1] = 8
        elif failure == "duplicate_key":
            responses[1]["rows"][0][-1] = 2
        elif failure == "null_key":
            responses[1]["rows"][0][-2] = None
        elif failure == "candidate_error":
            responses[2].update(success=False, error="failed")
        elif failure == "candidate_truncated":
            responses[2]["truncated"] = True
        else:
            responses[2]["rows"] = [["different"]]

    def execute(sql, config):
        calls.append(sql)
        return responses[len(calls) - 1]

    ctx = context()
    before = copy.deepcopy(_snapshot_correction_state(ctx))
    model = Mock()
    ctx = asyncio.run(
        AskService(llm_manager=model, db_executor=execute)._apply_stable_first(ctx)
    )
    assert len(calls) <= 3 and ctx.stable_first_probe_diagnostics["calls"] == len(calls)
    if failure:
        assert _snapshot_correction_state(ctx) == before
        assert ctx.stable_first["status"] in {"unchanged", "reverted"}
    else:
        assert ctx.stable_first[
            "status"
        ] == "normalized" and ctx.execution_result.rows == [["new"]]
        assert ctx.sql != SQL and len(calls) == 3
    assert not model.mock_calls
    restored = Ask3Context.from_dict(ctx.to_dict())
    assert restored.stable_first == ctx.stable_first
    assert restored.stable_first_probe_diagnostics == ctx.stable_first_probe_diagnostics


def test_probe_count_deadline_and_read_only(monkeypatch):
    now = [0.0]
    monkeypatch.setattr("features.ask.value_probe.time.monotonic", lambda: now[0])
    calls = []

    def execute(*args):
        calls.append(args)
        return {"success": True, "rows": [[1]], "columns": ["v"]}

    ctx = context()
    probe = create_stable_first_probe_executor(ctx, execute)
    for _ in range(3):
        assert probe("SELECT 1", {})["success"]
    assert not probe("SELECT 1", {})["success"] and len(calls) == 3
    probe = create_stable_first_probe_executor(ctx, execute)
    now[0] = 4.01
    assert not probe("SELECT 1", {})["success"] and len(calls) == 3
    probe = create_stable_first_probe_executor(ctx, execute)
    assert not probe("DELETE FROM items", {})["success"] and len(calls) == 3
