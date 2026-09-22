"""Versioned Jev quick-assessment contract and local response validation."""

from __future__ import annotations

import math
from typing import Any

MODEL = "jev-1.13.0"
RUBRIC_VERSION = "query-quick-assessment-v2"

CHOICES = {
    "access_expression_risk": "Index access concern",
    "index_coverage": "Possible index gap",
    "join_growth": "Join expansion concern",
    "repeated_work": "Repeated-work concern",
    "broad_work": "Large-workload concern",
}
CHOICE_VALUES = {
    "no_evidence",
    "possible_concern",
    "strong_concern",
    "insufficient_context",
}

DESCRIPTIONS = {
    "access_expression_risk": "Expressions around filtered columns can make useful index access harder; confirm with a measured plan.",
    "index_coverage": "Important filters or joins may lack a supporting supplied index; Deep Analyze can inspect the live plan.",
    "join_growth": "The join shape may create a large intermediate result; measure row flow before changing it.",
    "repeated_work": "A correlated or repeated subquery pattern may deserve plan and runtime review.",
    "broad_work": "Sorting, aggregation, distinct work, or broad retrieval may be costly at the supplied scale.",
}


# Bit order the registry projects into its filterable finding mask. Appending
# to this tuple is safe; reordering it would reinterpret every stored mask.
FINDING_BITS = (
    "access_expression_risk",
    "index_coverage",
    "join_growth",
    "repeated_work",
    "broad_work",
)


def finding_mask(findings: Any) -> int:
    """Pack validated finding ids into the registry's filter mask."""
    index = {name: bit for bit, name in enumerate(FINDING_BITS)}
    mask = 0
    for finding in findings or []:
        bit = index.get(str(finding.get("id", "")))
        if bit is not None:
            mask |= 1 << bit
    return mask


def _finite_probability(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("probability_not_numeric")
    result = float(value)
    if not math.isfinite(result) or not 0 <= result <= 1:
        raise ValueError("probability_out_of_range")
    return result


def validate_response(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise ValueError("response_not_object")
    if body.get("model") != MODEL or body.get("rubric_version") != RUBRIC_VERSION:
        raise ValueError("response_version_mismatch")
    answers = body.get("answers")
    if not isinstance(answers, dict) or set(answers) != {*CHOICES, "priority"}:
        raise ValueError("answer_ids_invalid")

    findings: list[dict[str, Any]] = []
    insufficient = 0
    for answer_id, label in CHOICES.items():
        answer = answers.get(answer_id)
        if not isinstance(answer, dict) or answer.get("type") != "choice":
            raise ValueError("choice_answer_invalid")
        choice = answer.get("choice")
        probabilities = answer.get("probabilities")
        if choice not in CHOICE_VALUES or not isinstance(probabilities, dict):
            raise ValueError("choice_value_invalid")
        if set(probabilities) != CHOICE_VALUES:
            raise ValueError("choice_probabilities_invalid")
        for value in probabilities.values():
            _finite_probability(value)
        confidence = _finite_probability(answer.get("confidence"))
        if choice == "insufficient_context":
            insufficient += 1
        if choice in {"possible_concern", "strong_concern"}:
            findings.append(
                {
                    "id": answer_id,
                    "label": label,
                    "verdict": choice,
                    "confidence": confidence,
                    "description": DESCRIPTIONS[answer_id],
                }
            )

    priority = answers.get("priority")
    if not isinstance(priority, dict) or priority.get("type") != "score":
        raise ValueError("priority_answer_invalid")
    score = priority.get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise ValueError("priority_score_invalid")
    score = float(score)
    if not math.isfinite(score) or not 0 <= score <= 4:
        raise ValueError("priority_score_out_of_range")
    confidence = _finite_probability(priority.get("confidence"))

    # When most judgments lack evidence, ranking it Low would communicate a
    # conclusion the supplied metadata cannot support.
    limited = insufficient >= 3
    normalized_score = None if limited else round(score * 25)
    band = (
        "Limited"
        if limited
        else "High"
        if normalized_score >= 70
        else "Medium"
        if normalized_score >= 40
        else "Low"
    )
    return {
        "findings": findings,
        "priority_score": normalized_score,
        "band": band,
        "confidence": confidence,
        "raw_answers": answers,
        "usage": body.get("usage") if isinstance(body.get("usage"), dict) else {},
    }
