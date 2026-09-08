import copy
import json
import pytest
from features.ask.membership_request import (
    qualifier_instance,
    qualifier_accepts,
    membership_accepts,
)
from features.ask.list_membership import route_list_membership as route_split_membership
from features.ask.list_membership import plan_list_membership

Q = "Count active equipment with sklils besides Brazing."
SQL = "SELECT COUNT(*) FROM equipment WHERE active=1 AND (skills IS NULL OR skills='' OR FIND_IN_SET('Brazing',skills)=0)"
PLAN = plan_list_membership(SQL, "mysql")
QUALIFIER = dict(
    output="parent_count",
    collection_binding="yes",
    value_kind="collection",
    named_member="Brazing",
    member_relation="different_from_named",
    lower_bound=1,
    upper_bound=-1,
    named_presence="optional",
    member_qualifiers=[],
    allows_missing=False,
    combination="single_requirement",
    source_excerpt="sklils besides Brazing",
    coverage="complete",
)
MEMBERSHIP = dict(
    mixed_members_match="yes",
    quantifier="any_other_member",
    requires_named_member=False,
    requests_missing_values=False,
    output_request="count_parent_records",
    value_role="collection_member",
    generated_column_matches_question="yes",
    named_member="Brazing",
    source_excerpt="skills besides Brazing",
)


@pytest.mark.parametrize(
    "changes",
    [
        {"lower_bound": 2},
        {"upper_bound": 1},
        {"lower_bound": True},
        {"member_qualifiers": ["unexpired"]},
        {"member_qualifiers": ["approved"]},
        {"member_qualifiers": ["added yesterday"]},
        {"allows_missing": True},
        {"combination": "compound"},
        {"coverage": "ambiguous"},
        {"source_excerpt": "skills besides Brazing"},
        {"named_member": "Welding"},
    ],
)
def test_qualifier_guard_cannot_be_overridden_by_approving_membership(changes):
    assert membership_accepts(Q, PLAN, MEMBERSHIP)
    assert not qualifier_accepts(Q, PLAN, {**QUALIFIER, **changes})


@pytest.mark.parametrize(
    "changes",
    [
        {"requires_named_member": True},
        {"mixed_members_match": "no"},
        {"quantifier": "excludes_named_member"},
        {"requests_missing_values": True},
        {"value_role": "scalar_text"},
        {"generated_column_matches_question": "no"},
        {"output_request": "count_collection_members"},
        {"named_member": "Welding"},
    ],
)
def test_named_presence_and_quantifier_guard_remain_mandatory(changes):
    assert qualifier_accepts(Q, PLAN, QUALIFIER)
    assert not membership_accepts(Q, PLAN, {**MEMBERSHIP, **changes})


def test_quote_and_named_presence_have_fixed_owners():
    assert MEMBERSHIP["source_excerpt"] not in Q
    assert membership_accepts(Q, PLAN, MEMBERSHIP)
    assert qualifier_accepts(Q, PLAN, {**QUALIFIER, "named_presence": "forbidden"})
    assert not qualifier_accepts(
        Q, PLAN, {**QUALIFIER, "source_excerpt": MEMBERSHIP["source_excerpt"]}
    )
    assert not membership_accepts(
        Q, PLAN, {**MEMBERSHIP, "requires_named_member": True}
    )


@pytest.mark.parametrize("title", [None, "collection_request"])
def test_exact_single_envelope_preserves_instance_values(title):
    value = {"properties": copy.deepcopy(QUALIFIER), "additionalProperties": False}
    if title is not None:
        value["title"] = title
    instance, normalization = qualifier_instance(value)
    assert instance == QUALIFIER and normalization == "single-schema-envelope"
    assert value["properties"] == QUALIFIER


@pytest.mark.parametrize(
    "value",
    [
        {"properties": QUALIFIER},
        {"properties": QUALIFIER, "additionalProperties": True},
        {"properties": QUALIFIER, "additionalProperties": False, "title": "other"},
        {"properties": QUALIFIER, "additionalProperties": False, "extra": True},
        {
            "properties": {"properties": QUALIFIER, "additionalProperties": False},
            "additionalProperties": False,
        },
        {
            "properties": {**QUALIFIER, "lower_bound": "1"},
            "additionalProperties": False,
        },
        {
            "properties": {**QUALIFIER, "lower_bound": True},
            "additionalProperties": False,
        },
        {"properties": {**QUALIFIER, "extra": True}, "additionalProperties": False},
    ],
)
def test_ambiguous_or_untyped_envelope_fails_closed(value):
    assert qualifier_instance(value) == (None, "invalid")


def test_qualifier_rejection_stops_before_second_model_call():
    class Adapter:
        def __init__(self):
            self.calls = []

        def generate_response(self, **kw):
            self.calls.append(kw)
            return {
                "response": json.dumps({**QUALIFIER, "upper_bound": 1}),
                "model": "fixture",
            }

    adapter = Adapter()
    result = route_split_membership(Q, SQL, "mysql", adapter)
    assert not result["activate"] and len(adapter.calls) == 1


def test_two_stage_activation_requires_both_complete_contracts():
    class Adapter:
        def __init__(self):
            self.calls = []

        def generate_response(self, **kw):
            self.calls.append(kw)
            value = QUALIFIER if len(self.calls) == 1 else MEMBERSHIP
            return {"response": json.dumps(value), "model": "fixture"}

    adapter = Adapter()
    result = route_split_membership(Q, SQL, "mysql", adapter)
    assert result["activate"] and len(adapter.calls) == 2
    assert [x["purpose"] for x in adapter.calls] == [
        "collection_request_routing",
        "list_membership_routing",
    ]
