import asyncio
import copy

import pytest

from features.ask.engine.ask3.context import Ask3Context
from features.ask.engine.ask3.types import (
    ColumnInfo,
    ExecutionResult,
    SchemaInfo,
    TableInfo,
)
from features.ask.service import AskService
from features.ask.value_probe import create_identifier_quoting_probe_executor

ERROR = "(1064, 'syntax error near reserved word')"
SQL = "SELECT amount FROM groups WHERE id=1"


class NoModel:
    def generate_response(self, **kwargs):
        raise AssertionError("Identifier quoting must not call a model")


def context():
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
                "groups": TableInfo(
                    name="groups",
                    columns={
                        n: ColumnInfo(name=n, data_type="int") for n in ("id", "amount")
                    },
                )
            },
        ),
        execution_result=ExecutionResult(error=ERROR, error_kind="ProgrammingError"),
        enforce_result_limit=False,
    )


@pytest.mark.parametrize("rows", [[[10]], []])
def test_complete_success_preserves_text_and_generation(rows):
    ctx = context()
    calls = []

    def execute(sql, config):
        calls.append(sql)
        return {
            "success": True,
            "rows": [["GROUPS", 1]] if len(calls) == 1 else rows,
            "columns": ["WORD", "RESERVED"] if len(calls) == 1 else ["amount"],
        }

    asyncio.run(
        AskService(
            llm_manager=NoModel(), db_executor=execute
        )._apply_identifier_quoting(ctx)
    )
    assert ctx.identifier_quoting["status"] == "normalized"
    assert ctx.sql == SQL.replace("FROM groups", "FROM `groups`") == calls[-1]
    assert ctx.generated_sql == SQL and ctx.execution_result.rows == rows
    assert len(calls) == 2 and not ctx.llm_calls
    saved = Ask3Context.from_dict(ctx.to_dict())
    assert saved.identifier_quoting == ctx.identifier_quoting
    assert (
        saved.identifier_quoting_probe_diagnostics
        == ctx.identifier_quoting_probe_diagnostics
    )


@pytest.mark.parametrize(
    "failure",
    [
        "proof-error",
        "proof-truncated",
        "ambiguous",
        "candidate-error",
        "candidate-truncated",
        "candidate-limit",
        "candidate-exception",
        "too-many",
    ],
)
def test_failed_or_incomplete_candidate_restores_original(failure):
    ctx = context()
    before = copy.deepcopy(ctx.execution_result)
    calls = []

    def execute(sql, config):
        calls.append(sql)
        proof = len(calls) == 1
        if failure == "candidate-exception" and not proof:
            raise RuntimeError("fixture")
        if (
            failure == "proof-error"
            and proof
            or failure == "candidate-error"
            and not proof
        ):
            return {"success": False, "error": "fixture error", "rows": []}
        rows = [["GROUPS", 1]] if proof else [[10]]
        if failure == "ambiguous" and proof:
            rows = [["GROUPS", 1], ["GROUPS", 1]]
        if failure == "candidate-limit" and not proof:
            rows = [[10]] * ctx.max_rows
        if failure == "too-many" and not proof:
            rows = [[10]] * 10001
        return {
            "success": True,
            "rows": rows,
            "columns": ["amount"],
            "truncated": failure == "proof-truncated"
            and proof
            or failure == "candidate-truncated"
            and not proof,
        }

    asyncio.run(
        AskService(
            llm_manager=NoModel(), db_executor=execute
        )._apply_identifier_quoting(ctx)
    )
    assert ctx.identifier_quoting["status"] != "normalized"
    assert ctx.sql == ctx.generated_sql == SQL and ctx.execution_result == before
    assert len(calls) <= 2 and not ctx.llm_calls


@pytest.mark.parametrize(
    "kind",
    ["postgres", "success", "other-error", "no-result", "no-schema", "unknown-column"],
)
def test_ineligible_or_invalid_preflight_never_executes_candidate(kind):
    ctx = context()
    calls = []
    if kind == "postgres":
        ctx.db_type = "postgres"
    elif kind == "success":
        ctx.execution_result = ExecutionResult(rows=[[10]])
    elif kind == "other-error":
        ctx.execution_result.error = "(1146, 'missing table')"
    elif kind == "no-result":
        ctx.execution_result = None
    elif kind == "no-schema":
        ctx.schema_info = None
    elif kind == "unknown-column":
        ctx.sql = "SELECT missing FROM groups"
    before = ctx.sql

    def execute(sql, config):
        calls.append(sql)
        assert "INFORMATION_SCHEMA.KEYWORDS" in sql
        return {
            "success": True,
            "rows": [["GROUPS", 1]],
            "columns": ["WORD", "RESERVED"],
        }

    asyncio.run(
        AskService(
            llm_manager=NoModel(), db_executor=execute
        )._apply_identifier_quoting(ctx)
    )
    assert ctx.sql == before and ctx.identifier_quoting["status"] != "normalized"
    assert len(calls) == (kind == "unknown-column")


def test_shared_budget_and_read_only():
    ctx = context()
    calls = []

    def execute(sql, config):
        calls.append(sql)
        return {"success": True, "rows": [[1]]}

    bounded = create_identifier_quoting_probe_executor(ctx, execute)
    assert not bounded("DELETE FROM groups", ctx.target_config)["success"]
    assert bounded("SELECT 1", ctx.target_config)["success"]
    assert not bounded("SELECT 2", ctx.target_config)["success"]
    assert calls == ["SELECT 1"]


def test_shared_deadline(monkeypatch):
    from features.ask import value_probe

    now = [0.0]
    monkeypatch.setattr(value_probe.time, "monotonic", lambda: now[0])
    ctx = context()
    calls = []

    def execute(sql, config):
        calls.append(sql)
        return {"success": True, "rows": [[1]]}

    bounded = create_identifier_quoting_probe_executor(ctx, execute)
    assert bounded("SELECT 1", ctx.target_config)["success"]
    now[0] = 2.01
    assert not bounded("SELECT 2", ctx.target_config)["success"]
    assert calls == ["SELECT 1"]


def test_experimental_profiles_enable_quoting():
    from devtools.ask_benchmark.runner import (
        ASK_ACCURACY_PROFILES,
        ask_profile_service_flags,
    )

    assert not AskService(llm_manager=NoModel())._identifier_quoting_enabled
    for profile in ASK_ACCURACY_PROFILES:
        assert ask_profile_service_flags(profile)["identifier_quoting_enabled"] == (
            profile in {"glm-dev-v1", "candidate-v4"}
        )
