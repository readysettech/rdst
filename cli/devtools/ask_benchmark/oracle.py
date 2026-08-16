from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterable
from datetime import date, datetime, time
from decimal import Decimal
from math import isfinite
from typing import Any

from .models import OracleScore, QueryResult

GOLD_FINGERPRINT_CODEC = "typed-exact-multiset-v1"
UNSTABLE_GOLD_CASE_IDS = (671, 1028, 1482)


def result_fingerprint(result: QueryResult) -> str:
    """Hash exact typed rows for cross-run gold provenance checks."""
    if not result.succeeded:
        raise ValueError("Only successful query results can be fingerprinted")
    rows = sorted(
        json.dumps(
            [_fingerprint_value(value) for value in row],
            sort_keys=True,
            separators=(",", ":"),
        )
        for row in result.rows
    )
    payload = json.dumps(
        {"codec": GOLD_FINGERPRINT_CODEC, "rows": rows},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def score_results(candidate: QueryResult, gold: QueryResult) -> OracleScore:
    if not gold.succeeded:
        raise ValueError("Gold result must succeed before scoring")

    candidate_rows = _freeze_rows(candidate.rows) if candidate.succeeded else ()
    gold_rows = _freeze_rows(gold.rows)
    execution_correct = candidate.succeeded and set(candidate_rows) == set(gold_rows)
    soft_f1 = bird_soft_f1(candidate_rows, gold_rows) if candidate.succeeded else 0.0
    return OracleScore(
        execution_correct=execution_correct,
        soft_f1=soft_f1,
        multiset_correct=candidate.succeeded
        and Counter(candidate_rows) == Counter(gold_rows),
        ordered_correct=candidate.succeeded and candidate_rows == gold_rows,
        candidate_row_count=len(candidate.rows) if candidate.succeeded else 0,
        gold_row_count=len(gold.rows),
    )


def bird_execution_correct(
    predicted: Iterable[Iterable[Any]], ground_truth: Iterable[Iterable[Any]]
) -> bool:
    return set(_freeze_rows(predicted)) == set(_freeze_rows(ground_truth))


def bird_soft_f1(
    predicted: Iterable[Iterable[Any]], ground_truth: Iterable[Iterable[Any]]
) -> float:
    predicted_rows = list(dict.fromkeys(_freeze_rows(predicted)))
    truth_rows = list(dict.fromkeys(_freeze_rows(ground_truth)))
    if not predicted_rows and not truth_rows:
        return 1.0

    match_scores: list[float] = []
    predicted_only_scores: list[float] = []
    truth_only_scores: list[float] = []
    for index, truth_row in enumerate(truth_rows):
        if index >= len(predicted_rows):
            match_scores.append(0.0)
            truth_only_scores.append(1.0)
            continue
        matched, predicted_only, truth_only = _row_match(
            predicted_rows[index], truth_row
        )
        match_scores.append(matched)
        predicted_only_scores.append(predicted_only)
        truth_only_scores.append(truth_only)

    for _ in range(len(predicted_rows) - len(truth_rows)):
        match_scores.append(0.0)
        predicted_only_scores.append(1.0)
        truth_only_scores.append(0.0)

    true_positive = sum(match_scores)
    false_positive = sum(predicted_only_scores)
    false_negative = sum(truth_only_scores)
    precision_denominator = true_positive + false_positive
    recall_denominator = true_positive + false_negative
    precision = true_positive / precision_denominator if precision_denominator else 0.0
    recall = true_positive / recall_denominator if recall_denominator else 0.0
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def _row_match(predicted_row: tuple[Any, ...], truth_row: tuple[Any, ...]):
    if not truth_row:
        return (1.0, 0.0, 0.0) if not predicted_row else (0.0, 1.0, 0.0)
    matches = sum(value in truth_row for value in predicted_row)
    predicted_only = sum(value not in truth_row for value in predicted_row)
    truth_only = sum(value not in predicted_row for value in truth_row)
    width = len(truth_row)
    return matches / width, predicted_only / width, truth_only / width


def _freeze_rows(rows: Iterable[Iterable[Any]]) -> tuple[tuple[Any, ...], ...]:
    return tuple(tuple(_freeze(value) for value in row) for row in rows)


def _fingerprint_value(value: Any):
    if value is None:
        return ["none"]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, int):
        return ["int", str(value)]
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("Non-finite floats cannot be fingerprinted")
        return ["float", (0.0 if value == 0 else value).hex()]
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Non-finite decimals cannot be fingerprinted")
        normalized = Decimal(0) if value == 0 else value.normalize()
        parts = normalized.as_tuple()
        return ["decimal", parts.sign, list(parts.digits), parts.exponent]
    if isinstance(value, str):
        return ["str", value]
    if isinstance(value, bytes):
        return ["bytes", value.hex()]
    if isinstance(value, bytearray):
        return ["bytes", bytes(value).hex()]
    if isinstance(value, datetime):
        return ["datetime", value.isoformat()]
    if isinstance(value, date):
        return ["date", value.isoformat()]
    if isinstance(value, time):
        return ["time", value.isoformat()]
    if isinstance(value, (list, tuple)):
        return ["sequence", [_fingerprint_value(item) for item in value]]
    if isinstance(value, dict):
        return [
            "mapping",
            sorted((str(key), _fingerprint_value(item)) for key, item in value.items()),
        ]
    raise TypeError(f"Unsupported result value for fingerprint: {type(value).__name__}")


def _freeze(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, dict):
        return tuple(sorted((key, _freeze(item)) for key, item in value.items()))
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, float) and value == 0:
        return 0.0
    if isinstance(value, Decimal) and value == 0:
        return Decimal(0)
    return value
