from __future__ import annotations

import ast
import hashlib
import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import requests

from .bird_dataset import MYSQL_INVALID_GOLD_CASE_IDS, SCORABLE_CASE_COUNT
from .models import BenchmarkCase, QueryResult
from .oracle import (
    UNSTABLE_GOLD_CASE_IDS,
    bird_execution_correct,
    bird_soft_f1,
    result_fingerprint,
    score_results,
)

OFFICIAL_EVALUATOR_REVISION = "b3d4bcbbae9a96934ad812551eb400c7a3b23c12"
OFFICIAL_EVALUATOR_FILES = {
    "evaluation_ex.py": "da1bbcd4530be83692d7c650c814ea9704bb710d0c953eb75d02ccb38233cf89",
    "evaluation_f1.py": "ba2700ee09b0c34d7e203788544757e6e6d6c99c0425bb92b3daa3927eee29bf",
    "evaluation_utils.py": "f6943d249caac5aeaef9bce21d43dbf29dcef85a0c965a76df032a9542f308bf",
}
_BASE_URL = (
    "https://raw.githubusercontent.com/bird-bench/mini_dev/"
    f"{OFFICIAL_EVALUATOR_REVISION}/evaluation"
)


class ConformanceError(RuntimeError):
    pass


def prepare_official_evaluator(
    cache_dir: Path, *, timeout_seconds: float = 30.0
) -> Path:
    destination = cache_dir / "official-evaluator" / OFFICIAL_EVALUATOR_REVISION
    destination.mkdir(parents=True, exist_ok=True)
    for filename, expected_sha256 in OFFICIAL_EVALUATOR_FILES.items():
        path = destination / filename
        if path.exists() and _sha256(path.read_bytes()) == expected_sha256:
            continue
        response = requests.get(f"{_BASE_URL}/{filename}", timeout=timeout_seconds)
        response.raise_for_status()
        content = response.content
        actual_sha256 = _sha256(content)
        if actual_sha256 != expected_sha256:
            raise ConformanceError(
                f"Official BIRD evaluator hash mismatch for {filename}: "
                f"expected {expected_sha256}, got {actual_sha256}"
            )
        descriptor, temporary_name = tempfile.mkstemp(
            dir=destination, prefix=f".{filename}.", suffix=".tmp"
        )
        try:
            with os.fdopen(descriptor, "wb") as file_obj:
                file_obj.write(content)
                file_obj.flush()
                os.fsync(file_obj.fileno())
            os.replace(temporary_name, path)
        except BaseException:
            Path(temporary_name).unlink(missing_ok=True)
            raise
    return destination


def conformance_receipt_path(cache_dir: Path) -> Path:
    return cache_dir / "official-evaluator" / "conformance.json"


def verify_conformance_receipt(
    cache_dir: Path, *, require_full: bool = False
) -> dict[str, Any]:
    path = conformance_receipt_path(cache_dir)
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConformanceError(
            f"Official BIRD scorer conformance receipt is unavailable at {path}; "
            "run the conformance command first"
        ) from exc
    expected = {
        "revision": OFFICIAL_EVALUATOR_REVISION,
        "files": OFFICIAL_EVALUATOR_FILES,
    }
    for field, value in expected.items():
        if receipt.get(field) != value:
            raise ConformanceError(
                f"Official BIRD scorer conformance receipt has stale {field}"
            )
    scope = str(receipt.get("scope", "sentinel_fixtures"))
    if require_full and (
        scope != "full_dataset"
        or int(receipt.get("full_dataset_case_count", 0)) != SCORABLE_CASE_COUNT
        or int(receipt.get("full_dataset_mismatch_count", -1)) != 0
        or len(str(receipt.get("full_dataset_replay_sha256", ""))) != 64
    ):
        raise ConformanceError(
            "Full-suite publication requires a verified official scorer receipt "
            f"for all {SCORABLE_CASE_COUNT} scorable cases"
        )
    return {
        "revision": receipt["revision"],
        "files": receipt["files"],
        "fixture_count": int(receipt.get("fixture_count", 0)),
        "scope": scope,
    }


def verify_official_oracle_conformance(evaluator_dir: Path) -> int:
    official_ex = _load_functions(
        evaluator_dir / "evaluation_ex.py", ("calculate_ex",)
    )["calculate_ex"]
    official_functions = _load_functions(
        evaluator_dir / "evaluation_f1.py",
        ("calculate_row_match", "calculate_f1_score"),
    )
    official_f1 = official_functions["calculate_f1_score"]

    fixtures = (
        ([(1,), (2,)], [(2,), (1,)]),
        ([(1,), (1,)], [(1,)]),
        ([(1, "a")], [(1, "b")]),
        ([], []),
        ([(1,), (2,)], [(1,), (3,)]),
    )
    for index, (predicted, gold) in enumerate(fixtures):
        expected_ex = bool(official_ex(predicted, gold))
        actual_ex = bird_execution_correct(predicted, gold)
        if actual_ex != expected_ex:
            raise ConformanceError(
                f"BIRD EX conformance failed for fixture {index}: "
                f"official={expected_ex}, rdst={actual_ex}"
            )
        expected_f1 = float(official_f1(predicted, gold))
        actual_f1 = bird_soft_f1(predicted, gold)
        if abs(actual_f1 - expected_f1) > 1e-12:
            raise ConformanceError(
                f"BIRD soft F1 conformance failed for fixture {index}: "
                f"official={expected_f1}, rdst={actual_f1}"
            )

    return len(fixtures)


def verify_full_candidate_replay(
    evaluator_dir: Path,
    cases: list[BenchmarkCase],
    attempts: list[dict[str, Any]],
    executor,
) -> dict[str, Any]:
    official_ex = _load_functions(
        evaluator_dir / "evaluation_ex.py", ("calculate_ex",)
    )["calculate_ex"]
    official_f1 = _load_functions(
        evaluator_dir / "evaluation_f1.py",
        ("calculate_row_match", "calculate_f1_score"),
    )["calculate_f1_score"]
    scored = [attempt for attempt in attempts if attempt.get("scored", True)]
    models = {str(attempt.get("model_name")) for attempt in scored}
    if len(models) != 1:
        raise ConformanceError("Full replay requires exactly one model configuration")
    by_question: dict[int, dict[str, Any]] = {}
    for attempt in scored:
        question_id = int(attempt["question_id"])
        if question_id in by_question:
            raise ConformanceError(
                f"Full replay has duplicate scored question {question_id}"
            )
        by_question[question_id] = attempt
    expected_ids = {case.question_id for case in cases}
    included_exclusions = sorted(expected_ids & set(MYSQL_INVALID_GOLD_CASE_IDS))
    if included_exclusions:
        raise ConformanceError(
            f"Full replay includes excluded invalid-gold cases: {included_exclusions}"
        )
    if len(cases) != SCORABLE_CASE_COUNT or set(by_question) != expected_ids:
        raise ConformanceError(
            "Full replay requires one scored attempt for each of "
            f"the {SCORABLE_CASE_COUNT} scorable cases"
        )

    mismatches = []
    unstable_stored_changes = []
    replay_rows = []
    for case in cases:
        attempt = by_question[case.question_id]
        gold = executor.execute(case.gold_sql, db_id=case.db_id)
        if not gold.succeeded:
            raise ConformanceError(
                f"Gold query {case.question_id} failed during official replay: {gold.error}"
            )
        candidate_sql = attempt.get("validated_sql") or attempt.get("generated_sql")
        candidate = (
            executor.execute(str(candidate_sql), db_id=case.db_id)
            if candidate_sql
            else QueryResult(error="No candidate SQL")
        )
        local = score_results(candidate, gold)
        official_execution = bool(
            official_ex(candidate.rows, gold.rows) if candidate.succeeded else False
        )
        official_soft_f1 = float(
            official_f1(candidate.rows, gold.rows) if candidate.succeeded else 0.0
        )
        if (
            local.execution_correct != official_execution
            or abs(local.soft_f1 - official_soft_f1) > 1e-12
        ):
            mismatches.append(
                {
                    "question_id": case.question_id,
                    "kind": "scorer",
                    "local_ex": local.execution_correct,
                    "official_ex": official_execution,
                    "local_soft_f1": local.soft_f1,
                    "official_soft_f1": official_soft_f1,
                }
            )
        stored_execution = bool(attempt.get("execution_correct"))
        if stored_execution != official_execution:
            target = (
                unstable_stored_changes
                if case.question_id in UNSTABLE_GOLD_CASE_IDS
                else mismatches
            )
            target.append(
                {
                    "question_id": case.question_id,
                    "kind": "stored_replay",
                    "stored_ex": stored_execution,
                    "replayed_ex": official_execution,
                }
            )
        replay_rows.append(
            {
                "question_id": case.question_id,
                "candidate_sql_sha256": _sha256(str(candidate_sql or "").encode()),
                "candidate_result_sha256": result_fingerprint(candidate)
                if candidate.succeeded
                else None,
                "gold_result_sha256": result_fingerprint(gold),
                "execution_correct": official_execution,
                "soft_f1": official_soft_f1,
            }
        )
    replay_sha256 = _sha256(
        json.dumps(replay_rows, sort_keys=True, separators=(",", ":")).encode()
    )
    return {
        "full_dataset_case_count": len(cases),
        "full_dataset_mismatch_count": len(mismatches),
        "full_dataset_mismatches": mismatches,
        "unstable_stored_replay_changes": unstable_stored_changes,
        "full_dataset_replay_sha256": replay_sha256,
        "model_name": next(iter(models)),
    }


def _load_functions(
    path: Path, names: tuple[str, ...]
) -> dict[str, Callable[..., Any]]:
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConformanceError(
            f"Unable to read official evaluator {path}: {exc}"
        ) from exc
    tree = ast.parse(source, filename=str(path))
    selected = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    found = {node.name for node in selected}
    missing = set(names) - found
    if missing:
        raise ConformanceError(
            f"Official evaluator {path} is missing functions: {', '.join(sorted(missing))}"
        )
    module = ast.Module(body=selected, type_ignores=[])
    namespace: dict[str, Any] = {"__builtins__": __builtins__}
    # The evaluator source is accepted only after its pinned SHA-256 matches.
    exec(compile(module, str(path), "exec"), namespace)  # noqa: S102
    return {name: namespace[name] for name in names}


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
