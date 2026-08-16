from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from features.ask.ambiguity_detection import (
    RANKED_RESOLVER_POLICY,
    Ambiguity,
    AmbiguityReport,
    clarification_required_for_report,
    resolve_ranked_ambiguity,
)

from .bird_dataset import DATASET_REVISION, PARTITION_REVISION, select_canary
from .models import BenchmarkCase

QUALIFICATION_CASE_IDS = (11, 26, 28, 45, 46)
GOLD_ALIGNMENT_BASIS = "bird-released-gold-sql-and-evidence-v1"


def gold_record_sha256(case: BenchmarkCase) -> str:
    """Fingerprint the complete released BIRD record used for evaluator labels."""
    payload = {
        "question_id": case.question_id,
        "db_id": case.db_id,
        "question": case.question,
        "evidence": case.evidence,
        "gold_sql": case.gold_sql,
        "difficulty": case.difficulty,
        "dialect": case.dialect,
    }
    return json_value_sha256(payload)


def json_value_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def load_gold_alignment_fixture(
    path: Path,
    all_cases: list[BenchmarkCase],
    *,
    require_gold_aligned: bool = False,
) -> dict[str, Any]:
    """Load labels and bind them to released gold in the development split."""
    try:
        fixture = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid clarification fixture {path}: {exc}") from exc
    if fixture.get("schema_version") != 2:
        raise ValueError("Gold-aligned clarification fixture must use schema_version 2")
    if fixture.get("partition_revision") != PARTITION_REVISION:
        raise ValueError("Clarification fixture has the wrong partition revision")
    if fixture.get("dataset_revision") != DATASET_REVISION:
        raise ValueError("Clarification fixture has the wrong dataset revision")
    if fixture.get("alignment_basis") != GOLD_ALIGNMENT_BASIS:
        raise ValueError("Clarification fixture has the wrong gold alignment basis")

    cases = fixture.get("cases")
    if not isinstance(cases, list):
        raise ValueError(  # noqa: TRY004 - invalid artifact value
            "Clarification fixture cases must be an array"
        )
    fixture_ids = [case.get("question_id") for case in cases]
    if fixture_ids != list(QUALIFICATION_CASE_IDS):
        raise ValueError(
            "Clarification fixture must contain qualification IDs "
            + ", ".join(map(str, QUALIFICATION_CASE_IDS))
        )
    development_ids = {case.question_id for case in select_canary(all_cases)}
    outside = sorted(set(fixture_ids) - development_ids)
    if outside:
        raise ValueError(
            "Clarification fixtures must come only from development cases: "
            + ", ".join(map(str, outside))
        )

    cases_by_id = {case.question_id: case for case in all_cases}
    for fixture_case in cases:
        question_id = int(fixture_case["question_id"])
        source_case = cases_by_id.get(question_id)
        if source_case is None:
            raise ValueError(f"Clarification case {question_id} is missing from BIRD")
        if fixture_case.get("gold_record_sha256") != gold_record_sha256(source_case):
            raise ValueError(
                f"Clarification case {question_id} has the wrong gold record hash"
            )
        if fixture_case.get("expected_detection") not in {"clear", "ambiguous"}:
            raise ValueError("expected_detection must be clear or ambiguous")
        if fixture_case.get("expected_auto_action") not in {
            "proceed",
            "select",
            "abstain",
        }:
            raise ValueError("Invalid expected_auto_action")
        response_sha256 = fixture_case.get("ambiguity_response_sha256")
        if not _is_sha256(response_sha256):
            raise ValueError(
                f"Clarification case {question_id} needs an ambiguity response hash"
            )
        for field in ("detector_output_sha256", "resolver_output_sha256"):
            if not _is_sha256(fixture_case.get(field)):
                raise ValueError(
                    f"Clarification case {question_id} needs a valid {field}"
                )
        alignments = fixture_case.get("gold_alignment")
        if not isinstance(alignments, list):
            raise ValueError(  # noqa: TRY004 - invalid artifact value
                f"Clarification case {question_id} needs gold_alignment labels"
            )
        ambiguity_ids = [item.get("ambiguity_id") for item in alignments]
        if len(ambiguity_ids) != len(set(ambiguity_ids)):
            raise ValueError(
                f"Clarification case {question_id} has duplicate ambiguity labels"
            )
        for item in alignments:
            _validate_alignment_item(question_id, item)
    if require_gold_aligned:
        _require_gold_alignment(fixture)
    return fixture


def build_gold_alignment_packet(
    fixture: dict[str, Any], attempts: list[dict[str, Any]]
) -> dict[str, Any]:
    """Bind recorded detector outputs to immutable BIRD-gold option labels."""
    _require_gold_alignment(fixture)
    attempts_by_id = {}
    for attempt in attempts:
        question_id = int(attempt.get("question_id", -1))
        if question_id not in QUALIFICATION_CASE_IDS:
            continue
        if question_id in attempts_by_id:
            raise ValueError(
                "Clarification qualification requires exactly one repetition per case"
            )
        attempts_by_id[question_id] = attempt
    missing = sorted(set(QUALIFICATION_CASE_IDS) - set(attempts_by_id))
    if missing:
        raise ValueError(
            "Run is missing clarification qualification cases: "
            + ", ".join(map(str, missing))
        )

    labels = {int(case["question_id"]): case for case in fixture["cases"]}
    packet_cases = []
    for question_id in QUALIFICATION_CASE_IDS:
        attempt = attempts_by_id[question_id]
        diagnostics = attempt.get("diagnostics") or {}
        report = diagnostics.get("ambiguity_report")
        resolutions = diagnostics.get("clarification_resolutions")
        if not isinstance(report, dict) or not isinstance(resolutions, list):
            raise ValueError(  # noqa: TRY004 - invalid artifact value
                f"Question {question_id} lacks ranked-resolver diagnostics"
            )
        response_sha256 = diagnostics.get("ambiguity_response_sha256")
        label = labels[question_id]
        if response_sha256 != label["ambiguity_response_sha256"]:
            raise ValueError(
                f"Question {question_id} detector output differs from its "
                "gold-aligned fixture"
            )
        if json_value_sha256(report) != label["detector_output_sha256"]:
            raise ValueError(
                f"Question {question_id} parsed detector output hash differs from "
                "its gold-aligned fixture"
            )
        if json_value_sha256(resolutions) != label["resolver_output_sha256"]:
            raise ValueError(
                f"Question {question_id} resolver output hash differs from its "
                "gold-aligned fixture"
            )
        ambiguities = report.get("ambiguities") or []
        reported_ids = [ambiguity.get("id") for ambiguity in ambiguities]
        aligned_ids = [item.get("ambiguity_id") for item in label["gold_alignment"]]
        if reported_ids != aligned_ids:
            raise ValueError(
                f"Question {question_id} ambiguity IDs differ from its gold alignment"
            )
        for ambiguity, alignment in zip(ambiguities, label["gold_alignment"]):
            option_ids = {
                option.get("id")
                for option in ambiguity.get("possible_interpretations") or []
            }
            if not set(alignment["correct_option_ids"]) <= option_ids:
                raise ValueError(
                    f"Question {question_id} ambiguity {ambiguity.get('id')} "
                    "references an unknown gold-aligned option"
                )
        packet_cases.append(
            {
                **label,
                "attempt_id": attempt.get("invocation_id"),
                "detector_output": report,
                "resolver_output": resolutions,
            }
        )
    return {
        "schema_version": 2,
        "partition_revision": PARTITION_REVISION,
        "dataset_revision": DATASET_REVISION,
        "alignment_status": "gold-aligned",
        "alignment_basis": GOLD_ALIGNMENT_BASIS,
        "source_fixture_aligned_at": fixture.get("aligned_at"),
        "source_fixture_sha256": json_value_sha256(fixture),
        "cases": packet_cases,
    }


def score_gold_alignment_packet(
    packet: dict[str, Any],
    fixture: dict[str, Any],
    *,
    replay_current_policy: bool = False,
) -> dict[str, Any]:
    """Score recorded or replayed policy behavior against released BIRD intent."""
    _require_gold_alignment(packet)
    _require_gold_alignment(fixture)
    _verify_packet_against_fixture(packet, fixture)
    detector_total = 0
    detector_correct = 0
    gold_target_total = 0
    covered_gold_targets = 0
    top_option_correct = 0
    missing_gold_targets = 0
    safe_missing_target_abstentions = 0
    auto_selection_total = 0
    auto_selection_correct = 0
    invalid_auto_selections = 0
    auto_action_total = 0
    auto_action_correct = 0
    partial_selection_count = 0

    for case in packet.get("cases", []):
        detector_output = case.get("detector_output") or {}
        ambiguities = detector_output.get("ambiguities") or []
        if replay_current_policy:
            requires_clarification, resolutions = _replay_policy(detector_output)
        else:
            requires_clarification = bool(
                detector_output.get("requires_clarification", False)
            )
            resolutions = case.get("resolver_output") or []
        resolutions_by_id = {item.get("ambiguity_id"): item for item in resolutions}
        observed_action = _observed_action(
            requires_clarification=requires_clarification,
            ambiguities=ambiguities,
            resolutions=resolutions,
        )
        partial_selection_count += int(observed_action == "partial-select")
        auto_action_total += 1
        auto_action_correct += int(observed_action == case.get("expected_auto_action"))
        observed_detection = "ambiguous" if requires_clarification else "clear"
        detector_total += 1
        detector_correct += int(observed_detection == case.get("expected_detection"))

        alignments = {
            item.get("ambiguity_id"): item for item in case.get("gold_alignment", [])
        }
        if set(alignments) != {item.get("id") for item in ambiguities}:
            raise ValueError("Gold alignment does not cover every detector ambiguity")

        for ambiguity in ambiguities:
            ambiguity_id = ambiguity.get("id")
            alignment = alignments[ambiguity_id]
            _validate_alignment_item(int(case["question_id"]), alignment)
            correct_ids = alignment["correct_option_ids"]
            options = ambiguity.get("possible_interpretations") or []
            option_ids = {option.get("id") for option in options}
            if not set(correct_ids) <= option_ids:
                raise ValueError(
                    f"Ambiguity {ambiguity_id!r} aligns an unknown option ID"
                )

            resolution = resolutions_by_id.get(ambiguity_id, {})
            selection_applied = _selection_applied(resolution)
            if selection_applied:
                auto_selection_total += 1
                selected = resolution.get("selected_option_id")
                correct = bool(alignment["material"]) and selected in correct_ids
                auto_selection_correct += int(correct)
                invalid_auto_selections += int(not correct)

            if not alignment["material"]:
                continue
            gold_target_total += 1
            if correct_ids:
                covered_gold_targets += 1
                ranked = sorted(
                    options,
                    key=lambda option: (-float(option["score"]), str(option["id"])),
                )
                top_option_correct += int(ranked[0]["id"] in correct_ids)
            else:
                missing_gold_targets += 1
                safe_missing_target_abstentions += int(
                    observed_action == "abstain" and not _selection_applied(resolution)
                )

    detector_accuracy = detector_correct / detector_total
    auto_action_accuracy = auto_action_correct / auto_action_total
    top_option_accuracy = (
        top_option_correct / covered_gold_targets if covered_gold_targets else None
    )
    option_coverage = (
        covered_gold_targets / gold_target_total if gold_target_total else None
    )
    missing_abstention_accuracy = (
        safe_missing_target_abstentions / missing_gold_targets
        if missing_gold_targets
        else None
    )
    safety_qualified = (
        detector_correct == detector_total
        and auto_action_correct == auto_action_total
        and invalid_auto_selections == 0
        and partial_selection_count == 0
        and safe_missing_target_abstentions == missing_gold_targets
    )
    ranking_qualified = (
        covered_gold_targets > 0 and top_option_correct == covered_gold_targets
    )

    return {
        "evaluation_mode": (
            "current-policy-replay" if replay_current_policy else "recorded"
        ),
        "alignment_basis": GOLD_ALIGNMENT_BASIS,
        "policy": (
            RANKED_RESOLVER_POLICY
            if replay_current_policy
            else _recorded_policy(packet)
        ),
        "aligned_case_count": detector_total,
        "detector_accuracy": detector_accuracy,
        "gold_target_count": gold_target_total,
        "gold_option_covered_count": covered_gold_targets,
        "gold_option_coverage": option_coverage,
        "missing_gold_option_count": missing_gold_targets,
        "missing_gold_option_abstention_accuracy": missing_abstention_accuracy,
        "top_option_accuracy_when_covered": top_option_accuracy,
        "auto_selection_count": auto_selection_total,
        "auto_selection_accuracy": (
            auto_selection_correct / auto_selection_total
            if auto_selection_total
            else None
        ),
        "invalid_auto_selection_count": invalid_auto_selections,
        "partial_selection_count": partial_selection_count,
        "auto_action_accuracy": auto_action_accuracy,
        "safety_qualified": safety_qualified,
        "ranking_qualified": ranking_qualified,
        "clarification_quality_claim_ready": safety_qualified
        and ranking_qualified
        and covered_gold_targets == gold_target_total,
        "qualified": safety_qualified,
    }


def _replay_policy(
    detector_output: dict[str, Any],
) -> tuple[bool, list[dict[str, Any]]]:
    ambiguities = [
        Ambiguity.from_dict(item) for item in detector_output.get("ambiguities") or []
    ]
    report = AmbiguityReport(
        ambiguities=ambiguities,
        total_ambiguities=len(ambiguities),
        requires_clarification=bool(
            detector_output.get("requires_clarification", False)
        ),
        can_proceed_with_assumptions=bool(
            detector_output.get("can_proceed_with_assumptions", True)
        ),
        overall_confidence=float(detector_output.get("overall_confidence", 1.0)),
    )
    requires_clarification = clarification_required_for_report(report)
    if not requires_clarification:
        return False, []
    ranked = [resolve_ranked_ambiguity(ambiguity) for ambiguity in ambiguities]
    apply_selections = bool(ranked) and all(
        resolution.action == "select" for resolution in ranked
    )
    resolutions = []
    for resolution in ranked:
        item = resolution.to_dict()
        item["applied"] = apply_selections
        resolutions.append(item)
    return True, resolutions


def _observed_action(
    *,
    requires_clarification: bool,
    ambiguities: list[dict[str, Any]],
    resolutions: list[dict[str, Any]],
) -> str:
    if not ambiguities or not requires_clarification:
        return "proceed"
    applied = [item for item in resolutions if _selection_applied(item)]
    if not applied:
        return "abstain"
    if len(applied) == len(ambiguities):
        return "select"
    return "partial-select"


def _selection_applied(resolution: dict[str, Any]) -> bool:
    if resolution.get("action") != "select":
        return False
    return bool(resolution.get("applied", True))


def _recorded_policy(packet: dict[str, Any]) -> str:
    policies = {
        str(resolution["policy"])
        for case in packet.get("cases", [])
        for resolution in case.get("resolver_output", [])
        if resolution.get("policy")
    }
    if not policies:
        return "unknown"
    if len(policies) == 1:
        return policies.pop()
    return "mixed:" + ",".join(sorted(policies))


def _validate_alignment_item(question_id: int, item: Any) -> None:
    if not isinstance(item, dict):
        raise ValueError(  # noqa: TRY004 - invalid artifact value
            f"Question {question_id} has an invalid gold alignment"
        )
    if not isinstance(item.get("ambiguity_id"), str):
        raise ValueError(  # noqa: TRY004 - invalid artifact value
            f"Question {question_id} alignment needs an ambiguity ID"
        )
    if not isinstance(item.get("material"), bool):
        raise ValueError(  # noqa: TRY004 - invalid artifact value
            f"Question {question_id} alignment needs material boolean"
        )
    correct_ids = item.get("correct_option_ids")
    if not isinstance(correct_ids, list) or not all(
        isinstance(option_id, str) for option_id in correct_ids
    ):
        raise ValueError(f"Question {question_id} alignment needs correct_option_ids")
    covered = item.get("gold_option_covered")
    if not isinstance(covered, bool) or covered != bool(correct_ids):
        raise ValueError(
            f"Question {question_id} has inconsistent gold option coverage"
        )
    for field in ("gold_target", "rationale"):
        if not str(item.get(field) or "").strip():
            raise ValueError(f"Question {question_id} alignment needs {field}")


def _require_gold_alignment(document: dict[str, Any]) -> None:
    if document.get("schema_version") != 2:
        raise ValueError("Clarification gold alignment must use schema_version 2")
    if document.get("alignment_status") != "gold-aligned":
        raise ValueError("Clarification labels are not aligned to BIRD gold")
    if document.get("alignment_basis") != GOLD_ALIGNMENT_BASIS:
        raise ValueError("Clarification labels have the wrong gold alignment basis")
    if document.get("partition_revision") != PARTITION_REVISION:
        raise ValueError("Clarification labels have the wrong partition revision")
    if document.get("dataset_revision") != DATASET_REVISION:
        raise ValueError("Clarification labels have the wrong dataset revision")
    cases = document.get("cases")
    if not isinstance(cases, list):
        raise ValueError(  # noqa: TRY004 - invalid artifact value
            "Clarification labels must contain cases"
        )
    if [case.get("question_id") for case in cases] != list(QUALIFICATION_CASE_IDS):
        raise ValueError("Clarification labels have the wrong qualification cases")


def _verify_packet_against_fixture(
    packet: dict[str, Any], fixture: dict[str, Any]
) -> None:
    if packet.get("source_fixture_sha256") != json_value_sha256(fixture):
        raise ValueError("Clarification packet does not match its gold fixture")
    fixture_cases = {
        int(case["question_id"]): case for case in fixture.get("cases", [])
    }
    for packet_case in packet.get("cases", []):
        question_id = int(packet_case["question_id"])
        expected = fixture_cases[question_id]
        for field in (
            "gold_record_sha256",
            "ambiguity_response_sha256",
            "detector_output_sha256",
            "resolver_output_sha256",
            "expected_detection",
            "expected_auto_action",
            "gold_alignment",
        ):
            if packet_case.get(field) != expected.get(field):
                raise ValueError(
                    f"Question {question_id} packet changed gold fixture field {field}"
                )
        if (
            json_value_sha256(packet_case.get("detector_output"))
            != expected["detector_output_sha256"]
        ):
            raise ValueError(
                f"Question {question_id} packet changed its detector output"
            )
        if (
            json_value_sha256(packet_case.get("resolver_output"))
            != expected["resolver_output_sha256"]
        ):
            raise ValueError(
                f"Question {question_id} packet changed its resolver output"
            )


def _is_sha256(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True
