import asyncio
import json

import pytest

from features.ask import result_check
from features.ask.engine.ask3.context import Ask3Context
from features.ask.engine.ask3.types import (
    ColumnInfo,
    ExecutionResult,
    SchemaInfo,
    TableInfo,
)
from features.ask.service import AskService

SQL = "SELECT name FROM items WHERE kind = 'Tool'"
REPAIRED = "SELECT name FROM items WHERE kind = 'tool'"


class Model:
    def __init__(self, sql=REPAIRED):
        self.sql = sql
        self.calls = []

    def generate_response(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "response": json.dumps({"sql": self.sql, "explanation": "Fixed the literal."}),
            "tokens_used": 10,
            "model": "fixture",
        }


def context(result):
    return Ask3Context(
        question="Name the tools.",
        target="fixture",
        db_type="postgresql",
        target_config={"engine": "postgresql"},
        sql=SQL,
        generated_sql=SQL,
        schema_info=SchemaInfo(
            target="fixture",
            db_type="postgresql",
            tables={
                "items": TableInfo(
                    name="items",
                    columns={
                        n: ColumnInfo(name=n, data_type="text") for n in ("name", "kind")
                    },
                )
            },
        ),
        execution_result=result,
        enforce_result_limit=False,
    )


def run(ctx, model, execute):
    service = AskService(llm_manager=model, db_executor=execute)
    return asyncio.run(service._apply_result_check_repair(ctx))


@pytest.mark.parametrize(
    ("columns", "rows", "expected"),
    [
        (["name"], [], ["it returned no rows."]),
        (
            ["name", "score"],
            [["a", None], ["b", None]],
            ["column score is NULL in every returned row."],
        ),
        (
            ["name"],
            [["a"], ["a"], ["b"]],
            ["1 of 3 returned rows repeat another row exactly."],
        ),
        (["name", "score"], [{"name": "a", "score": 1}, {"name": "b", "score": None}], []),
    ],
)
def test_issues_name_each_sign_of_a_wrong_result(columns, rows, expected):
    assert result_check.issues(columns, rows) == expected


def test_a_repair_with_fewer_signs_replaces_the_answer():
    ctx = context(ExecutionResult(columns=["name"], rows=[]))
    model = Model()
    executed = []

    def execute(sql, config):
        executed.append(sql)
        return {"success": True, "rows": [["hammer"]], "columns": ["name"]}

    ctx = run(ctx, model, execute)

    assert ctx.result_check_repair["status"] == "normalized"
    assert ctx.result_check_repair["signs"] == ["it returned no rows."]
    assert ctx.sql == REPAIRED and executed == [REPAIRED]
    assert ctx.execution_result.rows == [["hammer"]]
    [call] = model.calls
    assert call["purpose"] == "sql_validation_repair"
    assert result_check.FEEDBACK_PREFIX + "it returned no rows." in call["prompt"]
    assert [entry["phase"] for entry in ctx.llm_calls] == ["result_check_repair"]
    saved = Ask3Context.from_dict(ctx.to_dict())
    assert saved.result_check_repair == ctx.result_check_repair


def test_a_repair_may_remove_repeated_rows():
    ctx = context(ExecutionResult(columns=["name"], rows=[["hammer"], ["hammer"], ["saw"]]))

    def execute(sql, config):
        return {"success": True, "rows": [["hammer"], ["saw"]], "columns": ["name"]}

    ctx = run(ctx, Model(), execute)

    assert ctx.result_check_repair["status"] == "normalized"
    assert ctx.execution_result.rows == [["hammer"], ["saw"]]


def test_the_same_sql_back_keeps_the_answer_without_running_it():
    result = ExecutionResult(columns=["name"], rows=[])
    ctx = context(result)

    def execute(sql, config):
        raise AssertionError(f"An unchanged query ran again: {sql}")

    ctx = run(ctx, Model(SQL), execute)

    assert ctx.result_check_repair["reason"] == "kept-original"
    assert ctx.sql == SQL and ctx.execution_result == result


def test_a_repair_with_as_many_signs_is_reverted():
    result = ExecutionResult(columns=["name"], rows=[])
    ctx = context(result)

    def execute(sql, config):
        return {"success": True, "rows": [], "columns": ["name"]}

    ctx = run(ctx, Model(), execute)

    assert ctx.result_check_repair["reason"] == "candidate-not-better"
    assert ctx.sql == SQL and ctx.execution_result == result


@pytest.mark.parametrize(
    ("before", "after", "reason"),
    [
        # Fewer signs, but the column the user asked for is gone.
        (
            ExecutionResult(columns=["name", "email"], rows=[["a", None], ["b", None]]),
            {"columns": ["name"], "rows": [["a"], ["b"]]},
            "candidate-changed-columns",
        ),
        # Two signs (all-NULL column, a repeated row) traded for one: no rows at all.
        (
            ExecutionResult(
                columns=["name", "email"], rows=[["a", None], ["a", None], ["b", None]]
            ),
            {"columns": ["name", "email"], "rows": []},
            "candidate-lost-rows",
        ),
        # Fewer repeats, but one of the two distinct answers is gone.
        (
            ExecutionResult(columns=["name"], rows=[["hammer"], ["hammer"], ["saw"]]),
            {"columns": ["name"], "rows": [["hammer"]]},
            "candidate-lost-rows",
        ),
        # The NULL column is filled, but saw is gone behind as many rows.
        (
            ExecutionResult(
                columns=["name", "kind"], rows=[["hammer", None], ["hammer", None], ["saw", None]]
            ),
            {"columns": ["name", "kind"], "rows": [["hammer", "tool"], ["hammer", "tool"]]},
            "candidate-lost-rows",
        ),
        # One all-NULL column filled in, another one emptied: no fewer signs.
        (
            ExecutionResult(
                columns=["name", "email", "phone"],
                rows=[["a", None, "1"], ["a", None, "1"], ["b", None, "2"]],
            ),
            {"columns": ["name", "email", "phone"], "rows": [["a", "x", None], ["b", "y", None]]},
            "candidate-not-better",
        ),
    ],
)
def test_a_repair_never_drops_columns_or_rows(before, after, reason):
    ctx = context(before)
    # Validating the candidate records its own resolution, which a revert must undo.
    ctx.resolved_column_validation_enabled = True

    def execute(sql, config):
        return {"success": True, **after}

    ctx = run(ctx, Model(), execute)

    assert ctx.result_check_repair["reason"] == reason
    assert ctx.sql == SQL and ctx.execution_result == before
    assert ctx.validation_resolution == {}


@pytest.mark.parametrize(
    "result",
    [
        None,
        ExecutionResult(columns=["name"], rows=[["hammer"], ["saw"]]),
        ExecutionResult(error="relation does not exist", error_kind="query_execution"),
    ],
)
def test_no_repair_without_a_sign(result):
    ctx = context(result)
    model = Model()

    def execute(sql, config):
        raise AssertionError("No repair runs")

    ctx = run(ctx, model, execute)

    assert ctx.result_check_repair["status"] == "unchanged"
    assert not model.calls and ctx.sql == SQL
