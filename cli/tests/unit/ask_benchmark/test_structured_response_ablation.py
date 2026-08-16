from devtools.ask_benchmark.models import BenchmarkCase, QueryResult
from devtools.ask_benchmark.structured_response_ablation import (
    STRUCTURED_RESPONSE_CELLS,
    _run_cell,
    _summarize,
)
from features.ask.prompts.ask_prompts import SQL_GENERATION_SYSTEM_PROMPT


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


def test_plain_response_cell_uses_expert_system_and_no_oracle_evidence() -> None:
    class Adapter:
        def query(self, **kwargs):
            assert kwargs["system_message"] == SQL_GENERATION_SYSTEM_PROMPT
            assert "Return one" in kwargs["user_query"]
            assert "unused oracle evidence" not in kwargs["user_query"]
            assert kwargs["purpose"] == "ablation_plain_sql_response"
            assert kwargs.get("extra") is None
            return {"text": "SELECT 1"}

    class Executor:
        def execute(self, sql, *, db_id):
            assert sql == "SELECT 1"
            assert db_id == "fixture"
            return QueryResult(columns=("value",), rows=((1,),))

    result = _run_cell(
        case=_case(),
        schema="Table: fixture",
        cell="plain-sql-response",
        adapter=Adapter(),
        executor=Executor(),
        gold=QueryResult(columns=("value",), rows=((1,),)),
    )

    assert result["outcome"] == "correct"


def test_response_ablation_summary_reports_paired_flips() -> None:
    attempts = []
    correctness = {
        "plain-sql-response": {1: True, 2: False},
        "structured-json-response": {1: False, 2: True},
    }
    for cell in STRUCTURED_RESPONSE_CELLS:
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
            "run_id": "response-v1",
            "source_run_id": "source",
            "case_ids": [1, 2],
        },
        attempts,
    )

    assert [cell["cell"] for cell in summary["cells"]] == list(
        STRUCTURED_RESPONSE_CELLS
    )
    assert summary["paired_comparison"]["accuracy_delta"] == 0.0
    assert summary["paired_comparison"]["baseline_only_correct_ids"] == [2]
    assert summary["paired_comparison"]["treatment_only_correct_ids"] == [1]
