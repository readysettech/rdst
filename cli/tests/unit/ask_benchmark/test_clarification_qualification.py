import json
from pathlib import Path

import pytest

from devtools.ask_benchmark.bird_dataset import DATASET_REVISION, PARTITION_REVISION
from devtools.ask_benchmark.clarification_qualification import (
    GOLD_ALIGNMENT_BASIS,
    QUALIFICATION_CASE_IDS,
    build_gold_alignment_packet,
    gold_record_sha256,
    json_value_sha256,
    load_gold_alignment_fixture,
    score_gold_alignment_packet,
)
from devtools.ask_benchmark.models import BenchmarkCase


def _development_cases():
    ids = list(QUALIFICATION_CASE_IDS) + list(range(100, 145))
    return [
        BenchmarkCase(
            question_id=question_id,
            db_id="fixture",
            question="question",
            evidence="released evidence",
            gold_sql="SELECT 1",
            difficulty="simple",
            dialect="mysql",
        )
        for question_id in ids
    ]


def _fixture(*, alignment_status="gold-aligned"):
    cases = _development_cases()
    by_id = {case.question_id: case for case in cases}
    attempts = {item["question_id"]: item for item in _attempts()}
    return {
        "schema_version": 2,
        "partition_revision": PARTITION_REVISION,
        "dataset_revision": DATASET_REVISION,
        "alignment_status": alignment_status,
        "alignment_basis": GOLD_ALIGNMENT_BASIS,
        "aligned_at": "2026-08-18T00:00:00Z",
        "cases": [
            {
                "question_id": question_id,
                "gold_record_sha256": gold_record_sha256(by_id[question_id]),
                "ambiguity_response_sha256": "a" * 64,
                "detector_output_sha256": json_value_sha256(
                    attempts[question_id]["diagnostics"]["ambiguity_report"]
                ),
                "resolver_output_sha256": json_value_sha256(
                    attempts[question_id]["diagnostics"]["clarification_resolutions"]
                ),
                "expected_detection": "ambiguous",
                "expected_auto_action": "abstain",
                "gold_alignment": [
                    {
                        "ambiguity_id": "metric",
                        "material": True,
                        "gold_target": "Use the released BIRD metric.",
                        "gold_option_covered": True,
                        "correct_option_ids": ["a"],
                        "rationale": "BIRD gold uses option a.",
                    }
                ],
            }
            for question_id in QUALIFICATION_CASE_IDS
        ],
    }


def _attempts():
    return [
        {
            "question_id": question_id,
            "invocation_id": f"attempt-{question_id}",
            "diagnostics": {
                "ambiguity_response_sha256": "a" * 64,
                "ambiguity_report": {
                    "ambiguities": [
                        {
                            "id": "metric",
                            "category": "unclear_schema_reference",
                            "term": "metric",
                            "reason": "Two metrics differ.",
                            "possible_interpretations": [
                                {
                                    "id": "a",
                                    "text": "metric a",
                                    "score": 0.9,
                                    "evidence": ["schema"],
                                    "sql_effect": "column_a",
                                },
                                {
                                    "id": "b",
                                    "text": "metric b",
                                    "score": 0.1,
                                    "evidence": ["schema"],
                                    "sql_effect": "column_b",
                                },
                            ],
                            "clarifying_question": "Which metric?",
                            "priority": "medium",
                        }
                    ],
                    "total_ambiguities": 1,
                    "requires_clarification": True,
                    "can_proceed_with_assumptions": False,
                    "overall_confidence": 0.5,
                },
                "clarification_resolutions": [
                    {
                        "ambiguity_id": "metric",
                        "action": "abstain",
                        "selected_option_id": None,
                        "applied": False,
                    }
                ],
            },
        }
        for question_id in QUALIFICATION_CASE_IDS
    ]


def _sync_output_hashes(fixture, attempts):
    attempts_by_id = {item["question_id"]: item for item in attempts}
    for case in fixture["cases"]:
        diagnostics = attempts_by_id[case["question_id"]]["diagnostics"]
        case["detector_output_sha256"] = json_value_sha256(
            diagnostics["ambiguity_report"]
        )
        case["resolver_output_sha256"] = json_value_sha256(
            diagnostics["clarification_resolutions"]
        )
    return fixture


def test_fixture_must_be_gold_aligned_for_qualification(tmp_path: Path):
    fixture = _fixture(alignment_status="pending")
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(fixture))

    loaded = load_gold_alignment_fixture(path, _development_cases())
    assert loaded["alignment_status"] == "pending"
    with pytest.raises(ValueError, match="not aligned"):
        load_gold_alignment_fixture(
            path, _development_cases(), require_gold_aligned=True
        )


def test_fixture_rejects_non_development_case(tmp_path: Path):
    fixture = _fixture()
    fixture["cases"][-1]["question_id"] = 999
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(fixture))

    with pytest.raises(ValueError, match="qualification IDs"):
        load_gold_alignment_fixture(path, _development_cases())


def test_fixture_rejects_changed_bird_gold_record(tmp_path: Path):
    fixture = _fixture()
    fixture["cases"][0]["gold_record_sha256"] = "0" * 64
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(fixture))

    with pytest.raises(ValueError, match="wrong gold record hash"):
        load_gold_alignment_fixture(path, _development_cases())


def test_packet_binds_options_to_gold_without_pending_human_review():
    packet = build_gold_alignment_packet(_fixture(), _attempts())

    assert packet["alignment_status"] == "gold-aligned"
    assert packet["alignment_basis"] == GOLD_ALIGNMENT_BASIS
    assert packet["cases"][0]["gold_alignment"][0]["correct_option_ids"] == ["a"]
    assert "review" not in packet["cases"][0]


def test_packet_rejects_detector_output_not_bound_by_fixture():
    attempts = _attempts()
    attempts[0]["diagnostics"]["ambiguity_response_sha256"] = "b" * 64

    with pytest.raises(ValueError, match="differs from its gold-aligned fixture"):
        build_gold_alignment_packet(_fixture(), attempts)


def test_scoring_rejects_packet_tampering_after_export():
    fixture = _fixture()
    packet = build_gold_alignment_packet(fixture, _attempts())
    packet["cases"][0]["detector_output"]["ambiguities"][0]["possible_interpretations"][
        0
    ]["score"] = 1.0

    with pytest.raises(ValueError, match="changed its detector output"):
        score_gold_alignment_packet(packet, fixture)


def test_scoring_rejects_wrong_applied_auto_selection():
    fixture = _fixture()
    attempts = _attempts()
    fixture["cases"][0]["expected_auto_action"] = "select"
    attempts[0]["diagnostics"]["clarification_resolutions"] = [
        {
            "ambiguity_id": "metric",
            "action": "select",
            "selected_option_id": "b",
            "applied": True,
        }
    ]
    _sync_output_hashes(fixture, attempts)
    packet = build_gold_alignment_packet(fixture, attempts)

    score = score_gold_alignment_packet(packet, fixture)

    assert score["top_option_accuracy_when_covered"] == 1.0
    assert score["invalid_auto_selection_count"] == 1
    assert score["qualified"] is False


def test_auto_action_proceeds_when_detector_marks_case_clear():
    fixture = _fixture()
    attempts = _attempts()
    fixture["cases"][0]["expected_detection"] = "clear"
    fixture["cases"][0]["expected_auto_action"] = "proceed"
    attempts[0]["diagnostics"]["ambiguity_report"]["requires_clarification"] = False
    attempts[0]["diagnostics"]["clarification_resolutions"] = []
    _sync_output_hashes(fixture, attempts)
    packet = build_gold_alignment_packet(fixture, attempts)

    score = score_gold_alignment_packet(packet, fixture)

    assert score["detector_accuracy"] == 1.0
    assert score["auto_action_accuracy"] == 1.0


def test_missing_gold_option_requires_case_level_abstention():
    fixture = _fixture()
    attempts = _attempts()
    for case in fixture["cases"]:
        alignment = case["gold_alignment"][0]
        alignment["gold_option_covered"] = False
        alignment["correct_option_ids"] = []
    packet = build_gold_alignment_packet(fixture, attempts)

    recorded = score_gold_alignment_packet(packet, fixture)
    assert recorded["missing_gold_option_abstention_accuracy"] == 1.0
    assert recorded["qualified"] is True

    attempts[0]["diagnostics"]["ambiguity_report"]["requires_clarification"] = False
    attempts[0]["diagnostics"]["clarification_resolutions"] = []
    fixture["cases"][0]["expected_detection"] = "ambiguous"
    fixture["cases"][0]["expected_auto_action"] = "abstain"
    _sync_output_hashes(fixture, attempts)
    unsafe_packet = build_gold_alignment_packet(fixture, attempts)
    unsafe = score_gold_alignment_packet(unsafe_packet, fixture)
    assert unsafe["missing_gold_option_abstention_accuracy"] == 0.8
    assert unsafe["qualified"] is False


def test_current_policy_replay_escalates_two_medium_ambiguities_atomically():
    fixture = _fixture()
    attempts = _attempts()
    detector_output = attempts[0]["diagnostics"]["ambiguity_report"]
    first = detector_output["ambiguities"][0]
    second = json.loads(json.dumps(first))
    second["id"] = "second"
    second["possible_interpretations"][0]["id"] = "second-a"
    second["possible_interpretations"][1]["id"] = "second-b"
    detector_output["ambiguities"].append(second)
    detector_output["total_ambiguities"] = 2
    detector_output["requires_clarification"] = False
    detector_output["can_proceed_with_assumptions"] = True
    detector_output["overall_confidence"] = 0.72
    for ambiguity in detector_output["ambiguities"]:
        ambiguity["possible_interpretations"][0]["score"] = 0.7
        ambiguity["possible_interpretations"][1]["score"] = 0.3
    fixture["cases"][0]["gold_alignment"].append(
        {
            "ambiguity_id": "second",
            "material": True,
            "gold_target": "A missing released interpretation.",
            "gold_option_covered": False,
            "correct_option_ids": [],
            "rationale": "Neither option matches BIRD gold.",
        }
    )
    _sync_output_hashes(fixture, attempts)
    packet = build_gold_alignment_packet(fixture, attempts)

    replayed = score_gold_alignment_packet(packet, fixture, replay_current_policy=True)

    assert replayed["evaluation_mode"] == "current-policy-replay"
    assert replayed["missing_gold_option_abstention_accuracy"] == 1.0


def test_current_policy_replay_does_not_apply_partial_selection():
    fixture = _fixture()
    attempts = _attempts()
    for other_attempt in attempts[1:]:
        options = other_attempt["diagnostics"]["ambiguity_report"]["ambiguities"][0][
            "possible_interpretations"
        ]
        options[0]["score"] = 0.55
        options[1]["score"] = 0.45
    detector_output = attempts[0]["diagnostics"]["ambiguity_report"]
    first = detector_output["ambiguities"][0]
    first["possible_interpretations"][0]["score"] = 0.96
    second = json.loads(json.dumps(first))
    second["id"] = "second"
    second["possible_interpretations"][0]["id"] = "second-a"
    second["possible_interpretations"][0]["score"] = 0.55
    second["possible_interpretations"][1]["id"] = "second-b"
    second["possible_interpretations"][1]["score"] = 0.45
    detector_output["ambiguities"].append(second)
    detector_output["total_ambiguities"] = 2
    fixture["cases"][0]["gold_alignment"].append(
        {
            "ambiguity_id": "second",
            "material": True,
            "gold_target": "Use option second-a.",
            "gold_option_covered": True,
            "correct_option_ids": ["second-a"],
            "rationale": "BIRD gold uses second-a.",
        }
    )
    _sync_output_hashes(fixture, attempts)
    packet = build_gold_alignment_packet(fixture, attempts)

    score = score_gold_alignment_packet(packet, fixture, replay_current_policy=True)

    assert score["partial_selection_count"] == 0
    assert score["auto_selection_count"] == 0
