from features.ask import endpoint_component as m
from copy import deepcopy
import pytest

GOOD = {
    "decision": "activate",
    "count_unit": "undirected_edges",
    "endpoint_reference": "local_numeric_component",
    "requested_number": "12",
    "all_graphs": True,
    "no_unrepresented_conditions": True,
    "question_quote": "Count the links",
}


def envelope(value):
    return {"additionalProperties": False, "properties": value}


def test_exact_values_unwrapped_without_mutation():
    v = envelope(deepcopy(GOOD))
    before = deepcopy(v)
    assert m._endpoint_response_values(v) == GOOD and v == before
    assert m._endpoint_response_values(GOOD) is GOOD


@pytest.mark.parametrize("field", list(GOOD))
def test_missing_fields_rejected(field):
    v = deepcopy(GOOD)
    v.pop(field)
    outer = envelope(v)
    assert m._endpoint_response_values(outer) is outer


@pytest.mark.parametrize(
    "field,bad",
    [
        ("decision", "accept"),
        ("count_unit", "edges"),
        ("endpoint_reference", "local"),
        ("requested_number", 12),
        ("all_graphs", 1),
        ("all_graphs", "true"),
        ("no_unrepresented_conditions", 0),
        ("question_quote", {"type": "string"}),
        ("count_unit", None),
    ],
)
def test_types_and_enums_not_coerced(field, bad):
    v = deepcopy(GOOD)
    v[field] = bad
    outer = envelope(v)
    assert m._endpoint_response_values(outer) is outer


@pytest.mark.parametrize(
    "extra", [{"required": list(GOOD)}, {"decision": "abstain"}, {"type": "object"}]
)
def test_other_envelope_members_not_ignored(extra):
    outer = envelope(deepcopy(GOOD))
    outer.update(extra)
    assert m._endpoint_response_values(outer) is outer


@pytest.mark.parametrize("value", [True, None, 0, "false"])
def test_additionalproperties_exact_false_only(value):
    outer = envelope(deepcopy(GOOD))
    outer["additionalProperties"] = value
    assert m._endpoint_response_values(outer) is outer


@pytest.mark.parametrize(
    "field,value",
    [
        ("decision", "abstain"),
        ("count_unit", "directed_rows"),
        ("endpoint_reference", "complete_literal_identifier"),
        ("requested_number", None),
        ("all_graphs", False),
        ("no_unrepresented_conditions", False),
    ],
)
def test_negative_semantics_preserved(field, value):
    v = deepcopy(GOOD)
    v[field] = value
    assert m._endpoint_response_values(envelope(v)) == v
