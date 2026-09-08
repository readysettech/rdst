import json
import pytest
from features.ask.percentage_threshold_unit import accepts_unit
from features.ask.percentage_threshold_request import route_percentage_threshold

Q = "Count batches with defect rate below 0.5%."
UNIT = {
    "rate_unit": "percent",
    "proportion_multiplier": "0.01",
    "unit_expression": "%",
    "interpretation": "absolute_threshold",
}
NUMBER = {
    "column_binding": "yes",
    "requested_unit": "percentage",
    "number_text": "0.5",
    "condition_excerpt": "defect rate below 0.5%",
    "comparison": "lt",
    "threshold_count": 1,
    "condition_kind": "threshold",
}


@pytest.mark.parametrize(
    "field,value",
    [
        ("rate_unit", "basis_points"),
        ("rate_unit", "per_mille"),
        ("rate_unit", "unknown"),
        ("proportion_multiplier", "0.0001"),
        ("proportion_multiplier", "0.001"),
        ("proportion_multiplier", 0.01),
        ("interpretation", "change"),
        ("interpretation", "raw"),
        ("unit_expression", "percent"),
        ("unit_expression", ""),
        ("extra", False),
    ],
)
def test_unit_contract_fails_closed(field, value):
    assert not accepts_unit(Q, {**UNIT, field: value})


def test_exact_percent_unit():
    assert accepts_unit(Q, UNIT)


class Adapter:
    def __init__(self, number=NUMBER, unit=UNIT):
        self.number = number
        self.unit = unit
        self.calls = []

    def generate_response(self, **kw):
        self.calls.append(kw)
        return {
            "response": json.dumps(
                self.number
                if kw["purpose"] == "percentage_threshold_request"
                else self.unit
            ),
            "model": "fixture",
        }


def test_known_first_stage_misclassification_cannot_override_unit():
    a = Adapter(
        unit={**UNIT, "rate_unit": "basis_points", "proportion_multiplier": "0.0001"}
    )
    r = route_percentage_threshold(
        Q, "SELECT COUNT(*) FROM batches WHERE defect_rate<0.5", "mysql", a
    )
    assert not r["activate"] and len(a.calls) == 2


def test_number_mismatch_stops_before_unit_call():
    a = Adapter()
    r = route_percentage_threshold(
        Q, "SELECT COUNT(*) FROM batches WHERE defect_rate<0.005", "mysql", a
    )
    assert not r["activate"] and len(a.calls) == 1


def test_two_contracts_required():
    a = Adapter()
    r = route_percentage_threshold(
        Q, "SELECT COUNT(*) FROM batches WHERE defect_rate<0.5", "mysql", a
    )
    assert r["activate"] and len(a.calls) == 2
    assert all("generated_sql" not in json.loads(c["prompt"]) for c in a.calls)
