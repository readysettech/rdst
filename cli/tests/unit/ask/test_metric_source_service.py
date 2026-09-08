import asyncio, json
import pytest
from features.ask.service import AskService
from features.ask.engine.ask3.context import Ask3Context
from features.ask.engine.ask3.types import (
    SchemaInfo,
    TableInfo,
    ColumnInfo,
    ExecutionResult,
)

SQL = "SELECT YEAR(i.billed_on) AS year FROM invoices i JOIN sites s ON i.site_id=s.site_id WHERE s.region='West' GROUP BY YEAR(i.billed_on) ORDER BY SUM(i.charge) DESC LIMIT 1"
PERIODS = [["202301", 10, 10, 1], ["202401", 20, 20, 1]]


def context():
    def table(name, columns, relationships=None):
        return TableInfo(
            name=name,
            columns={n: ColumnInfo(name=n, data_type=t) for n, t in columns.items()},
            relationships=relationships or [],
        )

    schema = SchemaInfo(
        target="local",
        db_type="mysql",
        tables={
            "sites": table("sites", {"site_id": "int", "region": "varchar"}),
            "invoices": table(
                "invoices",
                {
                    "invoice_id": "int",
                    "site_id": "int",
                    "billed_on": "date",
                    "charge": "decimal",
                },
            ),
            "energy_usage": table(
                "energy_usage",
                {
                    "site_id": "int",
                    "reporting_month": "varchar",
                    "energy_used": "double",
                },
                [
                    {
                        "target": "sites",
                        "join": "energy_usage.site_id=sites.site_id",
                        "type": "many_to_one",
                    }
                ],
            ),
            "unrelated": table("unrelated", {"id": "int", "note": "text"}),
        },
    )
    return Ask3Context(
        question="Which year had the most total energy usage at West sites?",
        target="local",
        db_type="mysql",
        target_config={"engine": "mysql"},
        schema_info=schema,
        sql=SQL,
        execution_result=ExecutionResult(rows=[[2023]], columns=["year"], row_count=1),
        enforce_result_limit=False,
    )


class Decision:
    def __init__(self, **changes):
        self.changes = changes
        self.requests = []

    def generate_response(self, **kw):
        self.requests.append(kw)
        if kw["purpose"] == "annual_request_routing":
            return {
                "response": json.dumps(
                    {
                        "source_constraint": "none",
                        "source_quote": "",
                        "metric_definition": "ordinary",
                        "metric_quote": "",
                        "output_request": "year_only",
                        "aggregation_request": "sum",
                        "rank_request": "highest",
                        "time_constraint": "none",
                        "row_grain": "ordinary_fact_rows",
                        "population_conditions": [
                            {
                                "kind": "category_equality",
                                "quote": "West sites",
                                "value_quote": "West",
                            }
                        ],
                    }
                ),
                "model": "fixture",
                "tokens_used": 20,
            }
        v = {
            "decision": "replace",
            "option_id": "b0",
            "output_request": "year_only",
            "aggregate_request": "sum",
            "requested_metric_excerpt": "energy usage",
            "current_metric_match": "proxy",
            "selected_metric_match": "direct",
            "selected_period_matches_measure": True,
            "dimension_scope_preserved": True,
            "missing_definition": False,
            "existing_filters_match_question": "match",
            "question_scope_quote": "West sites",
        }
        v.update(self.changes)
        return {"response": json.dumps(v), "model": "fixture", "tokens_used": 40}


@pytest.mark.parametrize(
    "candidate,status",
    [
        ("2024", "normalized"),
        ("2023", "reverted"),
        (2024, "reverted"),
        (None, "reverted"),
    ],
)
def test_service_accepts_only_witnessed_year_and_restores(candidate, status):
    calls = []
    llm = Decision()

    def db(sql, config):
        calls.append(sql)
        return {
            "success": True,
            "rows": [[2, 2, 2]]
            if len(calls) == 1
            else PERIODS
            if len(calls) == 2
            else [[candidate]],
            "columns": ["n", "nonnull", "unique"]
            if len(calls) == 1
            else ["period", "sum", "absolute", "n"]
            if len(calls) == 2
            else ["year"],
        }

    ctx = asyncio.run(
        AskService(llm_manager=llm, db_executor=db)._apply_metric_source(context())
    )
    assert ctx.metric_source["status"] == status and len(calls) == 3
    assert ctx.metric_source_probe_diagnostics["max_calls"] == 3
    assert ctx.execution_result.rows == (
        [[candidate]] if status == "normalized" else [[2023]]
    )
    assert (ctx.sql != SQL) == (status == "normalized")
    assert len(ctx.llm_calls) == 2
    guard = json.loads(llm.requests[0]["prompt"])
    assert set(guard) == {"effective_question", "trigger_catalog"}
    payload = json.loads(llm.requests[1]["prompt"])
    assert "unrelated" in payload["complete_schema"]["tables"]
    restored = Ask3Context.from_dict(ctx.to_dict())
    assert (
        restored.metric_source == ctx.metric_source
        and restored.metric_source_probe_diagnostics
        == ctx.metric_source_probe_diagnostics
    )


@pytest.mark.parametrize(
    "change",
    [
        {"decision": "keep"},
        {"option_id": "invented"},
        {"output_request": "other"},
        {"aggregate_request": "other"},
        {"current_metric_match": "direct"},
        {"selected_metric_match": "unknown"},
        {"selected_period_matches_measure": False},
        {"dimension_scope_preserved": False},
        {"missing_definition": True},
        {"existing_filters_match_question": "mismatch"},
        {"question_scope_quote": "East"},
        {"requested_metric_excerpt": "made up"},
    ],
)
def test_semantic_guards_stop_before_database(change):
    def forbidden(*args):
        raise AssertionError("No SQL query permitted")

    ctx = asyncio.run(
        AskService(
            llm_manager=Decision(**change), db_executor=forbidden
        )._apply_metric_source(context())
    )
    assert ctx.sql == SQL and ctx.metric_source["reason"] == "model-abstained"


def test_missing_current_filter_literal_in_question_cannot_activate():
    ctx = context()
    ctx.question = ctx.question.replace("West", "East")

    def forbidden(*args):
        raise AssertionError("No SQL query permitted")

    ctx = asyncio.run(
        AskService(
            llm_manager=Decision(question_scope_quote="East sites"),
            db_executor=forbidden,
        )._apply_metric_source(ctx)
    )
    assert ctx.sql == SQL and ctx.metric_source["reason"] == "unsupported-request-contract"

    assert len(ctx.llm_calls) == 1


@pytest.mark.parametrize("dimension", [[[2, 2, 1]], [], [[2, 1, 1]]])
def test_dimension_ambiguity_stops_after_first_probe(dimension):
    calls = []

    def db(*args):
        calls.append(args)
        return {
            "success": True,
            "rows": dimension,
            "columns": ["n", "nonnull", "distinct"],
        }

    ctx = asyncio.run(
        AskService(llm_manager=Decision(), db_executor=db)._apply_metric_source(
            context()
        )
    )
    assert (
        ctx.sql == SQL
        and len(calls) == 1
        and ctx.metric_source["reason"] == "dimension-key-not-proved-unique"
    )


@pytest.mark.parametrize(
    "periods",
    [
        [],
        [["202301", 10, 10, 1], ["202401", 10, 10, 1]],
        [["202313", 1, 1, 1], ["202401", 20, 20, 1]],
    ],
)
def test_calendar_and_tie_failures_stop_after_second_probe(periods):
    calls = []

    def db(*args):
        calls.append(args)
        return {
            "success": True,
            "rows": [[2, 2, 2]] if len(calls) == 1 else periods,
            "columns": ["n", "nonnull", "distinct"]
            if len(calls) == 1
            else ["period", "sum", "absolute", "n"],
        }

    ctx = asyncio.run(
        AskService(llm_manager=Decision(), db_executor=db)._apply_metric_source(
            context()
        )
    )
    assert (
        ctx.sql == SQL
        and len(calls) == 2
        and ctx.metric_source["reason"] == "calendar-or-unique-winner-not-proved"
    )


def test_malformed_response_is_recorded_without_mutation():
    class Bad:
        def generate_response(self, **kw):
            return {"response": "{broken", "model": "fixture", "tokens_used": 2}

    ctx = asyncio.run(AskService(llm_manager=Bad())._apply_metric_source(context()))
    assert (
        ctx.sql == SQL
        and len(ctx.llm_calls) == 1
        and ctx.metric_source["status"] == "reverted"
    )


def test_user_context_abstains():
    ctx = context()
    ctx.provided_context = "Use our private definition of energy."
    ctx = asyncio.run(AskService(llm_manager=Decision())._apply_metric_source(ctx))
    assert (
        ctx.sql == SQL
        and ctx.metric_source["reason"] == "additional-context-requires-abstention"
    )


def test_budget_blocks_fourth_query_and_write():
    from features.ask.value_probe import create_metric_source_probe_executor

    ctx = context()
    calls = []

    def db(*args):
        calls.append(args)
        return {"success": True, "rows": [[1]], "columns": ["x"]}

    bounded = create_metric_source_probe_executor(ctx, db)
    assert not bounded("DELETE FROM sites", ctx.target_config)["success"]
    assert bounded("SELECT 1", ctx.target_config)["success"]
    assert bounded("SELECT 1", ctx.target_config)["success"]
    assert not bounded("SELECT 1", ctx.target_config)["success"]
    assert len(calls) == 2 and ctx.metric_source_probe_diagnostics["blocked_calls"] == 1


@pytest.mark.parametrize("kind", ["source", "definition", "uncertain"])
def test_question_constraint_blocks_binding_and_all_database_access(kind):
    class Constrained:
        def generate_response(self, **kw):
            assert kw["purpose"] == "annual_request_routing"
            v = dict(
                source_constraint="none",
                source_quote="",
                metric_definition="ordinary",
                metric_quote="",
            )
            if kind == "source":
                v.update(source_constraint="explicit", source_quote="West sites")
            elif kind == "definition":
                v.update(metric_definition="custom", metric_quote="energy usage")
            else:
                v["source_constraint"] = "uncertain"
            return dict(response=json.dumps(v), model="fixture", tokens_used=10)

    def forbidden(*a):
        raise AssertionError("No database access permitted")

    ctx = context()
    ctx = asyncio.run(
        AskService(
            llm_manager=Constrained(), db_executor=forbidden
        )._apply_metric_source(ctx)
    )
    assert ctx.metric_source["reason"] == "unsupported-request-contract"
    assert ctx.sql == SQL and len(ctx.llm_calls) == 1
    assert not ctx.metric_source_probe_diagnostics


def test_two_output_contract_preserves_requested_total_when_binder_abstains():
    ctx = context()
    ctx.sql = SQL.replace("AS year FROM", "AS year,SUM(i.charge) AS volume FROM")
    original = ctx.sql
    ctx.execution_result = ExecutionResult(
        rows=[[2023, 15]], columns=["year", "volume"], row_count=1
    )

    def forbidden(*args):
        raise AssertionError("No proof on unsupported output contract")

    ctx = asyncio.run(
        AskService(
            llm_manager=Decision(output_request="other"), db_executor=forbidden
        )._apply_metric_source(ctx)
    )
    assert ctx.sql == original and ctx.execution_result.rows == [[2023, 15]]
    assert ctx.metric_source["reason"] == "model-abstained" and len(ctx.llm_calls) == 2


def test_two_output_candidate_uses_same_guarded_witness_and_serializes():
    ctx = context()
    ctx.sql = SQL.replace(
        "AS year FROM", "AS year,SUM(i.charge) AS volume FROM"
    ).replace("ORDER BY SUM(i.charge)", "ORDER BY volume")
    ctx.execution_result = ExecutionResult(
        rows=[[2023, 15]], columns=["year", "volume"], row_count=1
    )
    calls = []

    def db(*args):
        calls.append(args)
        return dict(
            success=True,
            rows=[[2, 2, 2]]
            if len(calls) == 1
            else PERIODS
            if len(calls) == 2
            else [["2024"]],
            columns=["a", "b", "c"]
            if len(calls) == 1
            else ["a", "b", "c", "d"]
            if len(calls) == 2
            else ["year"],
        )

    ctx = asyncio.run(
        AskService(llm_manager=Decision(), db_executor=db)._apply_metric_source(ctx)
    )
    assert ctx.metric_source[
        "status"
    ] == "normalized" and ctx.execution_result.rows == [["2024"]]
    assert len(calls) == 3 and len(ctx.llm_calls) == 2
    assert Ask3Context.from_dict(ctx.to_dict()).metric_source == ctx.metric_source


@pytest.mark.parametrize(
    "change",
    [
        {"rank_request": "lowest"},
        {"rank_request": "ordinal"},
        {"aggregation_request": "count"},
        {"time_constraint": "explicit"},
        {"row_grain": "one_per_entity"},
        {"output_request": "other"},
        {"population_conditions": []},
    ],
)
def test_independent_contract_stops_unsupported_semantics_before_binding(change):
    class ContractOnly(Decision):
        def generate_response(self, **kw):
            assert kw["purpose"] == "annual_request_routing"
            response = super().generate_response(**kw)
            value = json.loads(response["response"])
            value.update(change)
            response["response"] = json.dumps(value)
            return response

    def forbidden(*args):
        raise AssertionError("No database call before supported request contract")

    ctx = asyncio.run(
        AskService(
            llm_manager=ContractOnly(), db_executor=forbidden
        )._apply_metric_source(context())
    )
    assert ctx.metric_source["reason"] == "unsupported-request-contract"
    assert ctx.sql == SQL and len(ctx.llm_calls) == 1


def test_contract_must_account_for_actual_sql_filter_literal():
    ctx = context()
    ctx.sql = ctx.sql.replace("s.region='West'", "s.region='East'")
    original = ctx.sql

    class ContractOnly(Decision):
        def generate_response(self, **kw):
            assert kw["purpose"] == "annual_request_routing"
            return super().generate_response(**kw)

    def forbidden(*args):
        raise AssertionError("Mismatched literal must block binding")

    ctx = asyncio.run(
        AskService(
            llm_manager=ContractOnly(), db_executor=forbidden
        )._apply_metric_source(ctx)
    )
    assert ctx.sql == original and len(ctx.llm_calls) == 1
    assert ctx.metric_source["reason"] == "unsupported-request-contract"
