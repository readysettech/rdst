from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from devtools.ask_benchmark import bird_interact
from devtools.ask_benchmark.artifacts import ArtifactStore
from devtools.ask_benchmark.bird_interact import (
    BirdInteractCase,
    FrozenExchange,
    FrozenTranscriptSimulator,
    PostgresConnectionConfig,
    PostgresQualificationExecutor,
    build_frozen_qualification_summary,
    load_frozen_qualification_cases,
    render_frozen_qualification_markdown,
    rescore_frozen_qualification,
)
from devtools.ask_benchmark.bird_interact_selection import (
    load_official_qualification_selection,
)
from devtools.ask_benchmark.models import QueryResult
from features.ask.events import AskClarificationNeededEvent, AskResultEvent
from features.ask.models import AskClarificationQuestion


def test_official_qualification_selection_is_frozen_and_valid():
    payload = load_official_qualification_selection()

    assert payload["source"]["record_count"] == 300
    assert payload["selection"]["category"] == "Query"
    assert payload["selection"]["eligible_count"] == 193
    assert [case["instance_id"] for case in payload["selection"]["cases"]] == [
        "robot_2",
        "crypto_3",
        "insider_8",
        "virtual_7",
        "credit_8",
        "cybermarket_8",
        "vaccine_4",
        "solar_1",
        "fake_1",
        "mental_10",
    ]


def _write_fixture(
    tmp_path: Path,
    *,
    sql: str = "SELECT value FROM metrics",
    order_sensitive: bool = False,
):
    public_path = tmp_path / "public.jsonl"
    examples_path = tmp_path / "examples.json"
    task = {
        "instance_id": "case_1",
        "selected_database": "fixture_db",
        "amb_user_query": "Show the quality score.",
        "user_query_ambiguity": {
            "critical_ambiguity": [{"term": "quality score", "sql_snippet": "value"}],
            "non_critical_ambiguity": (
                [
                    {
                        "term": "sorted by quality score",
                        "sql_snippet": "ORDER BY quality_score DESC",
                        "is_mask": False,
                        "type": "sort_ambiguity",
                    }
                ]
                if order_sensitive
                else []
            ),
        },
        "knowledge_ambiguity": [{"term": "Quality Index (QI)", "sql_snippet": "value"}],
    }
    public_path.write_text(json.dumps(task) + "\n", encoding="utf-8")
    question = "Should quality score use the Quality Index (QI)?"
    answer = "Yes, use the Quality Index definition."
    example = {
        "mode": "c-interact",
        "samples": [
            {
                "instance_id": "case_1",
                "database": "fixture_db",
                "phase1_passed": True,
                "adk_events": [
                    {
                        "type": "user_message",
                        "message": (
                            "User Query:\nShow the quality score.\n\n"
                            "You have 5 clarification turns."
                        ),
                    }
                ],
                "tool_trajectory": [
                    {
                        "tool": "ask_user",
                        "args": {"question": question},
                        "result": answer,
                    },
                    {
                        "tool": "submit_sql",
                        "args": {"sql": sql},
                        "result": "Phase 1 correct! (Reward: 0.7).",
                    },
                ],
            }
        ],
    }
    examples_path.write_text(json.dumps(example), encoding="utf-8")
    return public_path, examples_path


def _patch_hashes(monkeypatch, public_path: Path, examples_path: Path):
    monkeypatch.setattr(
        bird_interact,
        "BIRD_INTERACT_PUBLIC_SHA256",
        hashlib.sha256(public_path.read_bytes()).hexdigest(),
    )
    monkeypatch.setattr(
        bird_interact,
        "BIRD_INTERACT_EXAMPLES_SHA256",
        hashlib.sha256(examples_path.read_bytes()).hexdigest(),
    )


def test_load_frozen_qualification_case_validates_and_binds_sources(
    tmp_path: Path, monkeypatch
):
    public_path, examples_path = _write_fixture(tmp_path)
    _patch_hashes(monkeypatch, public_path, examples_path)

    cases = load_frozen_qualification_cases(
        public_path,
        examples_path,
        case_ids=("case_1",),
    )

    assert len(cases) == 1
    assert cases[0].reference_sql == "SELECT value FROM metrics"
    assert cases[0].max_clarification_turns == 5
    assert cases[0].order_sensitive is False
    assert cases[0].target_terms == ("quality score", "Quality Index (QI)")
    assert cases[0].exchanges[0].target_terms == (
        "Quality Index (QI)",
        "quality score",
    )


def test_load_frozen_qualification_rejects_non_read_only_reference(
    tmp_path: Path, monkeypatch
):
    public_path, examples_path = _write_fixture(
        tmp_path, sql="UPDATE metrics SET value = 1"
    )
    _patch_hashes(monkeypatch, public_path, examples_path)

    with pytest.raises(ValueError, match="not a read-only query"):
        load_frozen_qualification_cases(
            public_path,
            examples_path,
            case_ids=("case_1",),
        )


def test_load_frozen_qualification_derives_order_sensitivity(
    tmp_path: Path, monkeypatch
):
    public_path, examples_path = _write_fixture(tmp_path, order_sensitive=True)
    _patch_hashes(monkeypatch, public_path, examples_path)

    case = load_frozen_qualification_cases(
        public_path,
        examples_path,
        case_ids=("case_1",),
    )[0]

    assert case.order_sensitive is True
    assert case.target_terms == (
        "quality score",
        "sorted by quality score",
        "Quality Index (QI)",
    )
    assert case.exchanges[-1].source_kind == "public-non-critical-label"
    assert case.exchanges[-1].answer == (
        "Sort from highest to lowest in descending order."
    )


def test_frozen_simulator_answers_public_non_critical_sort_label(
    tmp_path: Path, monkeypatch
):
    public_path, examples_path = _write_fixture(tmp_path, order_sensitive=True)
    _patch_hashes(monkeypatch, public_path, examples_path)
    case = load_frozen_qualification_cases(
        public_path,
        examples_path,
        case_ids=("case_1",),
    )[0]

    answer = FrozenTranscriptSimulator(case).answer(
        (
            "Should the results be sorted from highest to lowest or lowest to "
            "highest by quality score?"
        ),
        [],
    )

    assert answer is not None
    assert answer["answer"] == "Sort from highest to lowest in descending order."
    assert answer["source_kind"] == "public-non-critical-label"
    assert answer["matched_target_terms"] == ["sorted by quality score"]


def test_frozen_simulator_replays_only_relevant_unused_answer():
    case = BirdInteractCase(
        instance_id="case_1",
        database="fixture_db",
        ambiguous_query="Show quality.",
        reference_sql="SELECT value FROM metrics",
        target_terms=("quality score", "Quality Index (QI)"),
        exchanges=(
            FrozenExchange(
                question="Should quality score use Quality Index (QI)?",
                answer="Use QI.",
                target_terms=("quality score", "Quality Index (QI)"),
            ),
        ),
        max_clarification_turns=5,
        order_sensitive=False,
    )
    simulator = FrozenTranscriptSimulator(case)

    matched = simulator.answer("What defines the quality score?", ["QI", "raw"])

    assert matched is not None
    assert matched["answer"] == "Use QI."
    assert matched["matched_target_terms"] == ["Quality Index (QI)", "quality score"]
    assert simulator.answer("What defines the quality score?", []) is None
    assert (
        FrozenTranscriptSimulator(case).answer("Which table should I use?", []) is None
    )


def test_postgres_ask_executor_maps_bounds(monkeypatch):
    executor = PostgresQualificationExecutor(PostgresConnectionConfig())
    monkeypatch.setattr(
        executor,
        "execute",
        lambda sql, db_id: QueryResult(
            error="timed out",
            timed_out=True,
        ),
    )

    result = executor.as_ask_executor("fixture_db")("SELECT 1", {})

    assert result["success"] is False
    assert result["error_kind"] == "timeout"


def test_summary_reports_qualification_not_publication():
    attempts = [
        {
            "instance_id": "case_1",
            "scored": True,
            "execution_correct": True,
            "latency_ms": 1200,
            "answered_turns": 1,
            "target_term_count": 2,
            "covered_target_term_count": 1,
            "clarification_questions": [{"matched": True}],
            "outcome": "correct",
        }
    ]
    calls = [
        {
            "stage": "clarify",
            "normalized_cold_cost_usd": "0.01",
        }
    ]

    summary = build_frozen_qualification_summary(attempts, calls, expected_cases=1)
    markdown = render_frozen_qualification_markdown(summary)

    assert summary["publication_status"] == "qualification-only-not-official"
    assert summary["execution_accuracy"] == 1.0
    assert summary["target_coverage"] == 0.5
    assert "not the official c-Interact evaluator" in markdown


def test_run_case_uses_canonical_ask_and_resume(monkeypatch):
    captured = {}

    class FakeService:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def ask(self, ask_input, options):
            captured["ask_input"] = ask_input
            captured["options"] = options
            yield AskClarificationNeededEvent(
                type="clarification_needed",
                session_id="session-1",
                interpretations=[],
                questions=[
                    AskClarificationQuestion(
                        id="metric",
                        question="What defines the quality score?",
                        options=["Quality Index (QI)", "raw value"],
                    )
                ],
            )

        async def resume(self, session_id, answers):
            captured["resume"] = (session_id, answers)
            yield AskResultEvent(
                type="result",
                success=True,
                sql="SELECT value FROM metrics",
                rows=[[1]],
                columns=["value"],
                row_count=1,
                execution_time_ms=1,
                llm_calls=3,
                total_tokens=10,
            )

        def abandon(self, session_id):
            raise AssertionError(f"unexpected abandon: {session_id}")

    class FakeExecutor:
        bounds = SimpleNamespace(timeout_seconds=30)
        config = SimpleNamespace(host="h", port=1, user="u", password="p")

        def as_ask_executor(self, db_id):
            return lambda sql, config: {
                "success": True,
                "rows": [[1]],
                "columns": ["value"],
            }

        def execute(self, sql, *, db_id):
            return QueryResult(columns=("value",), rows=((1,),))

    case = BirdInteractCase(
        instance_id="case_1",
        database="fixture_db",
        ambiguous_query="Show the quality score.",
        reference_sql="SELECT value FROM metrics",
        target_terms=("quality score", "Quality Index (QI)"),
        exchanges=(
            FrozenExchange(
                question="Should quality score use Quality Index (QI)?",
                answer="Use Quality Index (QI).",
                target_terms=("quality score", "Quality Index (QI)"),
            ),
        ),
        max_clarification_turns=5,
        order_sensitive=False,
    )
    monkeypatch.setattr(bird_interact, "AskService", FakeService)

    result = bird_interact._run_case(
        run_id="run",
        attempt_id="attempt",
        attempt_key="key",
        case=case,
        adapter=object(),
        executor=FakeExecutor(),
        reference=QueryResult(columns=("value",), rows=((1,),)),
        product_max_rows=100,
    )

    assert result["execution_correct"] is True
    assert captured["ask_input"].question == case.ambiguous_query
    assert captured["options"].no_interactive is False
    assert captured["options"].enforce_result_limit is True
    assert captured["resume"][0] == "session-1"
    assert captured["resume"][1] == {"metric": "Use Quality Index (QI)."}


def test_rescore_uses_order_for_labeled_sort_task(tmp_path: Path, monkeypatch):
    public_path, examples_path = _write_fixture(tmp_path, order_sensitive=True)
    _patch_hashes(monkeypatch, public_path, examples_path)
    case = load_frozen_qualification_cases(
        public_path,
        examples_path,
        case_ids=("case_1",),
    )[0]
    source = ArtifactStore(tmp_path / "source")
    source.initialize(
        {
            "artifact_schema_version": 1,
            "run_id": "source",
            "dataset_revision": bird_interact.BIRD_INTERACT_PUBLIC_REVISION,
            "source_revision": bird_interact.BIRD_INTERACT_SOURCE_REVISION,
            "public_data_sha256": bird_interact.BIRD_INTERACT_PUBLIC_SHA256,
            "frozen_examples_sha256": bird_interact.BIRD_INTERACT_EXAMPLES_SHA256,
            "qualification_protocol": "frozen-example-transcript-replay-v2",
            "official_score": False,
            "case_ids": ["case_1"],
        }
    )
    source.append_attempt(
        {
            "attempt_key": "source-key",
            "invocation_id": "attempt-1",
            "instance_id": "case_1",
            "scored": True,
            "execution_correct": True,
            "multiset_correct": True,
            "ordered_correct": False,
            "outcome": "correct",
            "clarification_questions": [],
            "target_term_count": 2,
            "covered_target_term_count": 0,
        }
    )
    source.append_call_receipt(
        {
            "attempt_id": "attempt-1",
            "stage": "sql_generation",
            "normalized_cold_cost_usd": "0.01",
        }
    )

    summary = rescore_frozen_qualification(
        source_run_dir=source.run_dir,
        output_dir=tmp_path / "derived",
        run_id="derived",
        cases=[case],
        protocol_sha256="protocol",
        rdst_revision="revision",
        rdst_dirty=True,
        rdst_diff_sha256="diff",
    )

    assert summary["execution_accuracy"] == 0.0
    assert summary["attempts"][0]["source_execution_correct"] is True
    assert summary["attempts"][0]["order_sensitive"] is True
    assert summary["derived_from"]["new_model_calls"] == 0
