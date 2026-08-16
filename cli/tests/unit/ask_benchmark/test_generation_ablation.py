import pytest

from devtools.ask_benchmark.bird_dataset import DATASET_REVISION
from devtools.ask_benchmark.generation_ablation import (
    ABLATION_CELLS,
    _run_cell,
    _summarize,
    _validate_source,
)
from devtools.ask_benchmark.models import BenchmarkCase, QueryResult


def _case() -> BenchmarkCase:
    return BenchmarkCase(
        question_id=11,
        db_id="fixture",
        question="Return one",
        evidence="unused oracle evidence",
        gold_sql="SELECT 1",
        difficulty="simple",
        dialect="mysql",
    )


def test_generation_ablation_source_requires_one_canonical_smoke_attempt() -> None:
    manifest = {
        "track": "rdst-ask",
        "dataset_revision": DATASET_REVISION,
        "context_mode": "llm-enriched",
        "interaction_mode": "auto",
        "suite": "smoke",
        "case_ids": [11],
    }
    attempts = [
        {
            "question_id": 11,
            "scored": True,
            "diagnostics": {"filtered_tables": ["fixture_table"]},
        }
    ]

    _validate_source(manifest, attempts, [_case()])

    attempts.append(attempts[0].copy())
    with pytest.raises(ValueError, match="exactly one scored attempt"):
        _validate_source(manifest, attempts, [_case()])


def test_generation_ablation_summary_keeps_cells_separate() -> None:
    attempts = []
    for index, cell in enumerate(ABLATION_CELLS):
        attempts.append(
            {
                "cell": cell,
                "question_id": 11,
                "scored": True,
                "execution_correct": index % 2 == 0,
                "latency_ms": 1000,
                "output_tokens": 10 + index,
                "normalized_cold_cost_usd": "0.01",
            }
        )

    summary = _summarize(
        {"run_id": "ablation-v1", "source_run_id": "source", "case_ids": [11]},
        attempts,
    )

    assert [cell["cell"] for cell in summary["cells"]] == list(ABLATION_CELLS)
    assert [cell["accuracy"] for cell in summary["cells"]] == [1.0, 0.0, 1.0, 0.0]
    assert [contrast["accuracy_delta"] for contrast in summary["contrasts"]] == [
        -1.0,
        -1.0,
        0.0,
        0.0,
    ]


def test_sql_only_cell_uses_original_question_and_ablation_stage() -> None:
    class Adapter:
        def query(self, **kwargs):
            assert "Return one" in kwargs["user_query"]
            assert "unused oracle evidence" not in kwargs["user_query"]
            assert kwargs["purpose"] == "ablation_filtered_sql_only"
            return {"text": "SELECT 1"}

    class Executor:
        def execute(self, sql, *, db_id):
            assert sql == "SELECT 1"
            assert db_id == "fixture"
            return QueryResult(columns=("value",), rows=((1,),))

    result = _run_cell(
        case=_case(),
        schema="Table: fixture_table\n  value (int)",
        schema_scope="filtered",
        contract="sql-only",
        adapter=Adapter(),
        executor=Executor(),
        gold=QueryResult(columns=("value",), rows=((1,),)),
    )

    assert result["outcome"] == "correct"
    assert result["execution_correct"] is True
