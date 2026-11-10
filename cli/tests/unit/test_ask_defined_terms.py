"""Tests for quoting provided-context definitions in SQL validation repairs."""

import json

from features.ask.defined_terms import MAX_DEFINITIONS, defined_term_feedback
from features.ask.engine.ask3.types import ValidationError

# The shape of a BIRD-Interact-Lite knowledge base in the provided context.
KNOWLEDGE_CONTEXT = "Domain knowledge for this database:\n" + json.dumps(
    [
        {
            "id": 8,
            "knowledge": "Wireless Performance Rating (WPR)",
            "description": "Rates the quality of wireless connectivity.",
            "definition": "WPR = \\frac{WlRangeM}{10} \\times \\frac{WlSignal + 100}{100}",
        },
        {
            "id": 18,
            "knowledge": "Premium Wireless Solution",
            "description": "Defines high-end wireless gaming devices.",
            "definition": "A device with WPR > 9.0 and BattLifeH > 24.",
            "children_knowledge": [8],
        },
        {
            "id": 0,
            "knowledge": "Signal-to-Noise Quality Indicator",
            "description": "Combines SNR and noise floor.",
            "definition": "$\\text{SNQI} = \\text{SnrRatio} - 0.1 \\times |\\text{NoiseFloorDbm}|$",
        },
    ],
    indent=2,
)
WPR = (
    "Wireless Performance Rating (WPR): "
    "WPR = \\frac{WlRangeM}{10} \\times \\frac{WlSignal + 100}{100}"
)


def _missing(column="", message="Column not found"):
    return ValidationError(column=column, table_alias=None, message=message)


def test_unknown_column_named_after_a_defined_metric_gets_its_definition():
    feedback = defined_term_feedback([_missing("wpr")], KNOWLEDGE_CONTEXT)

    assert "not columns" in feedback
    assert feedback.endswith(f"\n- {WPR}")
    assert "Premium Wireless Solution" not in feedback


def test_resolution_messages_and_formula_names_match_ignoring_case():
    feedback = defined_term_feedback(
        [
            _missing(message="Column resolution failed: Unknown column: snqi"),
            _missing(
                message="Column 'd.Wireless_Performance_Rating' could not be resolved"
            ),
        ],
        KNOWLEDGE_CONTEXT,
    )

    assert "- Signal-to-Noise Quality Indicator: $\\text{SNQI} = " in feedback
    assert f"- {WPR}" in feedback


def test_free_text_definitions_are_quoted_by_line():
    context = "Metrics:\n- Churn Rate = lost_customers / start_customers\n- Notes: none"

    feedback = defined_term_feedback([_missing("churn rate")], context)
    assert feedback.endswith("\n- Churn Rate = lost_customers / start_customers")
    assert defined_term_feedback([_missing("churn")], context) == ""


def test_names_without_a_definition_add_nothing():
    assert defined_term_feedback([_missing("battlifeh")], KNOWLEDGE_CONTEXT) == ""
    assert (
        defined_term_feedback(
            [_missing("wpr", message="Value 'x' for wpr is not in the question")],
            KNOWLEDGE_CONTEXT,
        )
        == ""
    )
    assert defined_term_feedback([_missing("wpr")], "") == ""


def test_quoted_definitions_are_bounded():
    context = "\n".join(f"M{i} = a + {i}" for i in range(MAX_DEFINITIONS + 3))
    errors = [_missing(f"m{i}") for i in range(MAX_DEFINITIONS + 3)]

    assert defined_term_feedback(errors, context).count("\n- ") == MAX_DEFINITIONS


def test_a_definition_in_the_users_answer_is_quoted():
    # Ask passes the user's answers, one per line, after the provided context.
    answers = "- WPR: WPR = wlrangem / 10 * wlsignal"

    feedback = defined_term_feedback([_missing("wpr")], answers)

    assert "WPR = wlrangem / 10 * wlsignal" in feedback
    assert "the user's answers define" in feedback
