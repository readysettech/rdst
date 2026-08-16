from unittest.mock import patch

import pytest

from devtools.ask_benchmark.models import BenchmarkCase, QueryResult
from devtools.ask_benchmark.structured_system_ablation import (
    LEGACY_GENERIC_SYSTEM_PROMPT,
    STRUCTURED_SYSTEM_CELLS,
    _run_cell,
    _summarize,
    _system_message,
)
from features.ask.prompts.ask_prompts import SQL_GENERATION_SYSTEM_PROMPT


def _case() -> BenchmarkCase:
    return BenchmarkCase(
        question_id=72,
        db_id="fixture",
        question="Return one",
        evidence="unused",
        gold_sql="SELECT 1",
        difficulty="simple",
        dialect="mysql",
    )


def test_system_ablation_cells_map_to_exact_messages() -> None:
    assert _system_message("generic-system") == LEGACY_GENERIC_SYSTEM_PROMPT
    assert _system_message("expert-sql-system") == SQL_GENERATION_SYSTEM_PROMPT
    with pytest.raises(ValueError, match="Unknown structured-system"):
        _system_message("other")


def test_system_ablation_summary_reports_paired_flips() -> None:
    attempts = [
        {
            "cell": "generic-system",
            "question_id": 1,
            "scored": True,
            "execution_correct": True,
            "latency_ms": 1000,
            "input_tokens": 100,
            "output_tokens": 20,
            "normalized_cold_cost_usd": "0.01",
        },
        {
            "cell": "expert-sql-system",
            "question_id": 1,
            "scored": True,
            "execution_correct": False,
            "latency_ms": 1000,
            "input_tokens": 100,
            "output_tokens": 20,
            "normalized_cold_cost_usd": "0.01",
        },
        {
            "cell": "generic-system",
            "question_id": 2,
            "scored": True,
            "execution_correct": False,
            "latency_ms": 1000,
            "input_tokens": 100,
            "output_tokens": 20,
            "normalized_cold_cost_usd": "0.01",
        },
        {
            "cell": "expert-sql-system",
            "question_id": 2,
            "scored": True,
            "execution_correct": True,
            "latency_ms": 1000,
            "input_tokens": 100,
            "output_tokens": 20,
            "normalized_cold_cost_usd": "0.01",
        },
    ]

    summary = _summarize(
        {"run_id": "system-v1", "source_run_id": "source", "case_ids": [1, 2]},
        attempts,
    )

    assert [cell["cell"] for cell in summary["cells"]] == list(STRUCTURED_SYSTEM_CELLS)
    assert summary["paired_comparison"]["accuracy_delta"] == 0.0
    assert summary["paired_comparison"]["baseline_only_correct_ids"] == [1]
    assert summary["paired_comparison"]["treatment_only_correct_ids"] == [2]


def test_system_ablation_cell_forwards_only_selected_system_message() -> None:
    class Executor:
        def execute(self, sql, *, db_id):
            assert sql == "SELECT 1"
            assert db_id == "fixture"
            return QueryResult(columns=("value",), rows=((1,),))

    with patch(
        "devtools.ask_benchmark.structured_system_ablation.generate_sql_from_nl",
        return_value={"success": True, "sql": "SELECT 1"},
    ) as generate:
        result = _run_cell(
            case=_case(),
            schema="Table: fixture",
            cell="expert-sql-system",
            adapter=object(),
            executor=Executor(),
            gold=QueryResult(columns=("value",), rows=((1,),)),
        )

    assert result["execution_correct"] is True
    assert generate.call_args.kwargs["system_message"] == SQL_GENERATION_SYSTEM_PROMPT
    assert generate.call_args.kwargs["purpose"] == (
        "ablation_structured_expert_sql_system"
    )
