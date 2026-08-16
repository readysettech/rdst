import json
from pathlib import Path

import pytest

from devtools.ask_benchmark.bird_conformance import (
    OFFICIAL_EVALUATOR_FILES,
    OFFICIAL_EVALUATOR_REVISION,
    ConformanceError,
    conformance_receipt_path,
    verify_conformance_receipt,
    verify_full_candidate_replay,
    verify_official_oracle_conformance,
)
from devtools.ask_benchmark.bird_dataset import (
    MYSQL_INVALID_GOLD_CASE_IDS,
    SCORABLE_CASE_COUNT,
)
from devtools.ask_benchmark.models import BenchmarkCase, QueryResult


def test_oracle_matches_pinned_official_function_shapes(tmp_path: Path):
    (tmp_path / "evaluation_ex.py").write_text(
        "def calculate_ex(predicted_res, ground_truth_res):\n"
        "    return int(set(predicted_res) == set(ground_truth_res))\n",
        encoding="utf-8",
    )
    (tmp_path / "evaluation_f1.py").write_text(
        """
def calculate_row_match(predicted_row, ground_truth_row):
    matches = sum(value in ground_truth_row for value in predicted_row)
    predicted_only = sum(value not in ground_truth_row for value in predicted_row)
    truth_only = sum(value not in predicted_row for value in ground_truth_row)
    width = len(ground_truth_row)
    return matches / width, predicted_only / width, truth_only / width


def calculate_f1_score(predicted, ground_truth):
    if not predicted and not ground_truth:
        return 1.0
    predicted = list(dict.fromkeys(predicted))
    ground_truth = list(dict.fromkeys(ground_truth))
    matches = []
    predicted_only = []
    truth_only = []
    for index, truth_row in enumerate(ground_truth):
        if index >= len(predicted):
            matches.append(0.0)
            truth_only.append(1.0)
            continue
        matched, pred_extra, truth_extra = calculate_row_match(
            predicted[index], truth_row
        )
        matches.append(matched)
        predicted_only.append(pred_extra)
        truth_only.append(truth_extra)
    for _ in range(len(predicted) - len(ground_truth)):
        matches.append(0.0)
        predicted_only.append(1.0)
        truth_only.append(0.0)
    true_positive = sum(matches)
    false_positive = sum(predicted_only)
    false_negative = sum(truth_only)
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0
""".lstrip(),
        encoding="utf-8",
    )

    assert verify_official_oracle_conformance(tmp_path) == 5


def _write_official_scorer_functions(path: Path):
    (path / "evaluation_ex.py").write_text(
        "def calculate_ex(predicted_res, ground_truth_res):\n"
        "    return int(set(predicted_res) == set(ground_truth_res))\n",
        encoding="utf-8",
    )
    (path / "evaluation_f1.py").write_text(
        """
def calculate_row_match(predicted_row, ground_truth_row):
    matches = sum(value in ground_truth_row for value in predicted_row)
    predicted_only = sum(value not in ground_truth_row for value in predicted_row)
    truth_only = sum(value not in predicted_row for value in ground_truth_row)
    width = len(ground_truth_row)
    return matches / width, predicted_only / width, truth_only / width


def calculate_f1_score(predicted, ground_truth):
    if not predicted and not ground_truth:
        return 1.0
    predicted = list(dict.fromkeys(predicted))
    ground_truth = list(dict.fromkeys(ground_truth))
    matched, predicted_only, truth_only = calculate_row_match(predicted[0], ground_truth[0])
    precision = matched / (matched + predicted_only) if matched + predicted_only else 0.0
    recall = matched / (matched + truth_only) if matched + truth_only else 0.0
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0
""".lstrip(),
        encoding="utf-8",
    )


def test_full_candidate_replay_checks_all_scorable_outcomes(tmp_path: Path):
    _write_official_scorer_functions(tmp_path)
    cases = [
        BenchmarkCase(
            question_id=index,
            db_id="fixture",
            question="question",
            evidence="",
            gold_sql="SELECT 1",
            difficulty="simple",
            dialect="mysql",
        )
        for index in range(500)
        if index not in MYSQL_INVALID_GOLD_CASE_IDS
    ]
    attempts = [
        {
            "question_id": index,
            "model_name": "model",
            "scored": True,
            "validated_sql": "SELECT 1",
            "execution_correct": True,
        }
        for index in range(500)
        if index not in MYSQL_INVALID_GOLD_CASE_IDS
    ]

    class Executor:
        def execute(self, _sql, *, db_id):
            assert db_id == "fixture"
            return QueryResult(rows=((1,),))

    receipt = verify_full_candidate_replay(tmp_path, cases, attempts, Executor())

    assert receipt["full_dataset_case_count"] == SCORABLE_CASE_COUNT
    assert receipt["full_dataset_mismatch_count"] == 0
    assert len(receipt["full_dataset_replay_sha256"]) == 64


def test_full_candidate_replay_detects_stored_score_drift(tmp_path: Path):
    _write_official_scorer_functions(tmp_path)
    cases = [
        BenchmarkCase(index, "fixture", "q", "", "SELECT 1", "simple", "mysql")
        for index in range(500)
        if index not in MYSQL_INVALID_GOLD_CASE_IDS
    ]
    attempts = [
        {
            "question_id": index,
            "model_name": "model",
            "scored": True,
            "validated_sql": "SELECT 1",
            "execution_correct": index != 1,
        }
        for index in range(500)
        if index not in MYSQL_INVALID_GOLD_CASE_IDS
    ]

    class Executor:
        def execute(self, _sql, *, db_id):
            return QueryResult(rows=((1,),))

    receipt = verify_full_candidate_replay(tmp_path, cases, attempts, Executor())

    assert receipt["full_dataset_mismatch_count"] == 1
    assert receipt["full_dataset_mismatches"][0]["question_id"] == 1


def test_full_suite_rejects_sentinel_only_conformance_receipt(tmp_path: Path):
    path = conformance_receipt_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "revision": OFFICIAL_EVALUATOR_REVISION,
                "files": OFFICIAL_EVALUATOR_FILES,
                "fixture_count": 5,
                "scope": "sentinel_fixtures",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConformanceError, match="scorable cases"):
        verify_conformance_receipt(tmp_path, require_full=True)
