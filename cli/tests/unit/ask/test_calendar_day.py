import asyncio, json
from types import SimpleNamespace as NS
import pytest, sqlglot
from sqlglot import exp
from features.ask.calendar_day import (
    plan_calendar_day,
    proves_iso_boundary,
    accepts_calendar_result,
)
from features.ask.service import AskService
from features.ask.engine.ask3.context import Ask3Context
from features.ask.engine.ask3.types import ExecutionResult
from features.ask.value_probe import create_calendar_day_probe_executor


def schema(kind="text"):
    return NS(
        tables={
            "readings": NS(
                columns={
                    "id": NS(data_type="int"),
                    "recorded_at": NS(data_type=kind),
                    "score": NS(data_type="int"),
                }
            )
        }
    )


SQL = "SELECT AVG(score) FROM readings WHERE id>1 AND recorded_at<='2022-07-06'"


@pytest.mark.parametrize("dialect", ["mysql", "postgres"])
@pytest.mark.parametrize("op", ["=", "<="])
def test_plan_changes_only_date_comparison(dialect, op):
    sql = SQL.replace("<=", op)
    p = plan_calendar_day(sql, dialect, schema())
    assert p
    t = sqlglot.parse_one(p.candidate_sql, read=dialect)
    old = sqlglot.parse_one(sql, read=dialect)
    t.set("where", old.args["where"])
    assert t == old
    assert (
        "2022-07-07" in p.candidate_sql
        and "CAST(recorded_at AS DATE)" in p.date_result_sql
    )
    storage = sqlglot.parse_one(p.storage_sql, read=dialect)
    assert storage.args["distinct"] and storage.args[
        "limit"
    ].expression == exp.Literal.number(1001)


@pytest.mark.parametrize(
    "sql",
    [
        SQL.replace("AND", "OR"),
        SQL.replace("recorded_at<=", "NOT recorded_at<="),
        SQL.replace("2022-07-06", "2022-02-30"),
        SQL.replace("2022-07-06", "2022-07-06T00:00:00"),
        SQL.replace("recorded_at", "DATE(recorded_at)"),
        SQL + ";DELETE FROM readings",
        SQL.replace("AVG(score)", "RAND()"),
        SQL.replace("readings", "unknown"),
        SQL.replace("2022-07-06", "9999-12-31"),
    ],
)
def test_unsupported_shape(sql):
    assert plan_calendar_day(sql, "mysql", schema()) is None


def test_native_date_is_unchanged():
    assert plan_calendar_day(SQL, "mysql", schema("date")) is None


@pytest.mark.parametrize(
    "rows",
    [[["2022-07-06T12:00:00"], [None]], [["2022-07-06 00:00:00.125"], ["2021-01-01"]]],
)
def test_proves_complete_valid_boundary(rows):
    assert proves_iso_boundary(plan_calendar_day(SQL, "mysql", schema()), rows)


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [["2022-07-06"]],
        [["2022-07-07T00:00:00"]],
        [["2022-07-06T12:00:00Z"]],
        [["2022-07-06T25:00:00"]],
        [["2022-07-06T00:00:00"], ["unknown"]],
        [["2022-07-06T00:00:00"]] * 1001,
    ],
)
def test_incomplete_invalid_or_unneeded_storage_abstains(rows):
    assert not proves_iso_boundary(plan_calendar_day(SQL, "mysql", schema()), rows)


class Decision:
    def __init__(self, g="inclusive_end_day", matches=True):
        self.g, self.matches = g, matches

    def generate_response(self, **kw):
        return {
            "response": json.dumps(
                {
                    "comparison_granularity": self.g,
                    "column_matches_question": self.matches,
                    "source_excerpt": "through July 6, 2022",
                }
            ),
            "model": "scripted",
            "tokens_used": 12,
        }


def ctx():
    return Ask3Context(
        question="Give average score through July 6, 2022",
        target="local",
        db_type="mysql",
        target_config={"engine": "mysql"},
        schema_info=schema(),
        sql=SQL,
        execution_result=ExecutionResult(
            rows=[[3.0]], columns=["average"], row_count=1
        ),
        enforce_result_limit=False,
    )


@pytest.mark.parametrize(
    "last,status", [([[4.0]], "normalized"), ([[5.0]], "reverted"), (None, "reverted")]
)
def test_service_native_result_proof_and_restore(last, status):
    calls = []

    def execute(sql, config):
        calls.append(sql)
        rows = [[["2022-07-06T12:00:00"]], [[4.0]], last][len(calls) - 1]
        return {
            "success": rows is not None,
            "rows": rows or [],
            "columns": ["average"],
            "error": "Unavailable" if rows is None else None,
        }

    c = asyncio.run(
        AskService(llm_manager=Decision(), db_executor=execute)._apply_calendar_day(
            ctx()
        )
    )
    assert c.calendar_day["status"] == status and len(calls) == 3
    if status == "reverted":
        assert c.sql == SQL and c.execution_result.rows == [[3.0]]
    else:
        assert c.execution_result.rows == last
    restored = Ask3Context.from_dict(c.to_dict())
    assert (
        restored.calendar_day == c.calendar_day
        and restored.calendar_day_probe_diagnostics == c.calendar_day_probe_diagnostics
    )
    assert len(c.llm_calls) == 1


def test_abstention_and_shared_read_only_budget():
    calls = []

    def execute(*a):
        calls.append(a)
        return {"success": True, "rows": []}

    c = asyncio.run(
        AskService(
            llm_manager=Decision("exact_or_ambiguous"), db_executor=execute
        )._apply_calendar_day(ctx())
    )
    assert not calls and c.sql == SQL
    b = create_calendar_day_probe_executor(c, execute)
    for _ in range(4):
        b("SELECT id FROM readings", c.target_config)
    b("DELETE FROM readings", c.target_config)
    assert len(calls) == 3


def test_row_witness_preserves_order_and_duplicates():
    p = plan_calendar_day(
        "SELECT id FROM readings WHERE recorded_at='2022-07-06' ORDER BY id",
        "mysql",
        schema(),
    )
    assert p
    assert accepts_calendar_result(p, [[1], [1], [2]], [[1], [1], [2]])
    assert not accepts_calendar_result(p, [[1], [1], [2]], [[2], [1], [1]])
    assert not accepts_calendar_result(p, [[1], [1]], [[1]])


@pytest.mark.parametrize("dialect", ["mysql", "postgres"])
@pytest.mark.parametrize("start", ["2022-07-01", "2022-07-06"])
def test_between_preserves_lower_endpoint_and_native_date_witness(dialect, start):
    sql = (
        "SELECT AVG(score) FROM readings WHERE id>1 AND "
        f"recorded_at BETWEEN '{start}' AND '2022-07-06'"
    )
    plan = plan_calendar_day(sql, dialect, schema())
    assert plan is not None and plan.operator == "between"
    candidate = sqlglot.parse_one(plan.candidate_sql, read=dialect)
    assert candidate.find(exp.GTE).expression.this == start
    assert candidate.find(exp.LT).expression.this == "2022-07-07"
    witness = sqlglot.parse_one(plan.date_result_sql, read=dialect)
    interval = witness.find(exp.Between)
    assert isinstance(interval.this, exp.Cast)
    assert interval.args["low"].this == start
    assert interval.args["high"].this == "2022-07-06"
    assert proves_iso_boundary(plan, [["2022-07-06T23:59:59.999999"]])
    assert not proves_iso_boundary(plan, [["2022-07-07T00:00:00"]])


@pytest.mark.parametrize(
    "predicate",
    [
        "recorded_at BETWEEN '2022-07-07' AND '2022-07-06'",
        "recorded_at BETWEEN '2022-02-30' AND '2022-07-06'",
        "recorded_at BETWEEN '2022-07-01T12:00:00' AND '2022-07-06'",
        "recorded_at BETWEEN '2022-07-01' AND '2022-07-06T12:00:00'",
        "recorded_at BETWEEN '2022-07-01' AND '9999-12-31'",
        "recorded_at BETWEEN recorded_at AND '2022-07-06'",
        "recorded_at NOT BETWEEN '2022-07-01' AND '2022-07-06'",
        "recorded_at BETWEEN '2022-07-01' AND '2022-07-06' OR id=1",
        "recorded_at BETWEEN '2022-07-01' AND '2022-07-06' "
        "AND recorded_at<='2022-07-08'",
    ],
)
@pytest.mark.parametrize("dialect", ["mysql", "postgres"])
def test_between_abstains_on_unsafe_or_ambiguous_interval(predicate, dialect):
    assert (
        plan_calendar_day(
            "SELECT id FROM readings WHERE " + predicate, dialect, schema()
        )
        is None
    )


def test_native_date_interval_unchanged():
    assert (
        plan_calendar_day(
            "SELECT id FROM readings WHERE recorded_at BETWEEN '2022-07-01' AND '2022-07-06'",
            "postgres",
            schema("date"),
        )
        is None
    )


def test_structural_context_keeps_all_types_and_keys_without_annotations():
    from features.ask.calendar_day import structural_schema
    from features.ask.engine.ask3.types import SchemaInfo, TableInfo, ColumnInfo
    from types import SimpleNamespace

    schema_info = SchemaInfo(
        "private-target",
        "mysql",
        {
            "observations": TableInfo(
                "observations",
                {
                    "id": ColumnInfo(
                        "id",
                        "int",
                        description="private column note",
                        is_primary_key=True,
                    ),
                    "date": ColumnInfo("date", "text"),
                    "object_id": ColumnInfo("object_id", "int", is_foreign_key=True),
                },
                description="private table note",
                business_context="private definition",
                relationships=[
                    {
                        "target": "objects",
                        "join": "observations.object_id = objects.id",
                        "type": "many_to_one",
                        "description": "private relationship note",
                    }
                ],
            ),
            "objects": TableInfo(
                "objects",
                {
                    "id": ColumnInfo("id", "int", is_primary_key=True),
                    "birthday": ColumnInfo("birthday", "text"),
                },
                relationships=[
                    SimpleNamespace(
                        target_table="archive",
                        join_pattern="objects.id = archive.id",
                        relationship_type="one_to_one",
                    )
                ],
            ),
            "archive": TableInfo("archive", {"id": ColumnInfo("id", "int")}),
        },
        formatted_schema="private formatted content",
        terminology={"private": "business value"},
    )
    value = structural_schema(schema_info)
    assert set(value["tables"]) == {"observations", "objects", "archive"}
    assert value["tables"]["observations"]["columns"]["id"] == {
        "data_type": "int",
        "is_primary_key": True,
        "is_foreign_key": False,
    }
    assert value["tables"]["observations"]["columns"]["object_id"]["is_foreign_key"]
    assert value["tables"]["objects"]["relationships"] == [
        {"target": "archive", "join": "objects.id = archive.id", "type": "one_to_one"}
    ]
    assert "private" not in json.dumps(value)


def test_calendar_router_receives_complete_structure_and_original_sql():
    from features.ask.calendar_day import route_calendar_day, structural_schema

    s = schema()
    s.tables["unreferenced"] = NS(columns={"other_date": NS(data_type="text")})
    plan = plan_calendar_day(SQL, "mysql", s)
    calls = []

    class Capture:
        def generate_response(self, **kwargs):
            calls.append(kwargs)
            return {
                "response": json.dumps(
                    {
                        "comparison_granularity": "inclusive_end_day",
                        "column_matches_question": True,
                        "source_excerpt": "through July 6, 2022",
                    }
                ),
                "model": "scripted",
            }

    routing = route_calendar_day(
        "Show readings through July 6, 2022",
        SQL,
        "mysql",
        Capture(),
        structural_facts=plan.facts,
    )
    value = json.loads(calls[0]["prompt"])
    assert value["parsed_sql_facts"]["complete_structural_schema"] == structural_schema(
        s
    )
    assert value["generated_sql"] == SQL
    assert value["parsed_sql_facts"]["column"] == "recorded_at"
    assert routing["activate"]
