from decimal import Decimal
from types import SimpleNamespace
import pytest
import sqlglot
from sqlglot import exp
from features.ask.text_mean_precision import (
    plan_text_mean,
    safe_text_mean_proof,
    accepts_text_mean_result,
)


def schema(kind="text"):
    return SimpleNamespace(
        tables={
            "readings": SimpleNamespace(
                columns={
                    "temperature": SimpleNamespace(data_type=kind),
                    "sensor_id": SimpleNamespace(data_type="int"),
                }
            )
        }
    )


def test_text_measure_plan_preserves_scope_and_null_count():
    sql = (
        "SELECT AVG(CAST(temperature AS DECIMAL(10,3))) FROM readings WHERE sensor_id=4"
    )
    plan = plan_text_mean(sql, "mysql", schema())
    assert plan
    old = sqlglot.parse_one(sql, read="mysql")
    new = sqlglot.parse_one(plan.candidate_sql, read="mysql")
    proof = sqlglot.parse_one(plan.proof_sql, read="mysql")
    assert old.args["where"] == new.args["where"] == proof.args["where"]
    assert new.find(exp.Avg).this.args["to"].this == exp.DataType.Type.DOUBLE
    assert proof.find(exp.Count).this == exp.column("temperature")
    assert safe_text_mean_proof([[4, 4, 23.75]], 3)
    assert accepts_text_mean_result(
        [[Decimal("22.125")]], [[22.125]], [[4, 4, 23.75]], 3
    )


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT AVG(temperature) FROM readings",
        "SELECT AVG(CAST(temperature AS DECIMAL(30,12))) FROM readings",
        "SELECT AVG(DISTINCT CAST(temperature AS DECIMAL(10,3))) FROM readings",
        "SELECT AVG(CAST(temperature AS DECIMAL(10,3))) FROM readings WHERE RAND()>0.5",
        "SELECT AVG(CAST(temperature AS DECIMAL(10,3))) FROM readings GROUP BY sensor_id",
        "SELECT AVG(CAST(temperature AS DECIMAL(10,3))) FROM readings; DELETE FROM readings",
    ],
)
def test_unsupported_text_mean_shapes_abstain(sql):
    assert plan_text_mean(sql, "mysql", schema()) is None


def test_native_numeric_schema_and_postgresql_keep_their_cast():
    sql = "SELECT AVG(CAST(temperature AS DECIMAL(10,3))) FROM readings"
    assert plan_text_mean(sql, "mysql", schema("decimal(10,3)")) is None
    assert plan_text_mean(sql, "postgresql", schema()) is None


@pytest.mark.parametrize(
    "proof",
    [
        [[0, 0, None]],
        [[3, 2, 25.0]],
        [[3, 3, float("inf")]],
        [[3, 3, float("nan")]],
        [[True, 1, 2.0]],
        [[1_000_001, 1_000_001, 1.0]],
        [[10000, 10000, 1e12]],
        [[3, 3, None]],
    ],
)
def test_unproven_text_or_unsafe_range_is_rejected(proof):
    assert not safe_text_mean_proof(proof, 3)


def test_candidate_must_agree_with_original_quantization_and_remain_scalar():
    proof = [[4, 4, 23.75]]
    assert not accepts_text_mean_result([[Decimal("22.125")]], [[25.0]], proof, 3)
    assert not accepts_text_mean_result(
        [[Decimal("22.125")]], [[float("nan")]], proof, 3
    )
    assert not accepts_text_mean_result(
        [[Decimal("22.125")]], [[22.125], [22.125]], proof, 3
    )


import asyncio
import json
from features.ask.engine.ask3.context import Ask3Context
from features.ask.engine.ask3.types import ExecutionResult
from features.ask.service import AskService


class Decision:
    def __init__(self, kind="quantity"):
        self.kind = kind

    def generate_response(self, **kwargs):
        return {
            "response": json.dumps(
                {
                    "decision": "activate",
                    "measure_kind": self.kind,
                    "source_excerpt": "Average temperature",
                }
            ),
            "model": "scripted",
            "tokens_used": 12,
        }


def context():
    return Ask3Context(
        question="Average temperature",
        target="sensors",
        db_type="mysql",
        target_config={"engine": "mysql"},
        schema_info=schema(),
        sql="SELECT AVG(CAST(temperature AS DECIMAL(10,3))) FROM readings",
        execution_result=ExecutionResult(
            rows=[[Decimal("22.125")]], columns=["average_temperature"], row_count=1
        ),
        enforce_result_limit=False,
    )


@pytest.mark.parametrize("value,status", [(22.125, "normalized"), (23.0, "reverted")])
def test_canonical_service_checks_text_mean_and_restores_on_disagreement(value, status):
    calls = []

    def execute(sql, config):
        calls.append(sql)
        return {
            "success": True,
            "rows": [[4, 4, 23.75]] if len(calls) == 1 else [[value]],
            "columns": ["count", "valid", "maximum"]
            if len(calls) == 1
            else ["average_temperature"],
        }

    ctx = context()
    original = ctx.sql
    ctx = asyncio.run(
        AskService(
            llm_manager=Decision(),
            db_executor=execute,
            text_mean_precision_enabled=True,
        )._apply_text_mean_precision(ctx)
    )
    assert ctx.text_mean_precision["status"] == status and len(calls) == 2
    assert ctx.text_mean_probe_diagnostics["calls"] == 2
    assert len(ctx.llm_calls) == 1
    if status == "reverted":
        assert ctx.sql == original and ctx.execution_result.rows == [
            [Decimal("22.125")]
        ]
    else:
        assert ctx.sql != original and ctx.execution_result.rows == [[value]]
    restored = Ask3Context.from_dict(ctx.to_dict())
    assert restored.text_mean_precision == ctx.text_mean_precision
    assert restored.text_mean_probe_diagnostics == ctx.text_mean_probe_diagnostics


def test_non_numeric_storage_proof_prevents_candidate_execution():
    calls = []

    def execute(sql, config):
        calls.append(sql)
        return {
            "success": True,
            "rows": [[4, 3, 23.75]],
            "columns": ["count", "valid", "maximum"],
        }

    ctx = context()
    original = ctx.sql
    ctx = asyncio.run(
        AskService(
            llm_manager=Decision(), db_executor=execute
        )._apply_text_mean_precision(ctx)
    )
    assert len(calls) == 1 and ctx.sql == original
    assert ctx.text_mean_precision["reason"] == "unsafe-or-unavailable-proof"


def test_money_and_unincorporated_context_do_not_change_text_mean():
    def forbidden(*args):
        raise AssertionError("No database probe expected")

    ctx = asyncio.run(
        AskService(
            llm_manager=Decision("money"), db_executor=forbidden
        )._apply_text_mean_precision(context())
    )
    assert ctx.text_mean_precision["reason"] == "model-abstained"
    ctx = context()
    ctx.provided_context = "Preserve exact decimal readings."
    ctx = asyncio.run(
        AskService(
            llm_manager=Decision(), db_executor=forbidden
        )._apply_text_mean_precision(ctx)
    )
    assert ctx.text_mean_precision["reason"] == "additional-context-requires-abstention"
