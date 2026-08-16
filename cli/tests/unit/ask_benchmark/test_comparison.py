import json
from pathlib import Path

import pytest

from devtools.ask_benchmark.comparison import ComparisonError, compare_runs
from devtools.ask_benchmark.dashboard import write_dashboard


def _run(tmp_path: Path, run_id: str, model: str, correct: bool):
    path = tmp_path / run_id
    path.mkdir()
    manifest = {
        "run_id": run_id,
        "dataset": "bird-mini",
        "dataset_revision": "revision",
        "dialect": "mysql",
        "suite": "canary",
        "track": "model-only",
        "context_mode": "evidence",
        "case_ids": [1],
        "repetitions": 1,
        "expected_attempts_per_model": 1,
        "gold_result_fingerprint_codec": "typed-exact-multiset-v1",
        "gold_result_fingerprints": {"1": "gold"},
        "unstable_gold_case_ids": [],
        "context_hashes": {"fixture": "context"},
        "query_bounds": {"timeout_seconds": 30},
        "models": [{"name": model, "transport": "openrouter"}],
    }
    (path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    attempt = {
        "attempt_key": f"{model}-1",
        "invocation_id": f"{model}-invocation",
        "question_id": 1,
        "repetition": 0,
        "track": "model-only",
        "context_mode": "evidence",
        "transport": "openrouter",
        "model_name": model,
        "reasoning_effort": "max",
        "outcome": "correct" if correct else "incorrect_result",
        "scored": True,
        "execution_correct": correct,
        "soft_f1": 1.0 if correct else 0.0,
        "latency_ms": 1_000,
        "actual_cost_usd": "0.02",
        "normalized_cold_cost_usd": "0.01",
        "call_records": [
            {
                "input_tokens": 10,
                "output_tokens": 5,
                "reasoning_tokens": 2,
                "cached_input_tokens": 0,
                "actual_cost_usd": "0.02",
                "normalized_cold_cost_usd": "0.01",
                "expected_billed_cost_usd": "0.02",
            }
        ],
    }
    (path / "attempts.jsonl").write_text(json.dumps(attempt) + "\n", encoding="utf-8")
    return path


def test_compare_runs_builds_cross_track_pipeline_uplift(tmp_path: Path):
    direct = _run(tmp_path, "direct", "sonnet", True)
    product = _run(tmp_path, "product", "sonnet", False)
    product_manifest_path = product / "manifest.json"
    product_manifest = json.loads(product_manifest_path.read_text(encoding="utf-8"))
    product_manifest["track"] = "rdst-ask"
    product_manifest_path.write_text(json.dumps(product_manifest), encoding="utf-8")
    product_attempt_path = product / "attempts.jsonl"
    product_attempt = json.loads(product_attempt_path.read_text(encoding="utf-8"))
    product_attempt["track"] = "rdst-ask"
    product_attempt_path.write_text(
        json.dumps(product_attempt) + "\n", encoding="utf-8"
    )

    summary = compare_runs([direct, product], tmp_path / "comparison")

    comparison = summary["pipeline_comparisons"][0]
    assert comparison["model_name"] == "sonnet"
    assert comparison["accuracy_delta"] == -1.0
    assert comparison["first_only_correct"] == []
    assert comparison["second_only_correct"] == [{"question_id": 1, "repetition": 0}]
    markdown = (tmp_path / "comparison" / "leaderboard.md").read_text()
    assert "RDST pipeline uplift" in markdown


def test_compare_runs_writes_machine_readable_and_html_outputs(tmp_path: Path):
    first = _run(tmp_path, "first", "model-a", True)
    second = _run(tmp_path, "second", "model-b", False)
    output = tmp_path / "comparison"

    summary = compare_runs([first, second], output)
    dashboard = write_dashboard(summary, output / "leaderboard.html")

    assert summary["leaderboards"][0]["paired_comparisons"][0][
        "first_only_correct"
    ] == [{"question_id": 1, "repetition": 0}]
    assert (output / "leaderboard.json").exists()
    assert (output / "leaderboard.csv").exists()
    assert dashboard.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")


def test_compare_runs_rejects_invalid_v2_attempt_fingerprint(tmp_path: Path):
    first = _run(tmp_path, "first", "model-a", True)
    second = _run(tmp_path, "second", "model-b", True)
    for path in (first, second):
        manifest_path = path / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["artifact_schema_version"] = 2
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ComparisonError, match="configuration fingerprint"):
        compare_runs([first, second], tmp_path / "comparison")


def test_compare_runs_accepts_independent_compatible_gold_replay_receipts(
    tmp_path: Path,
):
    first = _run(tmp_path, "first", "model-a", True)
    second = _run(tmp_path, "second", "model-b", True)
    identity = {
        "dataset_revision": "revision",
        "gold_exclusion_revision": "invalid-mysql-gold-v1",
        "excluded_gold_case_ids": [208, 212, 227, 327],
        "case_count": 496,
        "gold_result_fingerprint_codec": "typed-exact-multiset-v1",
        "declared_unstable_case_ids": [671],
        "database_identity": {"mysql_version": "8.4.0"},
    }
    for path, replay_hash in ((first, "a" * 64), (second, "b" * 64)):
        manifest_path = path / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["gold_replay_receipt"] = {
            **identity,
            "replay_sha256": replay_hash,
        }
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    summary = compare_runs([first, second], tmp_path / "comparison")

    assert any(
        "independently generated" in warning
        for warning in summary["comparison"]["compatibility_warnings"]
    )


def test_compare_runs_warns_on_declared_unstable_gold_fingerprint(tmp_path: Path):
    first = _run(tmp_path, "first", "model-a", True)
    second = _run(tmp_path, "second", "model-b", True)
    for path, fingerprint in ((first, "gold-a"), (second, "gold-b")):
        manifest_path = path / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["gold_result_fingerprints"] = {"1482": fingerprint}
        manifest["unstable_gold_case_ids"] = [1482]
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    summary = compare_runs([first, second], tmp_path / "comparison")

    warnings = summary["comparison"]["compatibility_warnings"]
    assert any("1482" in warning for warning in warnings)


def test_compare_runs_rejects_unlisted_gold_fingerprint_drift(tmp_path: Path):
    first = _run(tmp_path, "first", "model-a", True)
    second = _run(tmp_path, "second", "model-b", True)
    manifest_path = second / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["gold_result_fingerprints"] = {"1": "changed"}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ComparisonError, match="stable cases: 1"):
        compare_runs([first, second], tmp_path / "comparison")


def test_compare_runs_rejects_different_gold_fingerprint_codecs(tmp_path: Path):
    first = _run(tmp_path, "first", "model-a", True)
    second = _run(tmp_path, "second", "model-b", True)
    manifest_path = second / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["gold_result_fingerprint_codec"] = "legacy-json-v0"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ComparisonError, match="gold_result_fingerprint_codec"):
        compare_runs([first, second], tmp_path / "comparison")


def test_compare_runs_rejects_different_case_sets(tmp_path: Path):
    first = _run(tmp_path, "first", "model-a", True)
    second = _run(tmp_path, "second", "model-b", True)
    manifest_path = second / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["case_ids"] = [2]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ComparisonError, match="case_ids"):
        compare_runs([first, second], tmp_path / "comparison")
