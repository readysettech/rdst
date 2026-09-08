from types import SimpleNamespace as S
import pytest, sqlglot
from sqlglot import exp
from features.ask.calendar_component import (
    plan_month_component,
    prove_yearmonth_axis,
    accepts_month_component,
)


def schema(ambiguous=False):
    return S(
        tables={
            "dispatches": S(
                columns={
                    "period_key": S(data_type="varchar(8)"),
                    "units": S(data_type="int"),
                    "site_id": S(data_type="int"),
                }
            ),
            "sites": S(
                columns={
                    "id": S(data_type="int"),
                    "region": S(data_type="varchar(8)"),
                    **({"period_key": S(data_type="varchar(8)")} if ambiguous else {}),
                }
            ),
        }
    )


SQL = "SELECT period_key FROM dispatches d JOIN sites s ON d.site_id=s.id WHERE s.region='north' AND d.period_key BETWEEN '202401' AND '202412' GROUP BY d.period_key ORDER BY SUM(d.units) DESC LIMIT 1"


@pytest.mark.parametrize("dialect", ["mysql", "postgresql"])
@pytest.mark.parametrize(
    "query",
    [
        SQL,
        SQL.replace("SELECT period_key", "SELECT d.period_key"),
        SQL.replace("202401", "202403").replace("202412", "202409"),
        SQL.replace(" GROUP BY d.period_key", "").replace("SUM(d.units)", "d.units"),
        SQL.replace("SELECT period_key", "SELECT period_key AS reporting_month"),
    ],
)
def test_preserves_all_clauses(dialect, query):
    plan = plan_month_component(query, dialect, schema())
    assert plan
    d = "postgres" if dialect == "postgresql" else dialect
    a = sqlglot.parse_one(query, read=d)
    b = sqlglot.parse_one(plan.sql, read=d)
    proof = sqlglot.parse_one(plan.proof_sql, read=d)
    assert all(
        a.args.get(k) == b.args.get(k)
        for k in set(a.args) | set(b.args)
        if k != "expressions"
    )
    assert (
        a.args["where"] == proof.args["where"]
        and a.args["joins"] == proof.args["joins"]
    )
    assert plan.year == "2024"
    assert prove_yearmonth_axis([["202403"], ["202407"]], "2024")
    assert accepts_month_component([["202407"]], [["07"]], "2024")


@pytest.mark.parametrize(
    "query",
    [
        SQL.replace("202412", "202501"),
        SQL.replace("202401", "202413"),
        SQL.replace("202401", "202400"),
        SQL.replace("202401", "202411").replace("202412", "202410"),
        SQL.replace("202401", "２０２４０１"),
        SQL.replace("202401", "2024-01"),
        SQL.replace("'202401'", "202401"),
        SQL.replace("d.period_key BETWEEN", "s.id BETWEEN"),
        SQL.replace("AND d.period_key", "OR d.period_key"),
        SQL.replace("AND d.period_key", "AND NOT d.period_key"),
        SQL.replace("GROUP BY d.period_key", "GROUP BY d.units"),
        SQL.replace("SUM(d.units)", "1"),
        SQL.replace("'202412'", "'202412' AND period_key LIKE '2024%'"),
        SQL.replace("SELECT period_key", "SELECT period_key, d.units"),
        SQL.replace("SELECT period_key", "SELECT DISTINCT period_key"),
        SQL.replace("JOIN sites s", "JOIN sites d"),
    ],
)
def test_abstentions(query):
    assert plan_month_component(query, "mysql", schema()) is None


def test_ambiguous_unqualified_field():
    assert plan_month_component(SQL, "mysql", schema(True)) is None


import asyncio, json
from features.ask.service import AskService
from features.ask.engine.ask3.context import Ask3Context
from features.ask.engine.ask3.types import ExecutionResult


class Decision:
    def generate_response(self, **kwargs):
        return {
            "response": json.dumps(
                {"decision": "activate", "source_excerpt": "Which month"}
            ),
            "model": "fixture",
        }


@pytest.mark.parametrize("partial_call", [1, 2])
@pytest.mark.parametrize("dialect", ["mysql", "postgresql"])
def test_partial_proof_or_candidate_restores_original(partial_call, dialect):
    ctx = Ask3Context(
        question="Which month in 2024 had peak northern shipments?",
        target="logistics",
        db_type=dialect,
        target_config={"engine": dialect},
        schema_info=schema(),
        sql=SQL,
        execution_result=ExecutionResult(
            rows=[["202407"]], columns=["period_key"], row_count=1
        ),
        enforce_result_limit=False,
    )
    calls = []

    def execute(sql, config):
        calls.append(sql)
        return {
            "success": True,
            "rows": [["202401"], ["202407"]] if len(calls) == 1 else [["07"]],
            "columns": ["period_key"],
            "truncated": len(calls) == partial_call,
        }

    ctx = asyncio.run(
        AskService(llm_manager=Decision(), db_executor=execute)._apply_month_component(
            ctx
        )
    )
    assert ctx.sql == SQL and ctx.execution_result.rows == [["202407"]]
    assert len(calls) == partial_call
    assert ctx.month_component["status"] in ["unchanged", "reverted"]
