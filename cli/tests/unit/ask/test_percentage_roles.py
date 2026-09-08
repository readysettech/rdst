from copy import deepcopy
import json
import pytest
from features.ask.percentage_roles import (
    accepts_percentage_roles,
    route_percentage_roles,
)
from features.ask.occurrence_percentage import plan_occurrence_percentage
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
Q = "Among deliveries weighing between 5 and 10, what percentage of the customers are active?"

PLAN = plan_occurrence_percentage(SQL, "mysql", SCHEMA)
REQUEST = {
    "status": "clear",
    "units": "percentage",
    "counted_entity": {"table": "customers", "quote": "customers"},
    "event_context": {"table": "deliveries", "quote": "deliveries"},
    "numerator_condition": PLAN.facts["membership_atom"],
    "context_conditions": PLAN.facts["context_atoms"],
    "denominator_conditions": [],
    "multiplicity": "joined_occurrences",
    "include_unmatched_events": False,
    "include_unjoined_entities": False,
    "rounding_requested": False,
}


def test_roles_convert_only_to_existing_semantic_request():
    request = accepts_percentage_roles(Q, PLAN, REQUEST)
    assert request is not None and request["denominator_table"] == "customers"
    assert all(
        request[k] == v
        for k, v in REQUEST.items()
        if k not in ("counted_entity", "event_context")
    )


@pytest.mark.parametrize("role", ["counted_entity", "event_context"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("table", "wrong_table"),
        ("table", ""),
        ("table", None),
        ("quote", "not present in this question"),
        ("quote", ""),
        ("quote", "x"),
        ("quote", None),
    ],
)
def test_missing_wrong_or_unquoted_roles_abstain(role, field, value):
    request = deepcopy(REQUEST)
    request[role][field] = value
    assert accepts_percentage_roles(Q, PLAN, request) is None


def test_event_count_does_not_become_entity_count():
    request = deepcopy(REQUEST)
    request["counted_entity"] = deepcopy(request["event_context"])
    assert accepts_percentage_roles(Q, PLAN, request) is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "unsupported"),
        ("units", "fraction"),
        ("multiplicity", "distinct_entities"),
        ("multiplicity", "unknown"),
        ("include_unmatched_events", True),
        ("include_unjoined_entities", True),
        ("rounding_requested", True),
        ("include_unmatched_events", 0),
        ("context_conditions", []),
        ("context_conditions", None),
        ("denominator_conditions", [PLAN.facts["membership_atom"]]),
        (
            "numerator_condition",
            {
                "table": "customers",
                "column": "points",
                "operator": "eq",
                "values": [{"kind": "number", "value": "0"}],
            },
        ),
    ],
)
def test_existing_scope_units_and_grain_guards_remain_mandatory(field, value):
    request = deepcopy(REQUEST)
    request[field] = value
    assert accepts_percentage_roles(Q, PLAN, request) is None


@pytest.mark.parametrize("field", list(REQUEST))
def test_incomplete_typed_response_abstains(field):
    request = deepcopy(REQUEST)
    del request[field]
    assert accepts_percentage_roles(Q, PLAN, request) is None


def test_role_object_extra_fields_abstain():
    request = deepcopy(REQUEST)
    request["counted_entity"]["approved"] = True
    assert accepts_percentage_roles(Q, PLAN, request) is None


class Adapter:
    def generate_response(self, **kwargs):
        self.kw = kwargs
        return {"response": json.dumps(REQUEST), "model": "scripted", "tokens_used": 1}


def test_model_receives_question_schema_and_contract_only():
    a = Adapter()
    callbacks = []
    result = route_percentage_roles(
        Q, PLAN, a, lambda **kwargs: callbacks.append(kwargs)
    )
    assert result["apply"]
    prompt = json.loads(a.kw["prompt"])
    assert set(prompt) == {
        "effective_question",
        "complete_structural_schema",
        "trigger_catalog",
    }
    assert (
        prompt["complete_structural_schema"] == PLAN.facts["complete_structural_schema"]
    )
    assert "candidate_sql" not in prompt and "generated_sql" not in prompt
    assert len(callbacks) == 1 and callbacks[0]["phase"] == "percentage_roles_routing"
