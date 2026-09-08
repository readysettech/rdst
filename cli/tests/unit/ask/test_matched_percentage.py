import copy, json
from decimal import Decimal
import pytest, sqlglot
from sqlglot import exp
from features.ask.matched_percentage import (
    plan_matched_percentage,
    prove_matched_percentage,
    accepts_matched_percentage,
    route_matched_percentage,
    accepts_request,
)
from features.ask.engine.ask3.types import SchemaInfo, TableInfo, ColumnInfo

SCHEMA = SchemaInfo(
    "unrelated",
    "mysql",
    {
        "deliveries": TableInfo(
            "deliveries",
            {
                n: ColumnInfo(n, t, is_primary_key=n == "id")
                for n, t in {
                    "id": "int",
                    "customer_id": "int",
                    "weight": "int",
                    "region": "text",
                }.items()
            },
        ),
        "customers": TableInfo(
            "customers",
            {
                n: ColumnInfo(n, t, is_primary_key=n == "id")
                for n, t in {"id": "int", "status": "text", "points": "int"}.items()
            },
        ),
    },
)
SQL = "SELECT AVG(CASE WHEN c.status = 'active' THEN 100.0 ELSE 0 END) FROM deliveries d LEFT JOIN customers c ON d.customer_id=c.id WHERE d.weight BETWEEN 5 AND 10"


def test_only_scalar_expression_changes_and_primary_key_denominator():
    p = plan_matched_percentage(SQL, "mysql", SCHEMA)
    assert p
    before = sqlglot.parse_one(SQL, read="mysql")
    after = sqlglot.parse_one(p.candidate_sql, read="mysql")
    after.set("expressions", before.expressions)
    assert before == after
    tree = sqlglot.parse_one(p.candidate_sql, read="mysql")
    denom = tree.find(exp.Nullif).this
    assert (
        isinstance(denom, exp.Count)
        and denom.this.table == "c"
        and denom.this.name == "id"
    )
    proof = sqlglot.parse_one(p.proof_sql, read="mysql")
    assert len(proof.expressions) == 3


@pytest.mark.parametrize(
    "sql",
    [
        SQL.replace("LEFT JOIN", "JOIN"),
        SQL.replace("LEFT JOIN", "RIGHT JOIN"),
        SQL.replace("LEFT JOIN", "CROSS JOIN"),
        SQL.replace("AVG(", "SUM("),
        SQL.replace("THEN 100.0", "THEN 1"),
        SQL.replace("ELSE 0", "ELSE NULL"),
        SQL.replace("THEN 100.0 ELSE 0", "THEN 0 ELSE 100"),
        SQL.replace("c.status = 'active'", "c.status IS NULL"),
        SQL.replace("c.status = 'active'", "d.region = 'active'"),
        SQL.replace("c.id WHERE", "c.points WHERE"),
        SQL.replace("d.customer_id=c.id", "d.customer_id=c.id AND c.points>0"),
        SQL + " GROUP BY d.id",
        SQL + " LIMIT 1",
        SQL.replace("SELECT AVG", "SELECT DISTINCT AVG"),
        SQL + "; DELETE FROM deliveries",
        SQL.replace("d.weight BETWEEN 5 AND 10", "c.points BETWEEN 5 AND 10"),
        SQL.replace("d.weight BETWEEN 5 AND 10", "RAND()>0.5"),
        SQL.replace("THEN 100.0", "THEN 1e2"),
        SQL.replace("c.status", "c.missing"),
    ],
)
def test_unsupported_or_different_measure_abstains(sql):
    assert plan_matched_percentage(sql, "mysql", SCHEMA) is None


def test_postgres_abstains_and_unchanged_sql_is_parseable():
    assert plan_matched_percentage(SQL, "postgresql", SCHEMA) is None
    assert sqlglot.parse_one(SQL, read="postgres")


@pytest.mark.parametrize("kind", ["no-primary", "composite"])
def test_right_single_primary_key_required(kind):
    schema = copy.deepcopy(SCHEMA)
    if kind == "no-primary":
        schema.tables["customers"].columns["id"].is_primary_key = False
    else:
        schema.tables["customers"].columns["points"].is_primary_key = True
    assert plan_matched_percentage(SQL, "mysql", schema) is None


@pytest.mark.parametrize(
    "before,stats,expected",
    [
        (Decimal("40.00000"), [5, 4, 2], 50.0),
        (Decimal("1.29590"), [1389, 1357, 18], float(18) * 100 / 1357),
        (Decimal("0.0000"), [5, 4, 0], 0.0),
        (Decimal("40.00000"), [5, 5, 2], None),
        (Decimal("40.00000"), [5, 0, 2], None),
        (Decimal("40.00000"), [5, 1, 2], None),
        (Decimal("41.00000"), [5, 4, 2], None),
        (Decimal("40.00000"), [5, 4, Decimal("2.5")], None),
        (Decimal("40.00000"), [True, 4, 2], None),
        (Decimal("nan"), [5, 4, 2], None),
        (40.0, [5, 4, 2], None),
    ],
)
def test_original_average_and_complete_count_proof(before, stats, expected):
    p = prove_matched_percentage([[before]], [stats])
    assert (p["expected_percentage"] if p else None) == expected
    if expected is not None:
        assert accepts_matched_percentage(p, [[expected]])
        assert not accepts_matched_percentage(p, [[expected + 0.1]])
        assert not accepts_matched_percentage(p, [[float("nan")]])


REQUEST = {
    "status": "clear",
    "units": "percentage",
    "denominator_table": "customers",
    "numerator_condition": {
        "table": "customers",
        "column": "status",
        "operator": "eq",
        "values": [{"kind": "string", "value": "active"}],
    },
    "context_conditions": [
        {
            "table": "deliveries",
            "column": "weight",
            "operator": "between",
            "values": [
                {"kind": "number", "value": "5"},
                {"kind": "number", "value": "10"},
            ],
        }
    ],
    "denominator_conditions": [],
    "multiplicity": "joined_occurrences",
    "include_unmatched_events": False,
    "include_unjoined_entities": False,
    "rounding_requested": False,
}


def test_complete_independent_contract_matches():
    p = plan_matched_percentage(SQL, "mysql", SCHEMA)
    assert accepts_request(p, REQUEST)
    equivalent = copy.deepcopy(REQUEST)
    equivalent["context_conditions"][0]["values"][0]["value"] = "5.00"
    equivalent["denominator_table"] = "CUSTOMERS"
    assert accepts_request(p, equivalent)


@pytest.mark.parametrize(
    "key,value",
    [
        ("status", "ambiguous"),
        ("units", "fraction"),
        ("denominator_table", "deliveries"),
        ("multiplicity", "distinct_entities"),
        ("multiplicity", "unknown"),
        ("include_unmatched_events", True),
        ("include_unjoined_entities", True),
        ("rounding_requested", True),
        ("denominator_conditions", [REQUEST["numerator_condition"]]),
        ("context_conditions", []),
        ("context_conditions", None),
        ("numerator_condition", {}),
    ],
)
def test_contract_restrictions_cannot_be_overruled_by_a_match_boolean(key, value):
    p = plan_matched_percentage(SQL, "mysql", SCHEMA)
    r = copy.deepcopy(REQUEST)
    r[key] = value
    r["numerator_matches"] = True
    assert not accepts_request(p, r)


@pytest.mark.parametrize(
    "change",
    [
        {"table": "deliveries"},
        {"column": "points"},
        {"operator": "neq"},
        {"operator": "unknown"},
        {"operator": "is_null", "values": []},
        {"values": [{"kind": "number", "value": "0"}]},
        {"values": [{"kind": "string", "value": "inactive"}]},
        {"values": []},
    ],
)
def test_numerator_must_match_physical_column_operator_and_value(change):
    p = plan_matched_percentage(SQL, "mysql", SCHEMA)
    r = copy.deepcopy(REQUEST)
    r["numerator_condition"].update(change)
    assert not accepts_request(p, r)


@pytest.mark.parametrize(
    "kind",
    ["wrong-value", "wrong-type", "extra", "missing", "unknown-op", "different-column"],
)
def test_context_filters_are_a_complete_multiset(kind):
    p = plan_matched_percentage(SQL, "mysql", SCHEMA)
    r = copy.deepcopy(REQUEST)
    c = r["context_conditions"][0]
    if kind == "wrong-value":
        c["values"][0]["value"] = "6"
    elif kind == "wrong-type":
        c["values"][0]["kind"] = "string"
    elif kind == "extra":
        r["context_conditions"].append(copy.deepcopy(c))
    elif kind == "missing":
        r["context_conditions"] = []
    elif kind == "unknown-op":
        c["operator"] = "unknown"
    else:
        c["column"] = "id"
    assert not accepts_request(p, r)


def test_disjunctive_context_is_not_misrepresented_as_conjunction():
    sql = SQL.replace("d.weight BETWEEN 5 AND 10", "d.weight=5 OR d.weight=10")
    assert plan_matched_percentage(sql, "mysql", SCHEMA) is None


class Adapter:
    def __init__(self, request):
        self.request = request
        self.calls = []

    def generate_response(self, **kwargs):
        self.calls.append(kwargs)
        return {"response": json.dumps(self.request)}


def test_model_receives_only_question_schema_and_contract():
    q = "Among deliveries weighing 5 to 10, what percentage of customers are active?"
    a = Adapter(REQUEST)
    p = plan_matched_percentage(SQL, "mysql", SCHEMA)
    r = route_matched_percentage(q, p, a)
    assert r["apply"] and len(a.calls) == 1
    payload = json.loads(a.calls[0]["prompt"])
    assert set(payload) == {
        "effective_question",
        "complete_structural_schema",
        "trigger_catalog",
    }
    assert (
        a.calls[0]["max_tokens"] == 1600
        and a.calls[0]["purpose"] == "matched_percentage_request_routing"
    )


import asyncio
from features.ask.service import AskService
from features.ask.engine.ask3.context import Ask3Context
from features.ask.engine.ask3.types import ExecutionResult
from features.ask.value_probe import create_matched_percentage_probe_executor


def context():
    return Ask3Context(
        question="Among deliveries weighing 5 to 10, what percentage of customers are active?",
        target="local",
        db_type="mysql",
        target_config={"engine": "mysql"},
        schema_info=SCHEMA,
        sql=SQL,
        execution_result=ExecutionResult(
            rows=[[Decimal("40.00000")]], columns=["percentage"], row_count=1
        ),
        enforce_result_limit=False,
    )


@pytest.mark.parametrize(
    "candidate,stats_truncated,candidate_truncated,status,call_count",
    [
        (50.0, False, False, "normalized", 2),
        (40.0, False, False, "reverted", 2),
        (50.0, True, False, "unchanged", 1),
        (50.0, False, True, "reverted", 2),
    ],
)
def test_service_statistics_proof_and_restoration(
    candidate, stats_truncated, candidate_truncated, status, call_count
):
    calls = []

    def execute(sql, config):
        calls.append(sql)
        if len(calls) == 1:
            return {
                "success": True,
                "rows": [[5, 4, 2]],
                "columns": ["total", "matched", "hits"],
                "truncated": stats_truncated,
            }
        return {
            "success": True,
            "rows": [[candidate]],
            "columns": ["percentage"],
            "truncated": candidate_truncated,
        }

    c = asyncio.run(
        AskService(
            llm_manager=Adapter(REQUEST), db_executor=execute
        )._apply_matched_percentage(context())
    )
    assert c.matched_percentage["status"] == status and len(calls) == call_count
    assert (
        len(c.llm_calls) == 1
        and c.matched_percentage_probe_diagnostics["calls"] == call_count
    )
    if status == "normalized":
        assert c.sql != SQL and c.execution_result.rows == [[candidate]]
    else:
        assert c.sql == SQL and c.execution_result.rows == [[Decimal("40.00000")]]
    restored = Ask3Context.from_dict(c.to_dict())
    assert restored.matched_percentage == c.matched_percentage
    assert (
        restored.matched_percentage_probe_diagnostics
        == c.matched_percentage_probe_diagnostics
    )


@pytest.mark.parametrize(
    "stats",
    [
        [],
        [[5, 5, 2]],
        [[5, 1, 2]],
        [[5, 0, 2]],
        [[5, 4, 3]],
        [[5, 4, Decimal("2.5")]],
    ],
)
def test_inconsistent_or_unnecessary_statistics_never_execute_candidate(stats):
    calls = []

    def execute(sql, config):
        calls.append(sql)
        return {"success": True, "rows": stats, "columns": ["total", "matched", "hits"]}

    c = asyncio.run(
        AskService(
            llm_manager=Adapter(REQUEST), db_executor=execute
        )._apply_matched_percentage(context())
    )
    assert (
        len(calls) == 1
        and c.sql == SQL
        and c.matched_percentage["reason"] == "unmatched-population-not-proven"
    )


def test_host_contract_rejection_prevents_all_queries():
    request = copy.deepcopy(REQUEST)
    request["numerator_condition"]["operator"] = "is_null"
    request["numerator_condition"]["values"] = []

    def forbidden(*args):
        raise AssertionError("No query expected")

    c = asyncio.run(
        AskService(
            llm_manager=Adapter(request), db_executor=forbidden
        )._apply_matched_percentage(context())
    )
    assert (
        c.sql == SQL
        and c.matched_percentage["reason"]
        == "no-unambiguous-matched-entity-denominator"
    )


def test_candidate_validation_failure_prevents_probe(monkeypatch):
    async def reject(self, ctx, candidate):
        return False, ["fixture rejection"]

    monkeypatch.setattr(AskService, "_preflight_correction_candidate", reject)

    def forbidden(*args):
        raise AssertionError("No query expected")

    c = asyncio.run(
        AskService(
            llm_manager=Adapter(REQUEST), db_executor=forbidden
        )._apply_matched_percentage(context())
    )
    assert (
        c.sql == SQL and c.matched_percentage["reason"] == "candidate-validation-failed"
    )


def test_probe_budget_is_readonly_and_rejects_raw_truncation():
    c = context()
    calls = []

    def execute(*args):
        calls.append(args)
        return {"success": True, "rows": [[5, 4, 2]], "truncated": True}

    bounded = create_matched_percentage_probe_executor(c, execute)
    assert not bounded("SELECT COUNT(*) FROM deliveries", c.target_config)["success"]
    assert not bounded("DELETE FROM deliveries", c.target_config)["success"]
    assert not bounded("SELECT COUNT(*) FROM deliveries", c.target_config)["success"]
    assert (
        len(calls) == 1 and c.matched_percentage_probe_diagnostics["blocked_calls"] == 1
    )
