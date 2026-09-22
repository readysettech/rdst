import copy

import pytest

from features.query_registry import assessment_rubric


def _response(choice="no_evidence", score=2.8):
    probabilities = {
        "no_evidence": 1.0 if choice == "no_evidence" else 0.0,
        "possible_concern": 1.0 if choice == "possible_concern" else 0.0,
        "strong_concern": 1.0 if choice == "strong_concern" else 0.0,
        "insufficient_context": 1.0 if choice == "insufficient_context" else 0.0,
    }
    answers = {
        answer_id: {
            "type": "choice",
            "choice": choice,
            "probabilities": probabilities,
            "confidence": 0.9,
        }
        for answer_id in assessment_rubric.CHOICES
    }
    answers["priority"] = {
        "type": "score",
        "score": score,
        "legend": {str(i): str(i) for i in range(5)},
        "probabilities": {str(i): 0.2 for i in range(5)},
        "confidence": 0.8,
    }
    return {
        "model": assessment_rubric.MODEL,
        "rubric_version": assessment_rubric.RUBRIC_VERSION,
        "answers": answers,
        "usage": {"input_tokens": 100},
    }


def test_score_is_derived_in_code_and_concerns_use_fixed_labels():
    body = _response(choice="strong_concern", score=2.8)
    result = assessment_rubric.validate_response(body)
    assert result["priority_score"] == 70
    assert result["band"] == "High"
    assert [finding["label"] for finding in result["findings"]] == list(
        assessment_rubric.CHOICES.values()
    )


def test_missing_evidence_is_unranked_instead_of_low():
    result = assessment_rubric.validate_response(
        _response(choice="insufficient_context", score=0)
    )
    assert result["priority_score"] is None
    assert result["band"] == "Limited"


@pytest.mark.parametrize("mutation", ["model", "answer", "probability", "score"])
def test_malformed_or_partial_response_is_rejected(mutation):
    body = _response()
    if mutation == "model":
        body["model"] = "jev-latest"
    elif mutation == "answer":
        body["answers"].pop("join_growth")
    elif mutation == "probability":
        body["answers"]["join_growth"]["probabilities"]["no_evidence"] = float("nan")
    else:
        body["answers"]["priority"]["score"] = 5
    with pytest.raises(ValueError):
        assessment_rubric.validate_response(copy.deepcopy(body))
