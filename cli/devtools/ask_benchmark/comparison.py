from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shared.persistence import write_json, write_text

from .artifacts import ArtifactStore, make_attempt_key
from .report import build_summary, paired_comparison, render_markdown


class ComparisonError(ValueError):
    pass


@dataclass(frozen=True)
class LoadedRun:
    path: Path
    manifest: dict[str, Any]
    attempts: list[dict[str, Any]]
    call_receipts: list[dict[str, Any]]


_COMPATIBILITY_FIELDS = (
    "artifact_schema_version",
    "rdst_revision",
    "rdst_dirty",
    "rdst_diff_sha256",
    "dataset",
    "dataset_revision",
    "gold_exclusion_revision",
    "excluded_gold_case_ids",
    "dialect",
    "suite",
    "partition_provenance",
    "holdout_campaign_id",
    "context_mode",
    "case_ids",
    "repetitions",
    "expected_attempts_per_model",
    "gold_result_fingerprint_codec",
    "unstable_gold_case_ids",
    "context_hashes",
    "context_provenance",
    "database_provision",
    "query_bounds",
)


def compare_runs(run_dirs: list[Path], output_dir: Path):
    if len(run_dirs) < 2:
        raise ComparisonError("Comparison requires at least two run directories")
    runs = [_load_run(path) for path in run_dirs]
    warnings = _validate_compatibility(runs)
    _validate_model_names(runs)

    attempts = [
        {
            **attempt,
            "interaction_mode": attempt.get("interaction_mode")
            or run.manifest.get("interaction_mode")
            or (
                "interactive-no-answer"
                if run.manifest.get("track") == "rdst-ask"
                else "not-applicable"
            ),
        }
        for run in runs
        for attempt in run.attempts
    ]
    call_receipts = [
        {
            **receipt,
            "_source_track": run.manifest.get("track"),
            "_source_context_mode": run.manifest.get("context_mode"),
            "_source_interaction_mode": run.manifest.get("interaction_mode")
            or (
                "interactive-no-answer"
                if run.manifest.get("track") == "rdst-ask"
                else "not-applicable"
            ),
            "_source_transport": receipt.get("transport"),
        }
        for run in runs
        for receipt in run.call_receipts
    ]
    expected = int(runs[0].manifest["expected_attempts_per_model"])
    summary = build_summary(
        attempts,
        expected_attempts_per_model=expected,
        call_receipts=call_receipts,
    )
    summary["comparison"] = {
        "run_dirs": [str(run.path) for run in runs],
        "run_ids": [run.manifest.get("run_id") for run in runs],
        "compatibility_warnings": warnings,
    }
    tracks = {str(run.manifest.get("track")) for run in runs}
    if tracks == {"model-only", "rdst-ask"}:
        by_track = {
            track: {
                str(model["name"]): [
                    attempt
                    for run in runs
                    if str(run.manifest.get("track")) == track
                    for attempt in run.attempts
                    if str(attempt.get("model_name")) == str(model["name"])
                ]
                for run in runs
                if str(run.manifest.get("track")) == track
                for model in run.manifest.get("models", [])
            }
            for track in tracks
        }
        shared_models = sorted(set(by_track["model-only"]) & set(by_track["rdst-ask"]))
        if not shared_models:
            raise ComparisonError(
                "Pipeline comparison requires matching model configuration names"
            )
        summary["pipeline_comparisons"] = [
            {
                "model_name": model_name,
                **paired_comparison(
                    "rdst-ask",
                    by_track["rdst-ask"][model_name],
                    "model-only",
                    by_track["model-only"][model_name],
                ),
            }
            for model_name in shared_models
        ]

    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "leaderboard.json", summary)
    write_text(output_dir / "leaderboard.md", render_markdown(summary))
    _write_csv(output_dir / "leaderboard.csv", summary)
    return summary


def _load_run(path: Path) -> LoadedRun:
    store = ArtifactStore(path)
    try:
        manifest = json.loads(store.manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ComparisonError(
            f"Unable to load run manifest {store.manifest_path}: {exc}"
        ) from exc
    attempts = store.load_attempts()
    _validate_attempt_membership(path, manifest, attempts)
    models_by_name = {str(model["name"]): model for model in manifest.get("models", [])}
    for attempt in attempts:
        if model := models_by_name.get(str(attempt.get("model_name"))):
            attempt.setdefault("reasoning_effort", model.get("reasoning_effort"))
            attempt.setdefault("max_tokens", model.get("max_tokens"))
    return LoadedRun(
        path=path,
        manifest=manifest,
        attempts=attempts,
        call_receipts=store.load_call_receipts(),
    )


def _validate_attempt_membership(
    path: Path, manifest: dict[str, Any], attempts: list[dict[str, Any]]
) -> None:
    schema_version = int(manifest.get("artifact_schema_version", 1))
    if schema_version < 2:
        return
    models = {str(model["name"]): model for model in manifest.get("models", [])}
    model_names = set(models)
    case_ids = {int(case_id) for case_id in manifest.get("case_ids", [])}
    repetitions = int(manifest.get("repetitions", 0))
    fingerprints: dict[str, set[str]] = {}
    for attempt in attempts:
        model_name = str(attempt.get("model_name"))
        question_id = int(attempt.get("question_id", -1))
        repetition = int(attempt.get("repetition", -1))
        expected_dimensions = {
            "track": manifest.get("track"),
            "context_mode": manifest.get("context_mode"),
        }
        if "interaction_mode" in manifest:
            expected_dimensions["interaction_mode"] = manifest["interaction_mode"]
        invalid_dimensions = [
            field
            for field, expected in expected_dimensions.items()
            if attempt.get(field) != expected
        ]
        if model_name in models and attempt.get("transport") != models[model_name].get(
            "transport"
        ):
            invalid_dimensions.append("transport")
        if (
            model_name not in model_names
            or question_id not in case_ids
            or repetition < 0
            or repetition >= repetitions
            or invalid_dimensions
        ):
            raise ComparisonError(
                f"Run {path} contains an attempt outside its manifest: "
                f"model={model_name!r}, question_id={question_id}, "
                f"repetition={repetition}, invalid={invalid_dimensions}"
            )
        fingerprint = str(attempt.get("configuration_fingerprint", ""))
        expected_fingerprint = manifest.get("model_configuration_fingerprints", {}).get(
            model_name
        )
        if len(fingerprint) != 64 or fingerprint != expected_fingerprint:
            raise ComparisonError(
                f"Run {path} has an invalid configuration fingerprint for "
                f"{model_name} question {question_id}"
            )
        expected_key = make_attempt_key(
            dataset_revision=str(manifest["dataset_revision"]),
            dialect=str(manifest["dialect"]),
            track=str(manifest["track"]),
            context_mode=str(manifest["context_mode"]),
            model_identity=fingerprint,
            interaction_mode=manifest.get("interaction_mode"),
            question_id=question_id,
            repetition=repetition,
        )
        if attempt.get("attempt_key") != expected_key:
            raise ComparisonError(
                f"Run {path} has an invalid logical attempt key for "
                f"{model_name} question {question_id} repetition {repetition}"
            )
        fingerprints.setdefault(model_name, set()).add(fingerprint)
    divergent = [name for name, values in fingerprints.items() if len(values) != 1]
    if divergent:
        raise ComparisonError(
            f"Run {path} mixes configuration fingerprints for: "
            f"{', '.join(sorted(divergent))}"
        )


def _validate_compatibility(runs: list[LoadedRun]) -> list[str]:
    tracks = {str(run.manifest.get("track")) for run in runs}
    if len(tracks) > 1 and tracks != {"model-only", "rdst-ask"}:
        raise ComparisonError(f"Runs have incompatible tracks: {sorted(tracks)}")
    baseline = runs[0]
    warnings = []
    for run in runs[1:]:
        for field in _COMPATIBILITY_FIELDS:
            expected = _canonical(baseline.manifest.get(field))
            actual = _canonical(run.manifest.get(field))
            if actual != expected:
                raise ComparisonError(
                    f"Run {run.path} has incompatible {field}: "
                    f"expected {baseline.manifest.get(field)!r}, "
                    f"got {run.manifest.get(field)!r}"
                )
        expected_receipt = baseline.manifest.get("gold_replay_receipt")
        actual_receipt = run.manifest.get("gold_replay_receipt")
        if (expected_receipt is None) != (actual_receipt is None):
            raise ComparisonError(
                f"Run {run.path} has incompatible gold_replay_receipt presence"
            )
        if expected_receipt is not None:
            receipt_identity_fields = (
                "dataset_revision",
                "gold_exclusion_revision",
                "excluded_gold_case_ids",
                "case_count",
                "gold_result_fingerprint_codec",
                "declared_unstable_case_ids",
                "database_identity",
            )
            for field in receipt_identity_fields:
                if _canonical(actual_receipt.get(field)) != _canonical(
                    expected_receipt.get(field)
                ):
                    raise ComparisonError(
                        f"Run {run.path} has incompatible gold replay {field}"
                    )
            if actual_receipt.get("replay_sha256") != expected_receipt.get(
                "replay_sha256"
            ):
                warnings.append(
                    "Runs use independently generated compatible gold replay receipts"
                )
        expected_gold = baseline.manifest.get("gold_result_fingerprints", {})
        actual_gold = run.manifest.get("gold_result_fingerprints", {})
        differing_gold = {
            str(case_id)
            for case_id in set(expected_gold) | set(actual_gold)
            if expected_gold.get(case_id) != actual_gold.get(case_id)
        }
        unstable = {
            str(case_id)
            for case_id in baseline.manifest.get("unstable_gold_case_ids", [])
        }
        stable_differences = differing_gold - unstable
        if stable_differences:
            raise ComparisonError(
                f"Run {run.path} has incompatible gold_result_fingerprints "
                f"for stable cases: {', '.join(sorted(stable_differences))}"
            )
        if differing_gold:
            warnings.append(
                "Known floating-point gold instability changed exact result "
                f"fingerprints for cases: {', '.join(sorted(differing_gold))}"
            )
    protocol_values = {run.manifest.get("benchmark_protocol_sha256") for run in runs}
    if None in protocol_values:
        warnings.append(
            "One or more legacy runs lack benchmark_protocol_sha256; prompt "
            "compatibility is not cryptographically verified"
        )
    elif len(protocol_values) != 1:
        raise ComparisonError("Runs have incompatible benchmark_protocol_sha256")
    scorer_values = {_canonical(run.manifest.get("scorer_conformance")) for run in runs}
    if None in scorer_values:
        warnings.append(
            "One or more legacy runs lack official scorer-conformance provenance"
        )
    elif len(scorer_values) != 1:
        raise ComparisonError("Runs have incompatible scorer_conformance")
    return warnings


def _validate_model_names(runs: list[LoadedRun]) -> None:
    owners: dict[str, tuple[Path, str, Any]] = {}
    for run in runs:
        track = str(run.manifest.get("track"))
        for model in run.manifest.get("models", []):
            name = str(model.get("name"))
            identity = _canonical(model)
            if name in owners:
                owner_path, owner_track, owner_identity = owners[name]
                if track != owner_track and identity == owner_identity:
                    continue
                raise ComparisonError(
                    f"Model name {name!r} appears in both {owner_path} and {run.path}; "
                    "use distinct configuration identities"
                )
            owners[name] = (run.path, track, identity)


def _write_csv(path: Path, summary: dict[str, Any]) -> None:
    fields = (
        "track",
        "context_mode",
        "transport",
        "model_name",
        "reasoning_effort",
        "coverage",
        "execution_accuracy",
        "official_execution_accuracy",
        "accuracy_ci_low",
        "accuracy_ci_high",
        "normalized_cold_cost_per_task_usd",
        "normalized_cold_cost_per_correct_usd",
        "billed_cost_per_task_usd",
        "mean_latency_ms",
        "p95_latency_ms",
        "cost_efficiency_eligible",
        "cost_efficiency_winner",
        "pareto_optimal",
    )
    with open(path, "w", encoding="utf-8", newline="") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=fields)
        writer.writeheader()
        for board in summary["leaderboards"]:
            for model in board["models"]:
                interval = model["accuracy_ci_95"]
                writer.writerow(
                    {
                        "track": board["track"],
                        "context_mode": board["context_mode"],
                        "transport": board["transport"],
                        "model_name": model["model_name"],
                        "reasoning_effort": model["reasoning_effort"],
                        "coverage": model["coverage"],
                        "execution_accuracy": model["execution_accuracy"],
                        "official_execution_accuracy": model[
                            "official_execution_accuracy"
                        ],
                        "accuracy_ci_low": interval[0],
                        "accuracy_ci_high": interval[1],
                        "normalized_cold_cost_per_task_usd": model[
                            "cost_per_question_usd"
                        ],
                        "normalized_cold_cost_per_correct_usd": model[
                            "cost_per_correct_usd"
                        ],
                        "billed_cost_per_task_usd": model[
                            "billed_cost_per_question_usd"
                        ],
                        "mean_latency_ms": model["mean_latency_ms"],
                        "p95_latency_ms": model["p95_latency_ms"],
                        "cost_efficiency_eligible": model["cost_efficiency_eligible"],
                        "cost_efficiency_winner": model["cost_efficiency_winner"],
                        "pareto_optimal": model["pareto_optimal"],
                    }
                )


def _canonical(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
