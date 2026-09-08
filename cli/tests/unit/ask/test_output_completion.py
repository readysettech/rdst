import json
import pytest
from dataclasses import replace
from features.ask.output_completion import (
    output_shape,
    plan_output_completion,
    accepts_output_completion,
    route_output_completion,
)
from features.ask.engine.ask3.types import SchemaInfo, TableInfo, ColumnInfo


def schema(tables):
    return SchemaInfo(
        "unrelated",
        "mysql",
        {
            t: TableInfo(t, {n: ColumnInfo(n, k) for n, k in cols.items()})
            for t, cols in tables.items()
        },
    )


ITEMS = schema(
    {
        "items": {
            "id": "int",
            "name": "text",
            "category": "text",
            "returned": "int",
            "sku": "text",
            "price": "decimal",
        }
    }
)
CONTACTS = schema(
    {
        "contacts": {
            "id": "int",
            "email": "text",
            "region": "text",
            "active": "int",
            "billing_address": "text",
            "shipping_address": "text",
            "first_name": "text",
            "last_name": "text",
            "display_name": "text",
        }
    }
)
CTE = "WITH busiest AS (SELECT category FROM items WHERE returned=1 GROUP BY category ORDER BY COUNT(*) DESC LIMIT 1) SELECT i.name FROM items i JOIN busiest b ON b.category=i.category WHERE i.returned=1"
SIMPLE = "SELECT c.email FROM contacts c WHERE c.active=1"


@pytest.mark.parametrize("dialect", ["mysql", "postgresql"])
@pytest.mark.parametrize(
    "sql,schema,col,position",
    [
        (CTE, ITEMS, "category", 0),
        (CTE, ITEMS, "category", 1),
        (SIMPLE, CONTACTS, "region", 1),
        (SIMPLE.replace("c.email", "c.email,c.region"), CONTACTS, "id", 1),
    ],
)
def test_only_adds_one_projection(dialect, sql, schema, col, position):
    import sqlglot
    from sqlglot import exp

    shape = output_shape(sql, dialect, schema)
    assert shape
    option = next(o["index"] for o in shape.options if o["column"] == col)
    plan = plan_output_completion(shape, option, position)
    assert plan
    before = sqlglot.parse_one(sql, read=shape.dialect)
    after = sqlglot.parse_one(plan.candidate_sql, read=shape.dialect)
    assert len(after.expressions) == len(before.expressions) + 1
    removed = after.expressions.pop(position)
    assert isinstance(removed, exp.Column) and removed.name == col
    assert after == before


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT DISTINCT i.name FROM items i",
        "SELECT i.name FROM items i LIMIT 1",
        "SELECT i.name FROM items i GROUP BY i.name",
        "SELECT i.name FROM items i ORDER BY 1",
        "SELECT i.* FROM items i",
        "SELECT * FROM items",
        "SELECT name FROM items",
        "SELECT i.name FROM missing i",
        "SELECT i.unknown FROM items i",
        "SELECT RAND() FROM items i",
        "SELECT i.name FROM items i WHERE RAND()>0.5",
        "SELECT i.name FROM items i; DELETE FROM items",
        "SELECT i.name FROM (SELECT name FROM items) i",
        "SELECT i.name FROM items i UNION SELECT i.name FROM items i",
        "SELECT i.name FROM items i WHERE i.id IN (SELECT id FROM items)",
        "WITH RECURSIVE q AS (SELECT name FROM items) SELECT i.name FROM items i",
        "SELECT i.name FROM items i JOIN items i ON i.id=i.id",
        "SELECT i.name INTO other FROM items i",
        "SELECT i.name FROM items i FOR UPDATE",
        "SELECT SUM(i.price) FROM items i",
    ],
)
def test_unsupported_sql_abstains(sql):
    assert output_shape(sql, "mysql", ITEMS) is None


@pytest.mark.parametrize(
    "before,after,ok",
    [
        ([("x",), ("x",), (None,)], [("A", "x"), ("B", "x"), (None, None)], True),
        ([("x",), ("x",)], [("A", "x")], False),
        ([("x",), ("x",)], [("A", "x"), ("A", "y")], False),
        ([("x",)], [("A", "x", "extra")], False),
        ([], [], False),
        ([("x",), ("y",)], [("A", "y"), ("B", "x")], True),
    ],
)
def test_complete_result_multisets(before, after, ok):
    shape = output_shape(CTE, "mysql", ITEMS)
    plan = plan_output_completion(shape, 0, 0)
    assert accepts_output_completion(plan, before, after) == ok
    if before == [("x",), ("y",)]:
        assert not accepts_output_completion(replace(plan, ordered=True), before, after)


@pytest.mark.parametrize(
    "option,position",
    [(-1, 0), (999, 0), (True, 0), (0, -1), (0, 999), (0, True), (0, "1")],
)
def test_invalid_indices(option, position):
    assert (
        plan_output_completion(output_shape(CTE, "mysql", ITEMS), option, position)
        is None
    )


class Adapter:
    def __init__(self, values):
        self.values = iter(values)
        self.calls = []

    def generate_response(self, **kwargs):
        self.calls.append(kwargs)
        return {"response": json.dumps(next(self.values))}


def test_independent_question_call_and_exact_binding():
    shape = output_shape(SIMPLE, "mysql", CONTACTS)
    index = next(o["index"] for o in shape.options if o["column"] == "region")
    a = Adapter(
        [
            {"status": "clear", "output_excerpts": ["email", "region"]},
            {
                "status": "complete_one",
                "projection_mapping": [0, -1],
                "option_index": index,
            },
        ]
    )
    r = route_output_completion("Give email and region.", shape, a)
    assert r["option_index"] == index
    payload = json.loads(a.calls[0]["prompt"])
    assert set(payload) == {"effective_question", "trigger_catalog"}
    assert r["insertion_index"] == 1


@pytest.mark.parametrize(
    "request_value",
    [
        {"status": "ambiguous", "output_excerpts": ["email", "region"]},
        {"status": "clear", "output_excerpts": ["email", "location"]},
        {"status": "clear", "output_excerpts": ["email", "email"]},
        {"status": "clear", "output_excerpts": ["email"]},
        {"status": "clear", "output_excerpts": ["email", "region", "id"]},
    ],
)
def test_request_rejection_does_not_call_binder(request_value):
    a = Adapter([request_value])
    r = route_output_completion(
        "Give email and region.", output_shape(SIMPLE, "mysql", CONTACTS), a
    )
    assert r["option_index"] is None and len(a.calls) == 1


@pytest.mark.parametrize(
    "mapping,option",
    [
        ([0, 0], 0),
        ([-1, -1], 0),
        ([1, -1], 0),
        ([0, -1], True),
        ([0, -1], 999),
        ([True, -1], 0),
        ([0, 1, -1], 0),
    ],
)
def test_invalid_binding_rejected(mapping, option):
    a = Adapter(
        [
            {"status": "clear", "output_excerpts": ["email", "region"]},
            {
                "status": "complete_one",
                "projection_mapping": mapping,
                "option_index": option,
            },
        ]
    )
    assert (
        route_output_completion(
            "Give email and region.", output_shape(SIMPLE, "mysql", CONTACTS), a
        )["option_index"]
        is None
    )


import asyncio
from features.ask.service import AskService
from features.ask.engine.ask3.context import Ask3Context
from features.ask.engine.ask3.types import ExecutionResult
from features.ask.value_probe import create_output_completion_probe_executor


def context():
    return Ask3Context(
        question="Give email and region.",
        target="local",
        db_type="mysql",
        target_config={"engine": "mysql"},
        schema_info=CONTACTS,
        sql=SIMPLE,
        execution_result=ExecutionResult(
            rows=[["a"], ["b"]], columns=["email"], row_count=2
        ),
        enforce_result_limit=False,
    )


def decision():
    shape = output_shape(SIMPLE, "mysql", CONTACTS)
    option = next(o["index"] for o in shape.options if o["column"] == "region")
    return Adapter(
        [
            {"status": "clear", "output_excerpts": ["email", "region"]},
            {
                "status": "complete_one",
                "projection_mapping": [0, -1],
                "option_index": option,
            },
        ]
    )


@pytest.mark.parametrize(
    "rows,truncated,status",
    [
        ([["a", "N"], ["b", None]], False, "normalized"),
        ([["a", "N"], ["x", "S"]], False, "reverted"),
        ([["a", "N"]], False, "reverted"),
        ([["a", "N"], ["b", "S"]], True, "reverted"),
    ],
)
def test_service_proof_restoration_and_context_roundtrip(rows, truncated, status):
    calls = []

    def execute(sql, config):
        calls.append(sql)
        return {
            "success": True,
            "rows": rows,
            "columns": ["email", "region"],
            "truncated": truncated,
        }

    c = asyncio.run(
        AskService(
            llm_manager=decision(), db_executor=execute
        )._apply_output_completion(context())
    )
    assert c.output_completion["status"] == status and len(calls) == 1
    assert len(c.llm_calls) == 2 and c.output_completion_probe_diagnostics["calls"] == 1
    assert c.sql == SIMPLE if status == "reverted" else c.sql != SIMPLE
    assert c.execution_result.rows == ([["a"], ["b"]] if status == "reverted" else rows)
    restored = Ask3Context.from_dict(c.to_dict())
    assert restored.output_completion == c.output_completion
    assert (
        restored.output_completion_probe_diagnostics
        == c.output_completion_probe_diagnostics
    )


@pytest.mark.parametrize(
    "kind", ["extra-context", "empty", "truncated", "no-schema", "bad-shape"]
)
def test_service_abstains_before_calls(kind):
    c = context()
    if kind == "extra-context":
        c.provided_context = "Custom convention"
    elif kind == "empty":
        c.execution_result.rows = []
    elif kind == "truncated":
        c.execution_result.truncated = True
    elif kind == "no-schema":
        c.schema_info = None
    else:
        c.sql = "SELECT DISTINCT c.email FROM contacts c"
    a = Adapter([])

    def forbidden(*args):
        raise AssertionError("No query expected")

    c = asyncio.run(
        AskService(llm_manager=a, db_executor=forbidden)._apply_output_completion(c)
    )
    assert c.sql == (
        "SELECT DISTINCT c.email FROM contacts c" if kind == "bad-shape" else SIMPLE
    )
    assert not a.calls and c.output_completion["status"] != "normalized"


def test_single_call_readonly_budget_and_raw_truncation():
    c = context()
    calls = []

    def execute(*args):
        calls.append(args)
        return {
            "success": True,
            "rows": [["a", "N"]],
            "columns": ["email", "region"],
            "truncated": True,
        }

    bounded = create_output_completion_probe_executor(c, execute)
    assert not bounded("SELECT c.email FROM contacts c", c.target_config)["success"]
    assert not bounded("SELECT c.email FROM contacts c", c.target_config)["success"]
    assert (
        len(calls) == 1 and c.output_completion_probe_diagnostics["blocked_calls"] == 1
    )
    bounded = create_output_completion_probe_executor(c, execute)
    assert not bounded("DELETE FROM contacts", c.target_config)["success"]
    assert len(calls) == 1


def test_invalid_candidate_does_not_execute(monkeypatch):
    a = decision()
    c = context()

    async def reject(self, ctx, candidate):
        return False, ["fixture rejection"]

    monkeypatch.setattr(AskService, "_preflight_correction_candidate", reject)

    def forbidden(*args):
        raise AssertionError("No query expected")

    c = asyncio.run(
        AskService(llm_manager=a, db_executor=forbidden)._apply_output_completion(c)
    )
    assert (
        c.sql == SIMPLE
        and c.output_completion["reason"] == "candidate-validation-failed"
    )


def test_wide_schema_limit_and_old_shape_compatibility():
    import copy
    from features.ask.engine.ask3.types import ColumnInfo

    wide = copy.deepcopy(ITEMS)
    for i in range(123):
        wide.tables["items"].columns[f"reading_{i}"] = ColumnInfo(
            f"reading_{i}", "decimal"
        )
    shape = output_shape(CTE, "mysql", wide)
    assert shape and len(shape.options) == 128
    wide.tables["items"].columns["overflow"] = ColumnInfo("overflow", "decimal")
    assert output_shape(CTE, "mysql", wide) is None
