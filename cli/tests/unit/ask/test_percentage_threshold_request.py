from decimal import Decimal
import pytest
from features.ask.percentage_threshold_request import (
    numeric_text_value,
    grounded_number,
    threshold_facts,
    accepts,
)

Q = "Count batches with defects below 0.25%."
V = {
    "column_binding": "yes",
    "requested_unit": "percentage",
    "number_text": "0.25",
    "condition_excerpt": "defects below 0.25%",
    "comparison": "lt",
    "threshold_count": 1,
    "condition_kind": "threshold",
}
F = ("defect_rate", Decimal("0.25"), "lt")


def test_valid_bound():
    assert accepts(Q, F, V)


@pytest.mark.parametrize(
    "field,value",
    [
        ("column_binding", "unknown"),
        ("requested_unit", "fraction"),
        ("comparison", "gt"),
        ("threshold_count", 2),
        ("threshold_count", True),
        ("condition_kind", "change"),
        ("number_text", "25"),
        ("number_text", "0.0025"),
        ("condition_excerpt", "invented"),
        ("extra", False),
    ],
)
def test_strict_contract(field, value):
    assert not accepts(Q, F, {**V, field: value})


@pytest.mark.parametrize(
    "text",
    [
        "-1",
        "+1",
        "1e2",
        "nan",
        "Infinity",
        "1,000",
        "1.000",
        "eight",
        "0",
        "101",
        ".5",
        "0.5%",
    ],
)
def test_unsupported_numeric_formats(text):
    assert numeric_text_value(text) is None


@pytest.mark.parametrize(
    "text,expected",
    [
        ("0.25", "0.25"),
        ("0,25", "0.25"),
        ("2", "2"),
        ("100", "100"),
        ("0.0001", "0.0001"),
    ],
)
def test_direct_decimal_comparison(text, expected):
    assert numeric_text_value(text) == Decimal(expected)


@pytest.mark.parametrize(
    "text,excerpt,question",
    [
        ("25", "25%", "below 0.25%"),
        ("0.5", "0.5%", "below -0.5%"),
        ("5", "5e-1%", "below 5e-1%"),
        ("5", "5%", "below 15%"),
    ],
)
def test_excerpt_cannot_hide_numeric_prefix_or_suffix(text, excerpt, question):
    assert not grounded_number(text, excerpt, question)


@pytest.mark.parametrize(
    "sql,op",
    [
        ("x < 2", "lt"),
        ("x <= 2", "lte"),
        ("x > 2", "gt"),
        ("x >= 2", "gte"),
        ("2 < x", "gt"),
        ("2 >= x", "lte"),
    ],
)
def test_ast_comparison_orientation(sql, op):
    assert threshold_facts("SELECT COUNT(*) FROM t WHERE " + sql, "mysql") == (
        "x",
        Decimal("2"),
        op,
    )


def test_multiple_thresholds_abstain():
    assert threshold_facts("SELECT COUNT(*) FROM t WHERE x<2 AND y>3", "mysql") is None
