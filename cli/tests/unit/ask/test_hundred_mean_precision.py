from types import SimpleNamespace
from decimal import Decimal
import json
import pytest
import sqlglot
from sqlglot import exp
from features.ask.mean_precision import (
    plan_integer_mean,
    accepts_hundred_indicator_result,
    route_mean_precision,
)


def schema():
    return SimpleNamespace(
        tables={
            n: SimpleNamespace(
                columns={
                    c: SimpleNamespace(data_type="int")
                    for c in ("id", "active", "ready", "done", "units", "depot_id")
                }
            )
            for n in (
                "shipments",
                "depots",
                "orders",
                "jobs",
                "inspections",
                "readings",
                "invoices",
                "records",
                "logs",
            )
        }
    )


@pytest.mark.parametrize(
    "a,b",
    [
        ("100", "0"),
        ("100.0", "0"),
        ("100", "0.0"),
        ("100.0", "0.0"),
        ("0", "100"),
        ("0.0", "100"),
        ("0", "100.0"),
        ("0.0", "100.0"),
    ],
)
def test_exact_case_and_all_row_clauses_are_preserved(a, b):
    sql = f"SELECT AVG(CASE WHEN s.active=1 THEN {a} ELSE {b} END) AS pct FROM shipments s LEFT JOIN depots d ON s.depot_id=d.id WHERE s.id>2"
    plan = plan_integer_mean(sql, "mysql", schema())
    assert plan and plan.binary_ceiling == 100 and plan.scale == 1
    assert plan.native_fractional_places == 4 + int("." in a or "." in b)
    before = sqlglot.parse_one(sql, read="mysql")
    after = sqlglot.parse_one(plan.candidate_sql, read="mysql")
    proof = sqlglot.parse_one(plan.proof_sql, read="mysql")
    case = before.find(exp.Case)
    assert after.find(exp.Sum).this == after.find(exp.Count).this == case
    assert proof.expressions[0].this == proof.expressions[1].this == case
    for tree in (after, proof):
        assert all(
            before.args.get(k) == tree.args.get(k)
            for k in set(before.args) | set(tree.args)
            if k != "expressions"
        )
    avg = before.find(exp.Avg)
    avg.replace(after.find(exp.Div).copy())
    assert before == after


@pytest.mark.parametrize(
    "expression",
    [
        "AVG(CASE WHEN active=1 THEN 100 END)",
        "AVG(CASE WHEN active=1 THEN 100 ELSE NULL END)",
        "AVG(CASE WHEN active=1 THEN 100 ELSE 1 END)",
        "AVG(CASE WHEN active=1 THEN '100' ELSE '0' END)",
        "AVG(CASE WHEN active=1 THEN 100.01 ELSE 0 END)",
        "AVG(CASE WHEN active=1 THEN 100.0000001 ELSE 0 END)",
        "AVG(CASE WHEN active=1 THEN -100 ELSE 0 END)",
        "AVG(CASE WHEN active=1 THEN 100 WHEN active=2 THEN 0 ELSE 0 END)",
        "AVG(CASE active WHEN 1 THEN 100 ELSE 0 END)",
        "AVG(CASE WHEN RAND()>0.5 THEN 100 ELSE 0 END)",
        "AVG(CASE WHEN active=1 THEN 100 ELSE 0 END)*100",
        "100*AVG(CASE WHEN active=1 THEN 100 ELSE 0 END)",
        "ROUND(AVG(CASE WHEN active=1 THEN 100 ELSE 0 END),2)",
        "AVG(DISTINCT CASE WHEN active=1 THEN 100 ELSE 0 END)",
        "AVG(CASE WHEN active=1 THEN 100 ELSE 0 END) OVER ()",
    ],
)
def test_unsupported_representation_or_reinterpretation_abstains(expression):
    assert (
        plan_integer_mean("SELECT " + expression + " FROM shipments", "mysql", schema())
        is None
    )


@pytest.mark.parametrize(
    "suffix",
    [" GROUP BY depot_id", " ORDER BY 1", " LIMIT 1", "; DELETE FROM shipments"],
)
def test_bounds_and_grouped_averages_not_broadened(suffix):
    assert (
        plan_integer_mean(
            "SELECT AVG(CASE WHEN active=1 THEN 100 ELSE 0 END) FROM shipments"
            + suffix,
            "mysql",
            schema(),
        )
        is None
    )


@pytest.mark.parametrize("dialect", ["postgres", "postgresql"])
def test_postgres_native_numeric_unchanged(dialect):
    sql = "SELECT AVG(CASE WHEN active=1 THEN 100 ELSE 0 END) FROM shipments"
    assert sqlglot.parse_one(sql, read="postgres")
    assert plan_integer_mean(sql, dialect, schema()) is None


@pytest.mark.parametrize("places,value", [(4, "33.3333"), (5, "33.33333")])
def test_independent_exact_native_and_float_proofs(places, value):
    assert accepts_hundred_indicator_result(
        [[Decimal(value)]],
        [[100 / 3]],
        [[Decimal(100), 3]],
        native_fractional_places=places,
    )
    assert not accepts_hundred_indicator_result(
        [[Decimal(value)]],
        [[100 / 3 + 0.000001]],
        [[Decimal(100), 3]],
        native_fractional_places=places,
    )
    assert not accepts_hundred_indicator_result(
        [[Decimal(value) + Decimal("0.00001")]],
        [[100 / 3]],
        [[Decimal(100), 3]],
        native_fractional_places=places,
    )


@pytest.mark.parametrize(
    "total,count",
    [
        (None, 0),
        (100, 0),
        (0, 0),
        (-100, 3),
        (400, 3),
        (1, 3),
        (True, 3),
        (100, True),
        (2**53, 100000000000000),
        (100, 2**53),
        (Decimal("100.5"), 3),
        (float("nan"), 3),
    ],
)
def test_unsafe_or_impossible_proof_fails(total, count):
    assert not accepts_hundred_indicator_result(
        [[Decimal("33.3333")]],
        [[100 / 3]],
        [[total, count]],
        native_fractional_places=4,
    )


@pytest.mark.parametrize(
    "original,candidate",
    [
        (None, 100 / 3),
        (Decimal("NaN"), 100 / 3),
        (Decimal("33.3333"), Decimal("33.3333")),
        (Decimal("33.3333"), float("nan")),
        (Decimal("33.3333"), float("inf")),
    ],
)
def test_missing_or_wrong_result_type_fails(original, candidate):
    assert not accepts_hundred_indicator_result(
        [[original]], [[candidate]], [[100, 3]], native_fractional_places=4
    )


class Manager:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def generate_response(self, **kw):
        self.requests.append(kw)
        return {"response": json.dumps(self.response), "model": "scripted"}


@pytest.mark.parametrize(
    "unit,kind,expected",
    [
        ("percentage", "quantity", True),
        ("fraction", "quantity", False),
        ("unknown", "quantity", False),
        ("percentage", "money", False),
        ("percentage", "identifier", False),
        ("percentage", "unknown", False),
    ],
)
def test_units_and_dimension_independently_gate(unit, kind, expected):
    llm = Manager(
        {
            "decision": "activate",
            "measure_kind": kind,
            "source_excerpt": "percentage of ready jobs",
            "output_units": unit,
        }
    )
    r = route_mean_precision(
        "Give percentage of ready jobs",
        "SELECT AVG(CASE WHEN ready=1 THEN 100.0 ELSE 0 END) FROM jobs",
        "mysql",
        llm,
    )
    assert r["activate"] == expected
    facts = json.loads(llm.requests[0]["prompt"])["parsed_sql_facts"]
    assert facts["existing_scale"] == 100


def service_context():
    from features.ask.engine.ask3.context import Ask3Context
    from features.ask.engine.ask3.types import ExecutionResult

    return Ask3Context(
        question="What percentage of shipments have more than two units?",
        target="unrelated_shipments",
        db_type="mysql",
        target_config={"engine": "mysql"},
        schema_info=schema(),
        sql="SELECT AVG(CASE WHEN units>2 THEN 100.0 ELSE 0 END) AS pct FROM shipments",
        execution_result=ExecutionResult(
            rows=[[Decimal("33.33333")]], columns=["pct"], row_count=1
        ),
        enforce_result_limit=False,
    )


class ServiceDecision:
    def __init__(self, kind="quantity", decision="activate"):
        self.kind, self.decision = kind, decision
        self.calls = []

    def generate_response(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "response": json.dumps(
                {
                    "decision": self.decision,
                    "measure_kind": self.kind,
                    "output_units": "percentage",
                    "source_excerpt": "percentage of shipments",
                }
            ),
            "model": "scripted",
        }


@pytest.mark.parametrize(
    "scenario,expected_status,expected_calls",
    [
        ("correct", "normalized", 2),
        ("candidate-mismatch", "reverted", 2),
        ("original-rounding-mismatch", "reverted", 2),
        ("proof-truncated", "unchanged", 1),
        ("candidate-truncated", "reverted", 2),
        ("proof-error", "unchanged", 1),
        ("candidate-error", "reverted", 2),
        ("proof-impossible-total", "unchanged", 1),
        ("proof-empty", "unchanged", 1),
        ("candidate-extra-row", "reverted", 2),
    ],
)
def test_service_proves_result_and_restores_all_failed_candidates(
    scenario, expected_status, expected_calls
):
    import asyncio
    from features.ask.service import AskService

    ctx = service_context()
    if scenario == "original-rounding-mismatch":
        ctx.execution_result.rows = [[Decimal("33.33334")]]
    original_sql = ctx.sql
    original_rows = [list(r) for r in ctx.execution_result.rows]
    calls = []

    def execute(sql, config):
        calls.append(sql)
        is_proof = len(calls) == 1
        if is_proof:
            response = {
                "success": True,
                "rows": [[Decimal(100), 3]],
                "columns": ["sum", "count"],
            }
            if scenario == "proof-impossible-total":
                response["rows"] = [[Decimal(1), 3]]
            if scenario == "proof-empty":
                response["rows"] = [[None, 0]]
        else:
            response = {"success": True, "rows": [[100 / 3]], "columns": ["pct"]}
            if scenario == "candidate-mismatch":
                response["rows"] = [[100 / 3 + 0.000001]]
            if scenario == "candidate-extra-row":
                response["rows"] *= 2
        prefix = "proof" if is_proof else "candidate"
        if scenario == prefix + "-truncated":
            response["truncated"] = True
        if scenario == prefix + "-error":
            response.update(success=False, error="fixture execution error")
        return response

    llm = ServiceDecision()
    service = AskService(llm_manager=llm, db_executor=execute)
    ctx = asyncio.run(service._apply_integer_mean_precision(ctx))
    assert ctx.integer_mean_precision["status"] == expected_status
    assert len(calls) == expected_calls
    assert len(ctx.llm_calls) == len(llm.calls) == 1
    assert ctx.integer_mean_probe_diagnostics["calls"] == expected_calls
    if expected_status == "normalized":
        assert ctx.sql != original_sql
        assert ctx.execution_result.rows == [[100 / 3]]
    else:
        assert ctx.sql == original_sql and ctx.execution_result.rows == original_rows


@pytest.mark.parametrize(
    "kind,decision", [("money", "activate"), ("quantity", "abstain")]
)
def test_semantic_abstention_never_probes(kind, decision):
    import asyncio
    from features.ask.service import AskService

    ctx = service_context()
    original = ctx.sql

    def forbidden(*args):
        raise AssertionError("No query should run after semantic abstention")

    llm = ServiceDecision(kind=kind, decision=decision)
    ctx = asyncio.run(
        AskService(
            llm_manager=llm, db_executor=forbidden
        )._apply_integer_mean_precision(ctx)
    )
    assert (
        ctx.sql == original
        and ctx.integer_mean_precision["reason"] == "model-abstained"
    )
    assert len(llm.calls) == 1


@pytest.mark.parametrize("field", ["provided_context", "conversation_context"])
def test_prior_precision_context_abstains_before_model(field):
    import asyncio
    from features.ask.service import AskService

    ctx = service_context()
    setattr(ctx, field, "Use native decimal output.")
    llm = ServiceDecision()
    ctx = asyncio.run(AskService(llm_manager=llm)._apply_integer_mean_precision(ctx))
    assert not llm.calls
    assert (
        ctx.integer_mean_precision["reason"] == "additional-context-requires-abstention"
    )
