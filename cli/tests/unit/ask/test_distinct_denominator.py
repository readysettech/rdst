from pathlib import Path
import importlib.util
import pytest, sqlglot
from sqlglot import exp
from features.ask.occurrence_percentage import (
    plan_occurrence_percentage,
    prove_occurrence_percentage,
)

spec = importlib.util.spec_from_file_location(
    "base_occurrence_test", Path(__file__).with_name("test_occurrence_percentage.py")
)
old = importlib.util.module_from_spec(spec)
spec.loader.exec_module(old)
SQL = old.SQL.replace("COUNT(*)", "COUNT(DISTINCT c.id)")


def test_only_two_deduplication_sites_change():
    p = plan_occurrence_percentage(SQL, "mysql", old.SCHEMA)
    assert p
    before = sqlglot.parse_one(SQL, read="mysql")
    after = sqlglot.parse_one(p.candidate_sql, read="mysql")
    after.args["from_"].this.this.set("distinct", exp.Distinct())
    after.find(exp.Div).expression.set(
        "this", before.find(exp.Div).expression.this.copy()
    )
    assert after == before
    proof = sqlglot.parse_one(p.proof_sql, read="mysql")
    assert len(proof.expressions) == 6
    a, b = proof.expressions[-2:]
    assert isinstance(a.this.expressions[0].this, exp.Star)
    assert isinstance(b.this.expressions[0].this, exp.Distinct)
    assert (
        a.this.args["from_"]
        == b.this.args["from_"]
        == exp.From(this=before.args["joins"][0].this.copy())
    )


@pytest.mark.parametrize(
    "argument",
    [
        "DISTINCT c.points",
        "DISTINCT occ.customer_id",
        "DISTINCT c.id,c.points",
        "DISTINCT id",
        "DISTINCT c.id+0",
        "c.id",
    ],
)
def test_only_exact_bound_dimension_key_can_be_deduplicated(argument):
    assert (
        plan_occurrence_percentage(
            old.SQL.replace("COUNT(*)", "COUNT(" + argument + ")"), "mysql", old.SCHEMA
        )
        is None
    )


@pytest.mark.parametrize(
    "counts",
    [[2, 1], [2, 0], [2, None], [True, True], [0, 0], [2**53 + 1, 2**53 + 1], [2, 2.0]],
)
def test_dimension_nonunique_null_or_invalid_proof_abstains(counts):
    assert prove_occurrence_percentage([[50.0]], [[2, 1, 4, 3] + counts]) is None


def test_proved_unique_dimension_uses_identical_arithmetic():
    a = prove_occurrence_percentage([[50.0]], [[2, 1, 4, 3]])
    b = prove_occurrence_percentage([[50.0]], [[2, 1, 4, 3, 2, 2]])
    assert a == b and a["expected_percentage"] == 75.0


def test_postgres_abstains():
    assert plan_occurrence_percentage(SQL, "postgres", old.SCHEMA) is None


@pytest.mark.parametrize(
    "stats,truncated,candidate,expected_status,expected_calls",
    [
        ([2, 1, 4, 3, 2, 2], False, [[75.0]], "normalized", 2),
        ([2, 1, 4, 3, 3, 2], False, [[75.0]], "unchanged", 1),
        ([2, 1, 4, 3, 2, 2], True, [[75.0]], "unchanged", 1),
        ([2, 1, 4, 3, 2, 2], False, [[74.0]], "reverted", 2),
    ],
)
def test_canonical_service_dimension_proof_and_restoration(
    stats, truncated, candidate, expected_status, expected_calls
):
    import asyncio, hashlib
    from features.ask.service import AskService

    c = old.context()
    c.sql = SQL
    c.correction_intent_routing["selected_sql_sha256"] = hashlib.sha256(
        SQL.encode()
    ).hexdigest()
    calls = []

    def execute(sql, config):
        calls.append(sql)
        return {
            "success": True,
            "rows": [stats] if len(calls) == 1 else candidate,
            "columns": ["v"] * 6 if len(calls) == 1 else ["percentage"],
            "truncated": truncated if len(calls) == 1 else False,
        }

    actual = asyncio.run(
        AskService(
            llm_manager=old.Adapter(), db_executor=execute
        )._apply_occurrence_percentage(c)
    )
    assert (
        len(calls) == expected_calls
        and actual.occurrence_percentage["status"] == expected_status
    )
    assert actual.execution_result.rows == (
        candidate if expected_status == "normalized" else [[50.0]]
    )
    assert (actual.sql != SQL) == (expected_status == "normalized")
