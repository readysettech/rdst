from types import SimpleNamespace
import pytest
import sqlglot
from sqlglot import exp
from features.ask.calendar_component import (
    plan_month_component,
    prove_yearmonth_axis,
    accepts_month_component,
)


def schema(kind="varchar(8)"):
    return SimpleNamespace(
        tables={
            "dispatches": SimpleNamespace(
                columns={
                    "period_key": SimpleNamespace(data_type=kind),
                    "units": SimpleNamespace(data_type="int"),
                }
            )
        }
    )


@pytest.mark.parametrize("dialect", ["mysql", "postgresql"])
def test_calendar_month_plan_preserves_scope_metric_and_ranking(dialect):
    sql = "SELECT d.period_key FROM dispatches d WHERE d.period_key LIKE '2024%' GROUP BY d.period_key ORDER BY SUM(d.units) DESC LIMIT 1"
    plan = plan_month_component(sql, dialect, schema())
    assert plan
    d = "postgres" if dialect == "postgresql" else dialect
    old = sqlglot.parse_one(sql, read=d)
    new = sqlglot.parse_one(plan.sql, read=d)
    proof = sqlglot.parse_one(plan.proof_sql, read=d)
    for field in ["where", "group", "order", "limit"]:
        assert old.args[field] == new.args[field]
    assert new.expressions[0].find(exp.Substring)
    assert proof.args["limit"].expression.this == "13"
    assert proof.args["distinct"]
    assert prove_yearmonth_axis([["202401"], ["202407"]], "2024")
    assert accepts_month_component([["202407"]], [["07"]], "2024")


@pytest.mark.parametrize(
    "rows",
    [
        [["202400"]],
        [["202413"]],
        [["202312"]],
        [["2024-01"]],
        [["２０２４０１"]],
        [["202401"], ["202401"]],
        [[202401]],
        [],
        [["202401"]] * 13,
    ],
)
def test_ambiguous_or_invalid_calendar_axis_is_rejected(rows):
    assert not prove_yearmonth_axis(rows, "2024")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT period_key FROM dispatches GROUP BY period_key",
        "SELECT period_key FROM dispatches WHERE period_key LIKE '2024%' OR units>10 GROUP BY period_key",
        "SELECT period_key FROM dispatches WHERE period_key LIKE '2024%' GROUP BY units",
        "SELECT period_key FROM dispatches WHERE period_key LIKE '2024%' GROUP BY period_key ORDER BY 1",
        "SELECT period_key FROM dispatches WHERE period_key LIKE '2024%' AND RAND()>0.5 GROUP BY period_key",
    ],
)
def test_unsupported_scope_is_unchanged(sql):
    assert plan_month_component(sql, "mysql", schema()) is None


def test_numeric_codes_are_not_assumed_to_be_calendar_text():
    assert (
        plan_month_component(
            "SELECT period_key FROM dispatches WHERE period_key LIKE '2024%' GROUP BY period_key",
            "mysql",
            schema("int"),
        )
        is None
    )


def test_candidate_must_reconstruct_exact_original_axis_and_multiplicity():
    assert not accepts_month_component([["202407"]], [["08"]], "2024")
    assert not accepts_month_component([["202407"], ["202407"]], [["07"]], "2024")
    assert not accepts_month_component([["202407"]], [[7]], "2024")


import asyncio
import json
from features.ask.engine.ask3.context import Ask3Context
from features.ask.engine.ask3.types import ExecutionResult
from features.ask.service import AskService
from features.ask.value_probe import create_month_component_probe_executor


class Decision:
    def __init__(self, decision="activate"):
        self.decision = decision

    def generate_response(self, **kwargs):
        return {
            "response": json.dumps(
                {"decision": self.decision, "source_excerpt": "Which month"}
            ),
            "model": "scripted",
            "tokens_used": 10,
        }


def context(dialect="mysql"):
    return Ask3Context(
        question="Which month in 2024 had the most dispatched units?",
        target="logistics",
        db_type=dialect,
        target_config={"engine": dialect},
        schema_info=schema(),
        sql="SELECT period_key FROM dispatches WHERE period_key LIKE '2024%' GROUP BY period_key ORDER BY SUM(units) DESC LIMIT 1",
        execution_result=ExecutionResult(
            rows=[["202407"]], columns=["period_key"], row_count=1
        ),
        enforce_result_limit=False,
    )


@pytest.mark.parametrize("dialect", ["mysql", "postgresql"])
@pytest.mark.parametrize("candidate,status", [("07", "normalized"), ("08", "reverted")])
def test_canonical_calendar_candidate_requires_result_agreement(
    dialect, candidate, status
):
    calls = []

    def execute(sql, config):
        calls.append(sql)
        return {
            "success": True,
            "rows": [["202401"], ["202407"]] if len(calls) == 1 else [[candidate]],
            "columns": ["period_key"],
        }

    ctx = context(dialect)
    original = ctx.sql
    ctx = asyncio.run(
        AskService(
            llm_manager=Decision(), db_executor=execute, month_component_enabled=True
        )._apply_month_component(ctx)
    )
    assert ctx.month_component["status"] == status
    assert len(calls) == 2
    assert ctx.month_component_probe_diagnostics["calls"] == 2
    assert len(ctx.llm_calls) == 1
    if status == "reverted":
        assert ctx.sql == original and ctx.execution_result.rows == [["202407"]]
    else:
        assert ctx.sql != original and ctx.execution_result.rows == [["07"]]
    restored = Ask3Context.from_dict(ctx.to_dict())
    assert restored.month_component == ctx.month_component
    assert (
        restored.month_component_probe_diagnostics
        == ctx.month_component_probe_diagnostics
    )


@pytest.mark.parametrize("proof", [[["2024-07"]], [["202401"]] * 13, []])
def test_calendar_failed_proof_preserves_primary(proof):
    calls = []

    def execute(sql, config):
        calls.append(sql)
        return {"success": True, "rows": proof, "columns": ["period_key"]}

    ctx = context()
    original = ctx.sql
    ctx = asyncio.run(
        AskService(llm_manager=Decision(), db_executor=execute)._apply_month_component(
            ctx
        )
    )
    assert ctx.sql == original and ctx.execution_result.rows == [["202407"]]
    assert ctx.month_component["reason"] == "unsafe-or-unavailable-proof"
    assert len(calls) == 1


def test_calendar_abstention_avoids_database_access():
    def forbidden(*args):
        raise AssertionError("No database call expected")

    ctx = asyncio.run(
        AskService(
            llm_manager=Decision("abstain"), db_executor=forbidden
        )._apply_month_component(context())
    )
    assert ctx.month_component["reason"] == "model-abstained"


def test_calendar_probe_enforces_two_read_only_calls_and_deadline(monkeypatch):
    calls = []

    def execute(sql, config):
        calls.append(sql)
        return {"success": True, "rows": [["202401"], ["202402"]]}

    ctx = context()
    probe = create_month_component_probe_executor(ctx, execute)
    assert not probe("DELETE FROM dispatches", {})["success"]
    assert probe("SELECT DISTINCT period_key FROM dispatches LIMIT 13", {})["success"]
    assert not probe("SELECT period_key FROM dispatches", {})["success"]
    assert len(calls) == 1
    import features.ask.value_probe as module

    now = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    probe = create_month_component_probe_executor(context(), execute)
    now[0] = 105
    assert probe("SELECT 1", {})["error_kind"] == "probe_timeout"
    assert len(calls) == 1


@pytest.mark.parametrize("dialect", ["mysql", "postgresql"])
def test_row_month_projection_preserves_selected_event(dialect):
    sql = "SELECT d.period_key FROM dispatches d WHERE d.period_key LIKE '2024%' ORDER BY d.units DESC LIMIT 1"
    plan = plan_month_component(sql, dialect, schema())
    assert plan
    read = "postgres" if dialect == "postgresql" else dialect
    old = sqlglot.parse_one(sql, read=read)
    new = sqlglot.parse_one(plan.sql, read=read)
    assert new.args.get("group") is None
    for key, value in old.args.items():
        if key != "expressions":
            assert new.args.get(key) == value
    assert accepts_month_component([["202407"]], [["07"]], "2024")
    assert not accepts_month_component([["202407"]], [["08"]], "2024")


def test_implicit_aggregate_is_not_treated_as_an_event_row():
    sql = "SELECT period_key FROM dispatches WHERE period_key LIKE '2024%' ORDER BY SUM(units) DESC LIMIT 1"
    assert plan_month_component(sql, "mysql", schema()) is None
