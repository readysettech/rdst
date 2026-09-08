import json, copy
import pytest
from features.ask.annual_request import (
    decode_contract,
    matches_supported_annual_request,
    route_annual_request,
    SCHEMA,
)

QUESTION = "Which year had the highest total units for Gold accounts in the North?"
BASE = {
    "output_request": "year_only",
    "aggregation_request": "sum",
    "rank_request": "highest",
    "time_constraint": "none",
    "row_grain": "ordinary_fact_rows",
    "source_constraint": "none",
    "source_quote": "",
    "metric_definition": "ordinary",
    "metric_quote": "",
    "population_conditions": [
        {"kind": "category_equality", "quote": "Gold accounts", "value_quote": "Gold"},
        {"kind": "category_equality", "quote": "in the North", "value_quote": "North"},
    ],
}
FILTERS = [
    {"column": "a.region", "value": "North"},
    {"column": "a.tier", "value": "Gold"},
]


def test_complete_condition_bijection():
    assert matches_supported_annual_request(QUESTION, BASE, FILTERS)
    assert not matches_supported_annual_request(QUESTION, BASE, FILTERS[:1])
    assert not matches_supported_annual_request(QUESTION, BASE, FILTERS + FILTERS[:1])
    assert not matches_supported_annual_request(
        QUESTION, BASE, [{"value": "South"}, {"value": "Gold"}]
    )


@pytest.mark.parametrize(
    "key,value",
    [
        ("output_request", "other"),
        ("aggregation_request", "count"),
        ("aggregation_request", "average"),
        ("rank_request", "lowest"),
        ("rank_request", "ordinal"),
        ("time_constraint", "explicit"),
        ("row_grain", "one_per_entity"),
        ("source_constraint", "explicit"),
        ("metric_definition", "custom"),
        ("source_quote", "North"),
        ("metric_quote", "Gold"),
        ("time_constraint", "uncertain"),
        ("rank_request", True),
        ("extra", "unknown"),
    ],
)
def test_unsupported_contract_blocks_binding(key, value):
    c = copy.deepcopy(BASE)
    c[key] = value
    assert not matches_supported_annual_request(QUESTION, c, FILTERS)


@pytest.mark.parametrize(
    "change",
    [
        {"kind": "other"},
        {"kind": "uncertain"},
        {"value_quote": "gold"},
        {"quote": "made up"},
        {"quote": ""},
        {"value_quote": True},
        {"value_quote": "North"},
        {"extra": 1},
    ],
)
def test_unsupported_or_unquoted_condition_blocks_binding(change):
    c = copy.deepcopy(BASE)
    c["population_conditions"][0].update(change)
    assert not matches_supported_annual_request(QUESTION, c, FILTERS)


def test_duplicate_values_preserve_count_not_set_membership():
    q = "Gold accounts in region Gold"
    c = copy.deepcopy(BASE)
    c["population_conditions"] = [
        {"kind": "category_equality", "quote": "Gold accounts", "value_quote": "Gold"},
        {"kind": "category_equality", "quote": "region Gold", "value_quote": "Gold"},
    ]
    assert matches_supported_annual_request(
        q, c, [{"value": "Gold"}, {"value": "Gold"}]
    )
    assert not matches_supported_annual_request(q, c, [{"value": "Gold"}])


def test_only_exact_literal_value_wrapper_is_unwrapped():
    v, n = decode_contract(
        json.dumps({"properties": BASE, "additionalProperties": False})
    )
    assert v == BASE and n == "literal-properties-wrapper"
    for wrapped in [
        {"properties": BASE},
        {"properties": BASE, "additionalProperties": True},
        {"properties": BASE, "additionalProperties": False, "extra": 1},
        SCHEMA,
    ]:
        v, n = decode_contract(json.dumps(wrapped))
        assert n is None
        assert not matches_supported_annual_request(QUESTION, v, FILTERS)


def test_schema_echo_cannot_pass_as_values():
    echo = {"properties": SCHEMA["properties"], "additionalProperties": False}
    v, n = decode_contract(json.dumps(echo))
    assert n == "literal-properties-wrapper"
    assert not matches_supported_annual_request(QUESTION, v, FILTERS)


def test_route_input_excludes_sql_and_schema_and_preserves_raw_hash():
    requests = []
    callbacks = []
    raw = json.dumps({"properties": BASE, "additionalProperties": False})

    class Fixed:
        def generate_response(self, **kw):
            requests.append(kw)
            return dict(response=raw, model="scripted", tokens_used=12)

    r = route_annual_request(QUESTION, Fixed(), lambda **kw: callbacks.append(kw))
    assert set(json.loads(requests[0]["prompt"])) == {
        "effective_question",
        "trigger_catalog",
    }
    assert r["contract"] == BASE and callbacks[0]["response"] == raw
    assert r["response_normalization"] == "literal-properties-wrapper"
