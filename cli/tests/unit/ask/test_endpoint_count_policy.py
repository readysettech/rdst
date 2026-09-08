from types import SimpleNamespace as S
import pytest
from features.ask.endpoint_count_policy import (
    apply_count_policy,
    can_resolve_count_policy,
)

Q = "Count undirected edges for local node 12 across all graphs, once per edge."
R = {
    "status": "clear",
    "count_unit": "undirected_relationships",
    "result_shape": "scalar_count",
    "endpoint_number": "12",
    "count_phrase": "Count undirected edges",
    "conditions": [
        {"role": "endpoint_number", "phrase": "local node 12"},
        {"role": "all_graphs", "phrase": "across all graphs"},
        {"role": "other", "phrase": "once per edge"},
    ],
}
P = {
    "same_count_unit": True,
    "verbatim_count_phrase": "Count undirected edges",
    "other_conditions": [{"index": 2, "kind": "once_per_undirected_edge"}],
}


@pytest.mark.parametrize(
    "change",
    [
        {},
        {"same_count_unit": False},
        {"verbatim_count_phrase": "invented quote"},
        {"other_conditions": []},
        {"other_conditions": [{"index": True, "kind": "once_per_undirected_edge"}]},
        {"other_conditions": [{"index": 1, "kind": "once_per_undirected_edge"}]},
        {"other_conditions": [{"index": 2, "kind": "unsupported"}]},
        {"other_conditions": [{"index": 2, "kind": "once_per_undirected_edge"}] * 2},
        {"extra": True},
    ],
)
def test_policy_requires_complete_exact_annotation(change):
    assert bool(apply_count_policy(Q, S(number="12"), R, P | change)) == (not change)


@pytest.mark.parametrize(
    "role", ["edge_property", "neighbor_property", "time", "graph_subset"]
)
def test_identified_population_restrictions_cannot_be_reclassified(role):
    r = R | {
        "conditions": R["conditions"][:2] + [{"role": role, "phrase": "once per edge"}]
    }
    assert not can_resolve_count_policy(Q, S(number="12"), r)


def test_preserves_every_fixed_field_and_condition():
    n = apply_count_policy(Q, S(number="12"), R, P)
    assert {k: v for k, v in n.items() if k not in ["count_phrase", "conditions"]} == {
        k: v for k, v in R.items() if k not in ["count_phrase", "conditions"]
    }
    assert n["conditions"] == R["conditions"][:2]
