from pathlib import Path

import pytest

from devtools.ask_benchmark.dashboard import DashboardError, write_dashboard


def _model(name: str, accuracy: float, cost: str, **overrides):
    model = {
        "model_name": name,
        "reasoning_label": "max",
        "attempt_count": 5,
        "scored_attempt_count": 5,
        "unscored_attempt_count": 0,
        "correct_count": int(accuracy * 5),
        "coverage": 1.0,
        "execution_accuracy": accuracy,
        "accuracy_ci_95": [max(accuracy - 0.3, 0.0), min(accuracy + 0.3, 1.0)],
        "silent_wrong_rate": 1.0 - accuracy,
        "outcome_counts": {
            "correct": int(accuracy * 5),
            "incorrect_result": 5 - int(accuracy * 5),
        },
        "accuracy_by_difficulty": {
            "simple": {
                "attempt_count": 3,
                "execution_accuracy": accuracy,
                "accuracy_ci_95": [0.0, 1.0],
            }
        },
        "accuracy_by_database": {
            "california_schools": {
                "attempt_count": 5,
                "execution_accuracy": accuracy,
                "accuracy_ci_95": [0.0, 1.0],
            }
        },
        "mean_latency_ms": 4_000.0,
        "median_latency_ms": 3_500.0,
        "p95_latency_ms": 9_000.0,
        "mean_model_call_latency_ms": 3_900.0,
        "tasks_per_minute": 15.0,
        "input_tokens": 20_000,
        "output_tokens": 2_000,
        "reasoning_tokens": 1_500,
        "cached_input_tokens": 500,
        "cost_by_stage": {
            "model_only_generation": {
                "call_count": 5,
                "normalized_cold_cost_usd": cost,
            }
        },
        "actual_cost_usd": cost,
        "expected_billed_cost_usd": cost,
        "billing_variance_usd": "0",
        "repair_actual_cost_usd": "0",
        "repair_rate": 0.0,
        "cost_per_question_usd": cost,
        "cost_per_correct_usd": cost if accuracy else None,
        "billed_cost_per_question_usd": cost,
        "billed_cost_per_correct_usd": cost if accuracy else None,
        "cost_efficiency_eligible": False,
        "cost_efficiency_winner": False,
        "pareto_optimal": False,
    }
    model.update(overrides)
    return model


def _summary(models, **board_overrides):
    board = {
        "track": "model-only",
        "context_mode": "evidence",
        "transport": "openrouter",
        "expected_attempts_per_model": 5,
        "models": models,
        "paired_comparisons": [],
        "winner_statistically_unstable": False,
    }
    board.update(board_overrides)
    return {
        "attempt_count": 5 * len(models),
        "benchmark_elapsed_ms": 120_000.0,
        "leaderboards": [board],
        "source_runs": ["run-a", "run-b"],
    }


def test_write_dashboard_renders_all_sections(tmp_path: Path):
    summary = _summary(
        [
            _model(
                "model-a",
                0.6,
                "0.02",
                pareto_optimal=True,
                cost_efficiency_eligible=True,
                cost_efficiency_winner=True,
            ),
            _model("model-b", 0.4, "0.01", pareto_optimal=True),
            _model("model-c-historical", 0.4, "0.05"),
        ],
        winner_statistically_unstable=True,
    )

    path = write_dashboard(summary, tmp_path / "summary.html")
    document = path.read_text(encoding="utf-8")

    assert document.startswith("<!DOCTYPE html>")
    for section_id in (
        "hero",
        "leaderboard",
        "latency",
        "billing",
        "breakdown",
        "failures",
        "tokens",
        "reasoning",
        "methodology",
    ):
        assert f'id="{section_id}"' in document
    assert "Cost-efficient winner" in document
    assert "ordering uncertain" in document
    assert 'class="badge hist"' in document
    assert "run-a" in document
    assert "<script src=" not in document
    assert "<link rel=" not in document


def test_write_dashboard_escapes_untrusted_names(tmp_path: Path):
    hostile = "evil<script>alert(1)</script>"
    summary = _summary([_model(hostile, 0.6, "0.02")])
    summary["leaderboards"][0]["models"][0]["accuracy_by_database"] = {
        "db<img src=x>": {
            "attempt_count": 5,
            "execution_accuracy": 0.5,
            "accuracy_ci_95": [0.0, 1.0],
        }
    }

    document = write_dashboard(summary, tmp_path / "summary.html").read_text(
        encoding="utf-8"
    )

    assert "<script>alert(1)" not in document
    assert "<img src=x>" not in document
    assert "evil&lt;script&gt;" in document


def test_write_dashboard_scales_to_many_models(tmp_path: Path):
    models = [
        _model(f"model-{index:02d}", 0.2 + (index % 5) * 0.1, str(0.001 * (index + 1)))
        for index in range(16)
    ]
    document = write_dashboard(_summary(models), tmp_path / "summary.html").read_text(
        encoding="utf-8"
    )

    for model in models:
        assert model["model_name"] in document


def test_write_dashboard_requires_single_leaderboard(tmp_path: Path):
    summary = _summary([_model("model-a", 0.6, "0.02")])
    summary["leaderboards"].append(dict(summary["leaderboards"][0]))

    with pytest.raises(DashboardError, match="one compatible"):
        write_dashboard(summary, tmp_path / "summary.html")


def test_write_dashboard_requires_models(tmp_path: Path):
    summary = _summary([_model("model-a", 0.6, "0.02")])
    summary["leaderboards"][0]["models"] = []

    with pytest.raises(DashboardError, match="no models"):
        write_dashboard(summary, tmp_path / "summary.html")


@pytest.mark.parametrize("cost", [None, "0"])
def test_write_dashboard_requires_positive_task_costs(tmp_path: Path, cost):
    summary = _summary([_model("model-a", 0.6, "0.02")])
    summary["leaderboards"][0]["models"][0]["cost_per_question_usd"] = cost

    with pytest.raises(DashboardError, match="standardized task costs"):
        write_dashboard(summary, tmp_path / "summary.html")
