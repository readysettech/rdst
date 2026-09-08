import pytest
from features.ask.request_entity import accepts

Q = "Acme means Acme Supply. Count Acme orders."
GOOD = dict(
    polarity="positive",
    scope="single_named_entity",
    name_source="explicit_in_question",
    expanded_name="Acme Supply",
    reference_excerpt=Q,
)


def test_exact_independent_expansion():
    assert accepts(Q, "Acme", "Acme Supply", GOOD)


@pytest.mark.parametrize(
    "v",
    [
        None,
        {},
        [],
        {**GOOD, "extra": 1},
        {**GOOD, "polarity": "excluded"},
        {**GOOD, "polarity": "unknown"},
        {**GOOD, "polarity": "mentioned_only"},
        {**GOOD, "scope": "entity_with_related_members"},
        {**GOOD, "scope": "literal_spelling"},
        {**GOOD, "scope": "category_or_prefix"},
        {**GOOD, "name_source": "unknown"},
        {**GOOD, "expanded_name": "Acme Logistics"},
        {**GOOD, "reference_excerpt": "Count Acme Supply orders."},
        {**GOOD, "reference_excerpt": ""},
        {**GOOD, "polarity": True},
        {**GOOD, "reference_excerpt": "Count"},
    ],
)
def test_malformed_ambiguous_or_mismatched(v):
    assert not accepts(Q, "Acme", "Acme Supply", v)


def test_explicit_full_name_must_occur_verbatim():
    assert not accepts(
        "Count Acme orders.",
        "Acme",
        "Acme Supply",
        {**GOOD, "reference_excerpt": "Count Acme orders."},
    )
