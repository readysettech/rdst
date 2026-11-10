from unittest.mock import MagicMock, patch

from features.ask.ambiguity_detection import Ambiguity, AmbiguityOption, AmbiguityReport
from features.ask.engine.ask3.context import Ask3Context
from features.ask.engine.ask3.phases.clarify import clarify_question
from features.ask.engine.ask3.types import Status


def _open_question():
    return Ambiguity(
        id="details",
        category="unclear_schema_reference",
        term="details",
        reason="Several columns could be the details.",
        possible_interpretations=[],
        clarifying_question="Which columns should the order details include?",
        priority="medium",
        answer_type="value",
    )


def _strong_choice():
    return Ambiguity(
        id="status",
        category="unclear_value_reference",
        term="open",
        reason="Open could be a status.",
        possible_interpretations=[
            AmbiguityOption(id="a", text="Status is 'open'.", score=0.95),
            AmbiguityOption(id="b", text="Not yet shipped.", score=0.05),
        ],
        clarifying_question="Which orders count as open?",
        priority="medium",
    )


def _clarify(ambiguities):
    ctx = Ask3Context(question="Show open order details", target="t", no_interactive=True)
    report = AmbiguityReport(
        ambiguities=ambiguities,
        total_ambiguities=len(ambiguities),
        requires_clarification=True,
        can_proceed_with_assumptions=False,
        overall_confidence=0.6,
    )
    with patch(
        "features.ask.ambiguity_detection.detect_ambiguities",
        return_value={"success": True, "report": report},
    ):
        return clarify_question(ctx, MagicMock(), llm_manager=MagicMock())


def test_without_a_person_an_open_question_does_not_stop_the_answer():
    ctx = _clarify([_open_question()])

    assert ctx.status != Status.ERROR
    assert ctx.refined_question in (None, "")


def test_without_a_person_choices_are_gated_and_open_questions_left_out():
    ctx = _clarify([_open_question(), _strong_choice()])

    assert ctx.status != Status.ERROR
    assert "Status is 'open'." in ctx.refined_question
