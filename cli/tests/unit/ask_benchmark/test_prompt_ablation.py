from devtools.ask_benchmark.models import BenchmarkCase, QueryResult
from devtools.ask_benchmark.prompt_ablation import (
    PROMPT_ABLATION_CELLS,
    _render_summary,
    _run_cell,
    _summarize,
)
from features.ask.prompts.ask_prompts import (
    ENUM_GROUNDING_RULES,
    SQL_GENERATION_SYSTEM_PROMPT,
)


def _case() -> BenchmarkCase:
    return BenchmarkCase(
        question_id=72,
        db_id="fixture",
        question="Return one",
        evidence="unused oracle evidence",
        gold_sql="SELECT 1",
        difficulty="simple",
        dialect="mysql",
    )


def test_prompt_cells_use_same_expert_system_and_no_oracle_evidence() -> None:
    prompts = {}

    class Adapter:
        def query(self, **kwargs):
            assert kwargs["system_message"] == SQL_GENERATION_SYSTEM_PROMPT
            assert "Return one" in kwargs["user_query"]
            assert "unused oracle evidence" not in kwargs["user_query"]
            prompts[kwargs["purpose"]] = kwargs["user_query"]
            return {"text": "SELECT 1"}

    class Executor:
        def execute(self, sql, *, db_id):
            assert sql == "SELECT 1"
            assert db_id == "fixture"
            return QueryResult(columns=("value",), rows=((1,),))

    for cell in PROMPT_ABLATION_CELLS:
        result = _run_cell(
            case=_case(),
            schema="Table: fixture",
            cell=cell,
            adapter=Adapter(),
            executor=Executor(),
            gold=QueryResult(columns=("value",), rows=((1,),)),
        )
        assert result["outcome"] == "correct"

    assert set(prompts) == {
        "ablation_lean_direct_prompt",
        "ablation_detailed_product_prompt",
    }
    assert (
        prompts["ablation_lean_direct_prompt"]
        != prompts["ablation_detailed_product_prompt"]
    )


def test_prompt_ablation_summary_reports_paired_flips() -> None:
    attempts = []
    correctness = {
        "lean-direct-prompt": {1: True, 2: False},
        "detailed-product-prompt": {1: False, 2: True},
    }
    for cell in PROMPT_ABLATION_CELLS:
        for question_id, correct in correctness[cell].items():
            attempts.append(
                {
                    "cell": cell,
                    "question_id": question_id,
                    "scored": True,
                    "execution_correct": correct,
                    "latency_ms": 1000,
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "normalized_cold_cost_usd": "0.01",
                }
            )

    summary = _summarize(
        {
            "run_id": "prompt-v1",
            "source_run_id": "source",
            "case_ids": [1, 2],
        },
        attempts,
    )

    assert [cell["cell"] for cell in summary["cells"]] == list(PROMPT_ABLATION_CELLS)
    assert summary["paired_comparison"]["accuracy_delta"] == 0.0
    assert summary["paired_comparison"]["baseline_only_correct_ids"] == [2]
    assert summary["paired_comparison"]["treatment_only_correct_ids"] == [1]


def test_enum_grounding_cell_adds_only_grounding_rules() -> None:
    prompts = {}

    class Adapter:
        def query(self, **kwargs):
            prompts[kwargs["purpose"]] = kwargs["user_query"]
            return {"text": "SELECT 1"}

    class Executor:
        def execute(self, sql, *, db_id):
            return QueryResult(columns=("value",), rows=((1,),))

    for cell in ("detailed-product-prompt", "enum-grounded-product-prompt"):
        result = _run_cell(
            case=_case(),
            schema="Table: fixture",
            cell=cell,
            adapter=Adapter(),
            executor=Executor(),
            gold=QueryResult(columns=("value",), rows=((1,),)),
        )
        assert result["outcome"] == "correct"

    baseline = prompts["ablation_detailed_product_prompt"]
    treatment = prompts["ablation_enum_grounded_product_prompt"]
    assert ENUM_GROUNDING_RULES not in baseline
    assert treatment == baseline.replace(
        "Requirements:\n", f"Requirements:\n{ENUM_GROUNDING_RULES}\n"
    )


def test_summary_uses_actual_cell_names_for_flip_labels() -> None:
    rendered = _render_summary(
        {
            "cells": [],
            "paired_comparison": {
                "baseline": "detailed-product-prompt",
                "treatment": "enum-grounded-product-prompt",
                "delta_label": "enum-grounding",
                "accuracy_delta": 0.0,
                "baseline_only_correct_ids": [1460],
                "treatment_only_correct_ids": [72],
            },
        }
    )

    assert "detailed-product-prompt only correct: [1460]" in rendered
    assert "enum-grounded-product-prompt only correct: [72]" in rendered
    assert "Lean-only" not in rendered
