import asyncio
import copy
import json
import time

import pytest

from features.ask import execution_error_repair, service as ask_service
from features.ask.engine.ask3.context import Ask3Context
from features.ask.engine.ask3.types import (
    ColumnInfo,
    ExecutionResult,
    SchemaInfo,
    TableInfo,
)
from features.ask.service import AskService

SQL = "SELECT amout FROM items WHERE id = 1"
REPAIRED = "SELECT amount FROM items WHERE id = 1"
ERROR = "(1054, \"Unknown column 'amout' in 'field list'\")"


class Model:
    def __init__(self, sql=REPAIRED):
        self.sql = sql
        self.calls = []

    def generate_response(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "response": json.dumps(
                {"sql": self.sql, "explanation": "Fixed the column."}
            ),
            "tokens_used": 10,
            "model": "fixture",
        }


def context(error=ERROR, error_kind="query_execution", error_code=None):
    return Ask3Context(
        question="Show the amount for item one.",
        target="fixture",
        db_type="mysql",
        target_config={"engine": "mysql"},
        sql=SQL,
        generated_sql=SQL,
        schema_info=SchemaInfo(
            target="fixture",
            db_type="mysql",
            tables={
                "items": TableInfo(
                    name="items",
                    columns={
                        n: ColumnInfo(name=n, data_type="int") for n in ("id", "amount")
                    },
                )
            },
        ),
        execution_result=ExecutionResult(
            error=error, error_kind=error_kind, error_code=error_code
        ),
        enforce_result_limit=False,
    )


def run(ctx, model, execute):
    service = AskService(llm_manager=model, db_executor=execute)
    return asyncio.run(service._apply_execution_error_repair(ctx))


@pytest.mark.parametrize(
    ("error", "kind", "code", "db_type"),
    [
        (ERROR, "query_execution", None, "mysql"),
        ("(1064, 'You have an error in your SQL syntax')", None, None, "mysql"),
        ("(1146, \"Table 'db.t' doesn't exist\")", None, None, "mysql"),
        (
            "(1055, 'Expression #2 of SELECT list is not in GROUP BY')",
            None,
            None,
            "mysql",
        ),
        ("(1242, 'Subquery returns more than 1 row')", None, None, "mysql"),
        ('column "amout" does not exist', "query_execution", "42703", "postgresql"),
        ("syntax error at or near FROM", "query_execution", "42601", "postgresql"),
        ("division by zero", "query_execution", "22012", "postgresql"),
        ("more than one row returned by a subquery", None, "21000", "postgresql"),
    ],
)
def test_statement_errors_qualify_for_repair(error, kind, code, db_type):
    assert execution_error_repair.qualifies(error, kind, code, db_type)


@pytest.mark.parametrize(
    ("error", "kind", "code", "db_type"),
    [
        (
            "(1142, \"SELECT command denied to user 'u'@'h' for table 't'\")",
            None,
            None,
            "mysql",
        ),
        (
            "(1370, \"execute command denied to user 'u'@'%' for routine 'db.f'\")",
            None,
            None,
            "mysql",
        ),
        (
            "(1045, \"Access denied for user 'u'@'h'\")",
            "query_execution",
            None,
            "mysql",
        ),
        ("(1205, 'Lock wait timeout exceeded')", "query_execution", None, "mysql"),
        ("(1213, 'Deadlock found when trying to get lock')", None, None, "mysql"),
        (
            "(2013, 'Lost connection to MySQL server during query')",
            "connection",
            None,
            "mysql",
        ),
        ("(3024, 'Query execution was interrupted')", None, None, "mysql"),
        (
            "Query exceeded the 30-second execution limit.",
            "query_timeout",
            None,
            "mysql",
        ),
        ("Query exceeded benchmark wall-clock timeout", "timeout", None, "mysql"),
        ("Query result exceeded benchmark bounds", "result_too_large", None, "mysql"),
        ("Only a single statement is allowed.", "refused", None, "mysql"),
        (ERROR, "query_timeout", None, "mysql"),
        ("unknown failure", None, None, "mysql"),
        ("permission denied for table t", "query_execution", "42501", "postgresql"),
        (
            "canceling statement due to statement timeout",
            "query_timeout",
            "57014",
            "postgresql",
        ),
        ("server closed the connection", "connection", "08006", "postgresql"),
        ("out of memory", "query_execution", "53200", "postgresql"),
        ("deadlock detected", "query_execution", "40P01", "postgresql"),
        ('column "amout" does not exist', "query_execution", None, "postgresql"),
        (ERROR, "query_execution", None, "sqlite"),
        (None, None, None, "mysql"),
    ],
)
def test_errors_outside_the_statement_never_qualify(error, kind, code, db_type):
    assert not execution_error_repair.qualifies(error, kind, code, db_type)


def test_repair_runs_once_and_replaces_the_failed_answer():
    ctx = context()
    model = Model()
    executed = []

    def execute(sql, config):
        executed.append(sql)
        return {"success": True, "rows": [[10]], "columns": ["amount"]}

    ctx = run(ctx, model, execute)

    assert ctx.execution_error_repair["status"] == "normalized"
    assert ctx.sql == REPAIRED and executed == [REPAIRED]
    assert ctx.execution_result.rows == [[10]] and not ctx.execution_result.error
    [call] = model.calls
    assert call["purpose"] == "sql_validation_repair"
    assert "The database rejected this SQL when it ran it: (1054" in call["prompt"]
    assert SQL in call["prompt"]
    assert [entry["phase"] for entry in ctx.llm_calls] == ["execution_repair"]
    saved = Ask3Context.from_dict(ctx.to_dict())
    assert saved.execution_error_repair == ctx.execution_error_repair


def test_a_repair_that_still_fails_keeps_the_original_error():
    ctx = context()
    before = copy.deepcopy(ctx.execution_result)
    model = Model()
    executed = []

    def execute(sql, config):
        executed.append(sql)
        return {
            "success": False,
            "error": "(1054, \"Unknown column 'amount' in 'field list'\")",
            "error_kind": "query_execution",
        }

    ctx = run(ctx, model, execute)

    assert ctx.execution_error_repair["status"] == "reverted"
    assert ctx.execution_error_repair["reason"] == "candidate-execution-failed"
    assert ctx.sql == ctx.generated_sql == SQL and ctx.execution_result == before
    assert len(model.calls) == 1 and executed == [REPAIRED]


@pytest.mark.parametrize(
    "repaired",
    [
        "DELETE FROM items WHERE id = 1",
        "SELECT amount FROM items; DROP TABLE items",
        "SELECT amount FROM items; SELECT id FROM items",
        "UPDATE items SET amount = 0",
    ],
)
def test_a_repair_that_writes_or_runs_several_statements_is_never_executed(repaired):
    ctx = context()
    before = copy.deepcopy(ctx.execution_result)

    def execute(sql, config):
        raise AssertionError(f"A rejected repair ran: {sql}")

    ctx = run(ctx, Model(repaired), execute)

    assert ctx.execution_error_repair["status"] == "reverted"
    assert ctx.sql == SQL and ctx.execution_result == before


def test_a_repair_that_fails_validation_is_never_executed():
    ctx = context()

    def execute(sql, config):
        raise AssertionError(f"An invalid repair ran: {sql}")

    ctx = run(ctx, Model("SELECT amount FROM missing_items"), execute)

    assert ctx.execution_error_repair["reason"] == "candidate-validation-failed"
    assert ctx.sql == SQL and not ctx.validation_errors


@pytest.mark.parametrize(
    "result",
    [
        None,
        ExecutionResult(columns=["amount"], rows=[[10]]),
        ExecutionResult(
            error="(1142, \"SELECT command denied to user 'u'@'h' for table 't'\")",
            error_kind="query_execution",
        ),
        ExecutionResult(error="Query exceeded the limit.", error_kind="query_timeout"),
    ],
)
def test_no_repair_without_a_statement_error(result):
    ctx = context()
    ctx.execution_result = result
    model = Model()

    def execute(sql, config):
        raise AssertionError("No repair runs")

    ctx = run(ctx, model, execute)

    assert ctx.execution_error_repair["status"] == "unchanged"
    assert not model.calls and ctx.sql == SQL


def test_a_repair_that_outlasts_the_statement_deadline_is_abandoned(monkeypatch):
    monkeypatch.setattr(ask_service, "_EXECUTION_GRACE_SECONDS", 0)
    ctx = context()
    ctx.timeout_seconds = 0.05
    before = copy.deepcopy(ctx.execution_result)

    def execute(sql, config):
        time.sleep(0.5)
        return {"success": True, "rows": [[10]], "columns": ["amount"]}

    ctx = run(ctx, Model(), execute)

    assert ctx.execution_error_repair["reason"] == "candidate-timed-out"
    assert ctx.sql == SQL and ctx.execution_result == before


def test_postgres_execution_errors_carry_their_sqlstate(monkeypatch):
    psycopg2 = pytest.importorskip("psycopg2")
    from features.ask.engine.ask3.phases import execute as execute_phase

    class UndefinedColumn(psycopg2.ProgrammingError):
        pgcode = "42703"

    def connect(**kwargs):
        raise UndefinedColumn('column "amout" does not exist')

    monkeypatch.setattr(psycopg2, "connect", connect)
    monkeypatch.setattr(
        "shared.db_connection.resolve_connection_params",
        lambda **kwargs: {"host": "h", "user": "u", "database": "d"},
    )
    monkeypatch.setattr(
        "shared.db_connection.postgres_connection_kwargs", lambda params, **kwargs: {}
    )

    result = execute_phase._execute_postgres(SQL, {"engine": "postgresql"}, 30)

    assert result["error_code"] == "42703"
    assert result["error_kind"] == "query_execution"
