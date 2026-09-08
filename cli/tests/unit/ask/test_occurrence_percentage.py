from copy import deepcopy
from decimal import Decimal
import pytest
import sqlglot
from sqlglot import exp
from features.ask.occurrence_percentage import (
    plan_occurrence_percentage,
    prove_occurrence_percentage,
    accepts_occurrence_percentage,
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
SQL = "SELECT CAST(100 * SUM(CASE WHEN c.status = 'active' THEN 1 ELSE 0 END) AS DOUBLE) / COUNT(*) FROM (SELECT DISTINCT d.customer_id FROM deliveries AS d WHERE d.weight BETWEEN 5 AND 10) AS occ INNER JOIN customers AS c ON c.id = occ.customer_id"


def test_only_derived_distinct_is_removed():
    p = plan_occurrence_percentage(SQL, "mysql", SCHEMA)
    assert p
    old = sqlglot.parse_one(SQL, read="mysql")
    new = sqlglot.parse_one(p.candidate_sql, read="mysql")
    assert new.args["from_"].this.this.args.get("distinct") is None
    new.args["from_"].this.this.set(
        "distinct", old.args["from_"].this.this.args["distinct"].copy()
    )
    assert old == new
    assert p.facts["right_source"] == "customers" and len(p.facts["context_atoms"]) == 1
    assert p.proof_sql.count("COUNT(DISTINCT") == 2


def test_proof_checks_distinct_and_occurrence_counts():
    p = prove_occurrence_percentage([[50.0]], [[2, 1, 4, Decimal(3)]])
    assert p["expected_percentage"] == 75.0 and accepts_occurrence_percentage(
        p, [[75.0]]
    )
    assert not accepts_occurrence_percentage(p, [[74.999999999]])
    assert not accepts_occurrence_percentage(p, [[Decimal("75")]])


@pytest.mark.parametrize(
    "proof",
    [
        [2, 1, 2, 1],
        [2, 1, 4, 2],
        [0, 0, 4, 2],
        [2, 3, 4, 3],
        [2, 1, 4, 0],
        [2, 1, 4, 5],
        [-1, 0, 4, 1],
        [2, 1, 4, 3.0],
        [2, 1, 4, Decimal("NaN")],
        [2, 1, 4, Decimal("2.5")],
        [2, 1, 2**53 + 1, 3],
        [2, 1, 2**53, 2**53],
        [True, 1, 4, 3],
    ],
)
def test_unsafe_disagreeing_or_noop_proof_abstains(proof):
    assert prove_occurrence_percentage([[50.0]], [proof]) is None


@pytest.mark.parametrize(
    "before",
    [
        [],
        [[50]],
        [[Decimal("50")]],
        [[None]],
        [[float("inf")]],
        [[49.9]],
        [[50.0, 1]],
        [[50.0], [50.0]],
    ],
)
def test_invalid_original_abstains(before):
    assert prove_occurrence_percentage(before, [[2, 1, 4, 3]]) is None


@pytest.mark.parametrize(
    "change",
    [
        lambda s: s.replace("SELECT DISTINCT d.customer_id", "SELECT d.customer_id"),
        lambda s: s.replace("INNER JOIN", "LEFT JOIN"),
        lambda s: s.replace("INNER JOIN", "RIGHT JOIN"),
        lambda s: s.replace("COUNT(*)", "COUNT(c.id)"),
        lambda s: s.replace("THEN 1", "THEN 2"),
        lambda s: s.replace("ELSE 0", "ELSE 1"),
        lambda s: s.replace("100 *", "10 *"),
        lambda s: s.replace("AS DOUBLE", "AS DECIMAL(10,2)"),
        lambda s: s + " LIMIT 1",
        lambda s: s + " WHERE c.points > 5",
        lambda s: s.replace(
            "SELECT DISTINCT d.customer_id", "SELECT DISTINCT d.customer_id, d.weight"
        ),
        lambda s: s.replace("BETWEEN 5 AND 10", "BETWEEN 5 AND 10 OR d.weight = 20"),
        lambda s: s.replace("occ.customer_id", "occ.weight"),
        lambda s: s.replace("c.status", "d.region"),
    ],
)
def test_unsupported_shapes(change):
    assert plan_occurrence_percentage(change(SQL), "mysql", SCHEMA) is None


def test_redundant_foreign_key_nonnull_is_preserved_but_not_extra_request_scope():
    sql = SQL.replace(
        "BETWEEN 5 AND 10", "BETWEEN 5 AND 10 AND d.customer_id IS NOT NULL"
    )
    p = plan_occurrence_percentage(sql, "mysql", SCHEMA)
    assert p
    assert (
        len(p.facts["context_atoms"]) == 1
        and len(p.facts["redundant_nonnull_key_conditions"]) == 1
    )
    assert "NOT d.customer_id IS NULL" in p.candidate_sql


@pytest.mark.parametrize(
    "side,column", [("deliveries", "customer_id"), ("customers", "id")]
)
def test_noninteger_identity_rejected(side, column):
    schema = deepcopy(SCHEMA)
    schema.tables[side].columns[column].data_type = "text"
    assert plan_occurrence_percentage(SQL, "mysql", schema) is None


def test_missing_unique_key_rejected():
    schema = deepcopy(SCHEMA)
    schema.tables["customers"].columns["id"].is_primary_key = False
    assert plan_occurrence_percentage(SQL, "mysql", schema) is None


def test_postgres_unchanged():
    sql = "SELECT 100.0 * SUM(CASE WHEN c.status = 'active' THEN 1 ELSE 0 END) / COUNT(*) FROM (SELECT DISTINCT customer_id FROM deliveries) d JOIN customers c ON c.id=d.customer_id"
    sqlglot.parse_one(sql, read="postgres")
    assert plan_occurrence_percentage(sql, "postgres", SCHEMA) is None


import asyncio, json, hashlib
from features.ask.service import AskService
from features.ask.engine.ask3.context import Ask3Context
from features.ask.engine.ask3.types import ExecutionResult
from features.ask.value_probe import create_occurrence_percentage_probe_executor

QUESTION = "Among deliveries weighing between 5 and 10, what percentage of the customers are active?"
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

REQUEST.pop("denominator_table")
REQUEST.update(
    counted_entity={"table": "customers", "quote": "customers"},
    event_context={"table": "deliveries", "quote": "deliveries"},
)


class Adapter:
    def __init__(self, request=None):
        self.request = REQUEST if request is None else request
        self.calls = []

    def generate_response(self, **kwargs):
        self.calls.append(kwargs)
        assert kwargs["purpose"] == "percentage_roles_routing"
        return {
            "response": json.dumps(self.request),
            "model": "fixture",
            "tokens_used": 20,
        }


def context():
    c = Ask3Context(
        question=QUESTION,
        target="local",
        schema_info=SCHEMA,
        db_type="mysql",
        target_config={"engine": "mysql"},
        sql=SQL,
        execution_result=ExecutionResult(
            rows=[[50.0]], columns=["percentage"], row_count=1
        ),
        enforce_result_limit=False,
    )

    c.correction_intent_routing = {
        "status": "activate",
        "verdict": "activate",
        "selected_intents": ["percentage_output"],
        "selected_intent": "percentage_output",
        "activation_allowed": True,
        "shadow": False,
        "selected_sql_applied": True,
        "router_called_before_application": True,
        "effective_question_sha256": hashlib.sha256(QUESTION.encode()).hexdigest(),
        "selected_sql_sha256": hashlib.sha256(SQL.encode()).hexdigest(),
    }
    return c


@pytest.mark.parametrize(
    "candidate_rows,proof_truncated,candidate_truncated,status,expected_calls",
    [
        ([[75.0]], False, False, "normalized", 2),
        ([[50.0]], False, False, "reverted", 2),
        ([[75.0], [75.0]], False, False, "reverted", 2),
        ([[75.0]], True, False, "unchanged", 1),
        ([[75.0]], False, True, "reverted", 2),
    ],
)
def test_service_proof_candidate_and_restore(
    candidate_rows, proof_truncated, candidate_truncated, status, expected_calls
):
    calls = []

    def execute(sql, config):
        calls.append(sql)
        if len(calls) == 1:
            return {
                "success": True,
                "rows": [[2, 1, 4, Decimal(3)]],
                "columns": ["old_total", "old_hits", "total", "hits"],
                "truncated": proof_truncated,
            }
        return {
            "success": True,
            "rows": candidate_rows,
            "columns": ["percentage"],
            "truncated": candidate_truncated,
        }

    c = asyncio.run(
        AskService(
            llm_manager=Adapter(), db_executor=execute
        )._apply_occurrence_percentage(context())
    )
    assert (
        c.occurrence_percentage["status"] == status
        and len(calls) == expected_calls
        and len(c.llm_calls) == 1
    )
    assert c.occurrence_percentage_probe_diagnostics["calls"] == expected_calls
    if status == "normalized":
        assert c.sql != SQL and c.execution_result.rows == candidate_rows
    else:
        assert c.sql == SQL and c.execution_result.rows == [[50.0]]
    restored = Ask3Context.from_dict(c.to_dict())
    assert (
        restored.occurrence_percentage == c.occurrence_percentage
        and restored.occurrence_percentage_probe_diagnostics
        == c.occurrence_percentage_probe_diagnostics
    )


@pytest.mark.parametrize(
    "request_data",
    [
        REQUEST | {"multiplicity": "distinct_entities"},
        REQUEST | {"rounding_requested": True},
        REQUEST | {"include_unmatched_events": True},
        REQUEST | {"status": "ambiguous"},
    ],
)
def test_incompatible_typed_request_never_queries(request_data):
    def forbidden(*args):
        raise AssertionError("No query expected")

    c = asyncio.run(
        AskService(
            llm_manager=Adapter(request_data), db_executor=forbidden
        )._apply_occurrence_percentage(context())
    )
    assert (
        c.sql == SQL
        and c.occurrence_percentage["reason"] == "distinct-or-incompatible-request"
    )


@pytest.mark.parametrize(
    "rows", [[[2, 1, 2, 1]], [[2, 1, 4, 2]], [[0, 0, 4, 2]], [[2, 1, 4, 3.0]], []]
)
def test_invalid_proof_stops_before_candidate(rows):
    calls = []

    def execute(*args):
        calls.append(args)
        return {"success": True, "rows": rows, "columns": ["a", "b", "c", "d"]}

    c = asyncio.run(
        AskService(
            llm_manager=Adapter(), db_executor=execute
        )._apply_occurrence_percentage(context())
    )
    assert (
        c.sql == SQL
        and len(calls) == 1
        and c.occurrence_percentage["status"] == "unchanged"
    )


def test_preflight_blocks_queries(monkeypatch):
    async def reject(*args):
        return False, ["fixture"]

    monkeypatch.setattr(AskService, "_preflight_correction_candidate", reject)

    def forbidden(*args):
        raise AssertionError("No query expected")

    c = asyncio.run(
        AskService(
            llm_manager=Adapter(), db_executor=forbidden
        )._apply_occurrence_percentage(context())
    )
    assert (
        c.sql == SQL
        and c.occurrence_percentage["reason"] == "candidate-validation-failed"
    )


@pytest.mark.parametrize(
    "kind", ["context", "truncated", "error", "oversized", "missing_schema"]
)
def test_incomplete_input_abstains_before_model(kind):
    c = context()
    if kind == "context":
        c.provided_context = "A custom definition"
    elif kind == "truncated":
        c.execution_result.truncated = True
    elif kind == "error":
        c.execution_result.error = "Failure"
    elif kind == "missing_schema":
        c.schema_info = None
    else:
        c.execution_result.rows = [[50.0], [50.0]]
    a = Adapter()
    c = asyncio.run(AskService(llm_manager=a)._apply_occurrence_percentage(c))
    assert not a.calls and c.sql == SQL


def test_bounded_executor_rejects_writes_and_third_call():
    c = context()
    calls = []

    def execute(*args):
        calls.append(args)
        return {"success": True, "rows": [[1]], "columns": ["n"]}

    bounded = create_occurrence_percentage_probe_executor(c, execute)
    assert bounded("SELECT 1", c.target_config)["success"]
    assert not bounded("DELETE FROM deliveries", c.target_config)["success"]
    assert not bounded("SELECT 1", c.target_config)["success"]
    assert (
        len(calls) == 1
        and c.occurrence_percentage_probe_diagnostics["blocked_calls"] == 1
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("selected_sql_applied", False),
        ("shadow", True),
        ("effective_question_sha256", "wrong"),
        ("selected_sql_sha256", "wrong"),
        ("selected_intents", ["ratio_output"]),
    ],
)
def test_prior_unit_authorization_blocks_role_and_database(field, value):
    c = context()
    c.correction_intent_routing[field] = value
    a = Adapter()

    def forbidden(*args):
        raise AssertionError("No database call expected")

    c = asyncio.run(
        AskService(llm_manager=a, db_executor=forbidden)._apply_occurrence_percentage(c)
    )
    assert (
        not a.calls
        and c.sql == SQL
        and c.occurrence_percentage["reason"]
        == "prior-percentage-authorization-required"
    )
