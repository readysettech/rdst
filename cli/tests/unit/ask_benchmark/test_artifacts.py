from pathlib import Path

import pytest

from devtools.ask_benchmark.artifacts import ArtifactStore, make_attempt_key


def test_attempt_keys_include_model_and_repetition():
    first = make_attempt_key(
        dataset_revision="rev",
        dialect="mysql",
        track="model-only",
        context_mode="evidence",
        model_identity="kimi-k3@openrouter",
        question_id=1,
        repetition=0,
    )
    second = make_attempt_key(
        dataset_revision="rev",
        dialect="mysql",
        track="model-only",
        context_mode="evidence",
        model_identity="kimi-k3@openrouter",
        question_id=1,
        repetition=1,
    )

    assert first != second
    assert first == make_attempt_key(
        dataset_revision="rev",
        dialect="mysql",
        track="model-only",
        context_mode="evidence",
        model_identity="kimi-k3@openrouter",
        question_id=1,
        repetition=0,
    )
    auto = make_attempt_key(
        dataset_revision="rev",
        dialect="mysql",
        track="rdst-ask",
        context_mode="auto-init",
        model_identity="sonnet",
        interaction_mode="auto",
        question_id=1,
        repetition=0,
    )
    interactive = make_attempt_key(
        dataset_revision="rev",
        dialect="mysql",
        track="rdst-ask",
        context_mode="auto-init",
        model_identity="sonnet",
        interaction_mode="interactive-no-answer",
        question_id=1,
        repetition=0,
    )
    assert auto != interactive


def test_store_resumes_completed_attempts_and_ignores_partial_tail(tmp_path: Path):
    store = ArtifactStore(tmp_path / "run")
    store.initialize({"run_id": "run"})
    store.append_attempt({"attempt_key": "a", "outcome": "correct"})
    with open(store.attempts_path, "a", encoding="utf-8") as file_obj:
        file_obj.write('{"attempt_key":')

    assert store.completed_keys() == {"a"}
    assert store.pending_keys(["a", "b"]) == ["b"]

    store.append_attempt({"attempt_key": "b", "outcome": "correct"})

    assert store.completed_keys() == {"a", "b"}


def test_unscored_attempt_is_repaired_without_losing_history(tmp_path: Path):
    store = ArtifactStore(tmp_path / "run")
    store.initialize({"run_id": "run"})
    store.append_attempt(
        {
            "attempt_key": "logical",
            "invocation_id": "first",
            "outcome": "transport_error",
            "scored": False,
        }
    )

    assert store.completed_keys() == set()

    store.append_attempt(
        {
            "attempt_key": "logical",
            "invocation_id": "repair",
            "outcome": "correct",
            "scored": True,
        }
    )
    store.append_call_receipt(
        {"attempt_id": "repair", "model_name": "model", "actual_cost_usd": "0.1"}
    )

    assert store.completed_keys() == {"logical"}
    assert len(store.load_attempts()) == 2
    assert store.load_call_receipts()[0]["attempt_id"] == "repair"


def test_initialize_rejects_a_different_run(tmp_path: Path):
    store = ArtifactStore(tmp_path / "run")
    store.initialize({"run_id": "first"})

    try:
        store.initialize({"run_id": "second"})
    except ValueError as exc:
        assert "belongs to" in str(exc)
    else:
        raise AssertionError("Expected run identity mismatch")


def test_initialize_binds_interaction_source_and_scoring_policy(tmp_path: Path):
    store = ArtifactStore(tmp_path / "run")
    manifest = {
        "run_id": "run",
        "source_revision": "source-v1",
        "qualification_protocol": "protocol-v1",
        "clarification_policy": "deterministic-intent-stop-v1",
        "official_score_blocker": "hidden test cases",
        "simulator_answer_sources": ["public-label"],
        "execution_correctness_policy": "ordered-when-required",
        "derived_from": {"attempts_sha256": "a" * 64},
        "run_limits": {"max_provider_calls": 100},
    }
    store.initialize(manifest)

    for field in (
        "source_revision",
        "qualification_protocol",
        "clarification_policy",
        "official_score_blocker",
        "simulator_answer_sources",
        "execution_correctness_policy",
        "derived_from",
        "run_limits",
    ):
        changed = {**manifest, field: "changed"}
        with pytest.raises(ValueError, match=field):
            store.initialize(changed)
