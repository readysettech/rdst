from copy import deepcopy
import pytest
from features.ask import endpoint_component as m

GOOD = dict(
    decision="activate",
    count_unit="undirected_edges",
    endpoint_reference="local_numeric_component",
    requested_number="42",
    all_graphs=True,
    no_unrepresented_conditions=True,
    question_quote="Count the undirected links to local node 42 in all graphs.",
)


def mirror(v):
    return dict(additionalProperties=False, properties=deepcopy(v), **deepcopy(v))


@pytest.mark.parametrize(
    "v",
    [
        GOOD,
        dict(GOOD, decision="abstain"),
        dict(GOOD, count_unit="directed_rows"),
        dict(GOOD, all_graphs=False),
        dict(GOOD, no_unrepresented_conditions=False),
        dict(GOOD, requested_number=None),
        dict(GOOD, endpoint_reference="complete_literal_identifier"),
    ],
)
def test_identical_complete_values_only(v):
    outer = mirror(v)
    before = deepcopy(outer)
    assert m._endpoint_response_values(outer) == v and outer == before


@pytest.mark.parametrize("field", list(GOOD))
@pytest.mark.parametrize("side", ["outer", "inner"])
def test_partial_copy_is_rejected(field, side):
    v = mirror(GOOD)
    target = v if side == "outer" else v["properties"]
    target.pop(field)
    assert m._endpoint_response_values(v) is v


@pytest.mark.parametrize("field", list(GOOD))
@pytest.mark.parametrize("side", ["outer", "inner"])
def test_conflicting_copy_is_rejected(field, side):
    v = mirror(GOOD)
    target = v if side == "outer" else v["properties"]
    target[field] = False if type(target[field]) is bool else "conflicting"
    assert m._endpoint_response_values(v) is v


@pytest.mark.parametrize("field", ["all_graphs", "no_unrepresented_conditions"])
@pytest.mark.parametrize("side", ["outer", "inner"])
def test_python_boolean_integer_equality_is_not_enough(field, side):
    v = mirror(GOOD)
    target = v if side == "outer" else v["properties"]
    target[field] = 1
    assert m._endpoint_response_values(v) is v


@pytest.mark.parametrize(
    "key,value",
    [
        ("type", "object"),
        ("required", list(GOOD)),
        ("rationale", "ignore constraints"),
        ("extra", False),
    ],
)
@pytest.mark.parametrize("side", ["outer", "inner"])
def test_extra_members_are_not_dropped(key, value, side):
    v = mirror(GOOD)
    target = v if side == "outer" else v["properties"]
    target[key] = value
    assert m._endpoint_response_values(v) is v


@pytest.mark.parametrize("value", [True, None, 0, "false"])
def test_metadata_type_must_be_exact(value):
    v = mirror(GOOD)
    v["additionalProperties"] = value
    assert m._endpoint_response_values(v) is v


def test_schema_descriptors_are_not_decisions():
    v = mirror({k: {"type": "string"} for k in GOOD})
    assert m._endpoint_response_values(v) is v


def test_recursive_envelopes_are_rejected():
    v = mirror(GOOD)
    v["properties"] = mirror(GOOD)
    assert m._endpoint_response_values(v) is v
