from devtools.ask_benchmark.report import build_summary, render_markdown


def _attempts(model: str, correct: int, count: int, cost: str):
    return [
        {
            "attempt_key": f"{model}-{index}",
            "question_id": index,
            "repetition": 0,
            "track": "model-only",
            "context_mode": "evidence",
            "transport": "openrouter",
            "model_name": model,
            "db_id": "fixture",
            "difficulty": "simple",
            "outcome": "correct" if index < correct else "incorrect_result",
            "execution_correct": index < correct,
            "soft_f1": 1.0 if index < correct else 0.0,
            "actual_cost_usd": cost,
            "normalized_cold_cost_usd": cost,
        }
        for index in range(count)
    ]


def test_cheapest_model_within_two_accuracy_points_wins():
    attempts = (
        _attempts("accurate", 90, 100, "0.10")
        + _attempts("efficient", 88, 100, "0.01")
        + _attempts("too-inaccurate", 87, 100, "0.001")
        + _attempts("incomplete", 99, 99, "0.001")
    )

    summary = build_summary(attempts, expected_attempts_per_model=100)
    models = {
        model["model_name"]: model for model in summary["leaderboards"][0]["models"]
    }

    assert models["accurate"]["cost_efficiency_eligible"] is True
    assert models["efficient"]["cost_efficiency_eligible"] is True
    assert models["efficient"]["cost_efficiency_winner"] is True
    assert models["too-inaccurate"]["cost_efficiency_eligible"] is False
    assert models["incomplete"]["cost_efficiency_eligible"] is False
    difficulty = models["accurate"]["accuracy_by_difficulty"]["simple"]
    assert difficulty["attempt_count"] == 100
    assert difficulty["execution_accuracy"] == 0.9
    assert difficulty["accuracy_ci_95"][0] < 0.9 < difficulty["accuracy_ci_95"][1]
    assert models["accurate"]["silent_wrong_rate"] == 0.1


def test_internal_headline_excludes_declared_unstable_gold_cases():
    attempts = _attempts("model", 1, 2, "0.1")
    attempts[0]["question_id"] = 671
    attempts[0]["diagnostics"] = {"headline_eligible": False}
    attempts[1]["question_id"] = 1
    attempts[1]["diagnostics"] = {"headline_eligible": True}

    summary = build_summary(attempts, expected_attempts_per_model=2)
    model = summary["leaderboards"][0]["models"][0]

    assert summary["headline_excluded_case_ids"] == [671]
    assert model["official_execution_accuracy"] == 0.5
    assert model["execution_accuracy"] == 0.0
    assert model["headline_attempt_count"] == 1
    assert model["headline_execution_accuracy"] == 0.0
    assert model["cost_per_correct_usd"] is None
    assert model["official_cost_per_correct_usd"] == "0.2"
    assert model["cost_per_question_usd"] == "0.1"
    assert "excludes unstable gold cases: 671" in render_markdown(summary)


def test_markdown_makes_a_budget_stop_explicit():
    summary = build_summary(
        _attempts("model", 1, 1, "0.1"), expected_attempts_per_model=2
    )
    summary["run_stop"] = {
        "kind": "budget",
        "reason": "provider-call limit reached (1/1)",
    }

    markdown = render_markdown(summary)

    assert "Run stopped before complete coverage" in markdown
    assert "provider-call limit reached (1/1)" in markdown


def test_headline_uses_cold_cost_without_mixing_billed_cost():
    attempts = _attempts("cold-cheap", 1, 1, "10") + _attempts(
        "billed-cheap", 1, 1, "0.01"
    )
    attempts[0]["normalized_cold_cost_usd"] = "0.01"
    attempts[1]["normalized_cold_cost_usd"] = "1"

    summary = build_summary(attempts, expected_attempts_per_model=1)
    models = {
        model["model_name"]: model for model in summary["leaderboards"][0]["models"]
    }

    assert models["cold-cheap"]["cost_efficiency_winner"] is True
    assert models["cold-cheap"]["cost_per_question_usd"] == "0.01"
    assert models["cold-cheap"]["billed_cost_per_question_usd"] == "10"


def test_unscored_transport_attempt_blocks_coverage_until_repaired():
    attempts = _attempts("model", 1, 1, "0.1")
    attempts[0].update(
        {
            "scored": False,
            "outcome": "transport_error",
            "execution_correct": False,
        }
    )

    incomplete = build_summary(attempts, expected_attempts_per_model=1)
    incomplete_model = incomplete["leaderboards"][0]["models"][0]
    assert incomplete_model["coverage"] == 0
    assert incomplete_model["unscored_attempt_count"] == 1
    assert incomplete_model["cost_efficiency_eligible"] is False

    repair = _attempts("model", 1, 1, "0.2")[0]
    repair["attempt_key"] = attempts[0]["attempt_key"]
    repair["scored"] = True
    repaired = build_summary(attempts + [repair], expected_attempts_per_model=1)
    repaired_model = repaired["leaderboards"][0]["models"][0]
    assert repaired_model["coverage"] == 1
    assert repaired_model["execution_accuracy"] == 1
    assert repaired_model["normalized_cold_cost_usd"] == "0.2"
    assert repaired_model["repaired_pair_count"] == 1
    assert repaired_model["repair_rate"] == 1
    assert repaired_model["repair_normalized_cold_cost_usd"] == "0.1"


def test_orphan_call_receipts_are_counted_as_repair_spend():
    attempts = _attempts("model", 1, 1, "0.2")
    attempts[0]["invocation_id"] = "scored"
    receipts = [
        {
            "attempt_id": "scored",
            "model_name": "model",
            "actual_cost_usd": "0.2",
            "normalized_cold_cost_usd": "0.2",
            "expected_billed_cost_usd": "0.2",
        },
        {
            "attempt_id": "orphan",
            "model_name": "model",
            "actual_cost_usd": "0.1",
            "normalized_cold_cost_usd": "0.1",
            "expected_billed_cost_usd": "0.1",
        },
    ]

    summary = build_summary(
        attempts, expected_attempts_per_model=1, call_receipts=receipts
    )
    model = summary["leaderboards"][0]["models"][0]

    assert model["repair_invocation_count"] == 1
    assert model["orphan_receipt_invocation_count"] == 1
    assert model["repair_actual_cost_usd"] == "0.1"
    assert model["repair_normalized_cold_cost_usd"] == "0.1"


def test_paired_bootstrap_uses_only_shared_repetitions():
    first = _attempts("first", 1, 2, "0.1")
    first[0]["question_id"] = 1
    first[0]["repetition"] = 0
    first[1]["question_id"] = 1
    first[1]["repetition"] = 1
    first[1]["execution_correct"] = False
    second = _attempts("second", 1, 1, "0.1")
    second[0]["question_id"] = 1
    second[0]["repetition"] = 1

    summary = build_summary(first + second, expected_attempts_per_model=2)
    comparison = summary["leaderboards"][0]["paired_comparisons"][0]

    assert comparison["shared_attempt_count"] == 1
    assert comparison["shared_question_count"] == 1
    assert comparison["accuracy_delta"] == -1
    assert comparison["accuracy_delta_ci_95"] == [-1.0, -1.0]


def test_report_includes_benchmark_and_model_speed():
    attempts = _attempts("model", 2, 2, "0.1")
    attempts[0]["latency_ms"] = 1_000
    attempts[1]["latency_ms"] = 3_000

    summary = build_summary(
        attempts,
        expected_attempts_per_model=2,
        benchmark_elapsed_ms=5_000,
    )
    model = summary["leaderboards"][0]["models"][0]
    markdown = render_markdown(summary)

    assert model["mean_latency_ms"] == 2_000
    assert model["median_latency_ms"] == 2_000
    assert model["p95_latency_ms"] == 3_000
    assert model["total_latency_ms"] == 4_000
    assert model["tasks_per_minute"] == 30
    assert "Benchmark wall time: **0.08 minutes**" in markdown
    assert "Mean/task" in markdown


def test_report_uses_normalized_cost_when_billed_cost_is_missing():
    attempts = _attempts("model", 1, 1, "0.1")
    attempts[0]["actual_cost_usd"] = None

    summary = build_summary(attempts, expected_attempts_per_model=1)
    model = summary["leaderboards"][0]["models"][0]
    markdown = render_markdown(summary)

    assert model["cost_basis"] == "normalized_cold"
    assert model["cost_per_correct_usd"] == "0.1"
    assert model["cost_per_question_usd"] == "0.1"
    assert "Cold/task" in markdown
    assert model["accuracy_ci_95"][0] < 1.0
    assert model["accuracy_ci_95"][1] == 1.0
    assert "Cost-efficient winner: **model**" in markdown


def test_partial_ranked_resolution_is_not_reported_as_an_injection():
    attempts = _attempts("model", 0, 1, "0.1")
    attempts[0]["diagnostics"] = {
        "clarification_resolutions": [
            {"action": "select", "applied": False},
            {"action": "abstain", "applied": False},
        ]
    }

    summary = build_summary(attempts, expected_attempts_per_model=1)
    model = summary["leaderboards"][0]["models"][0]

    assert model["auto_injection_attempt_count"] == 0
    assert model["end_to_end_ex_given_auto_injection"] is None


def test_report_separates_detector_recommendations_from_actual_stops():
    attempts = _attempts("model", 0, 2, "0.1")
    for attempt in attempts:
        attempt["diagnostics"] = {
            "ambiguity_report": {
                "ambiguities": [{"id": "status"}],
                "requires_clarification": True,
            }
        }
    attempts[1]["outcome"] = "clarification_required"

    summary = build_summary(attempts, expected_attempts_per_model=2)
    model = summary["leaderboards"][0]["models"][0]
    markdown = render_markdown(summary)

    assert model["detector_clarification_recommended_attempt_count"] == 2
    assert model["clarification_required_attempt_count"] == 1
    assert "Detector recommended clarification" in markdown
    assert "Actual clarification stops" in markdown


def test_report_exposes_filter_bypass_and_model_stage_latency() -> None:
    attempts = _attempts("model", 0, 1, "0.1")
    attempts[0]["diagnostics"] = {
        "schema_filter_strategy": "full-schema-below-budget",
        "ambiguity_report": {
            "ambiguities": [{"id": "status"}],
            "requires_clarification": True,
        },
    }
    attempts[0]["call_records"] = [
        {
            "stage": "clarification",
            "latency_ms": 2_500,
            "input_tokens": 1_200,
            "output_tokens": 140,
            "normalized_cold_cost_usd": "0.01",
            "expected_billed_cost_usd": "0.01",
            "actual_cost_usd": "0.01",
        }
    ]

    summary = build_summary(attempts, expected_attempts_per_model=1)
    model = summary["leaderboards"][0]["models"][0]
    stage = model["cost_by_stage"]["clarification"]
    markdown = render_markdown(summary)

    assert model["schema_filter_bypass_attempt_count"] == 1
    assert model["schema_filter_bypass_rate"] == 1.0
    assert stage["mean_latency_ms"] == 2_500
    assert stage["p95_latency_ms"] == 2_500
    assert stage["mean_input_tokens"] == 1_200
    assert stage["mean_output_tokens"] == 140
    assert "Filter bypass rate" in markdown
    assert "Model stage timings" in markdown
    assert "| model | clarification | 1 | 2.50s | 2.50s | 1200.0 | 140.0 |" in markdown
