from __future__ import annotations

import hashlib
import random
from collections import Counter, defaultdict
from decimal import Decimal
from itertools import combinations
from math import ceil, comb, sqrt
from statistics import median
from typing import Any

GroupKey = tuple[str, str, str, str]


def build_summary(
    attempts: list[dict[str, Any]],
    *,
    expected_attempts_per_model: int,
    benchmark_elapsed_ms: float | None = None,
    call_receipts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    _validate_scored_attempts(attempts)
    grouped: dict[GroupKey, dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for attempt in attempts:
        group_key = (
            str(attempt["track"]),
            str(attempt["context_mode"]),
            str(attempt.get("interaction_mode", "unspecified")),
            str(attempt["transport"]),
        )
        grouped[group_key][str(attempt["model_name"])].append(attempt)

    leaderboards = []
    for group_key, models in sorted(grouped.items()):
        model_summaries = []
        for name, rows in sorted(models.items()):
            receipts = [
                receipt
                for receipt in (call_receipts or [])
                if receipt.get("benchmark_model_name", receipt.get("model_name"))
                == name
                and receipt.get("_source_track", group_key[0]) == group_key[0]
                and receipt.get("_source_context_mode", group_key[1]) == group_key[1]
                and receipt.get("_source_interaction_mode", group_key[2])
                == group_key[2]
                and receipt.get("_source_transport", group_key[3]) == group_key[3]
            ]
            model_summaries.append(
                _summarize_model(
                    name,
                    rows,
                    expected_attempts_per_model,
                    receipts,
                )
            )
        eligible_models = _mark_cost_efficiency(model_summaries)
        paired_comparisons = _paired_comparisons(models)
        instability = _winner_instability(eligible_models, paired_comparisons)
        leaderboards.append(
            {
                "track": group_key[0],
                "context_mode": group_key[1],
                "interaction_mode": group_key[2],
                "transport": group_key[3],
                "expected_attempts_per_model": expected_attempts_per_model,
                "models": eligible_models,
                "paired_comparisons": paired_comparisons,
                "winner_statistically_unstable": instability,
            }
        )

    excluded_case_ids = sorted(
        {
            int(attempt["question_id"])
            for attempt in attempts
            if not attempt.get("diagnostics", {}).get("headline_eligible", True)
        }
    )
    return {
        "attempt_count": len(attempts),
        "benchmark_elapsed_ms": benchmark_elapsed_ms,
        "headline_excluded_case_ids": excluded_case_ids,
        "leaderboards": leaderboards,
    }


def _validate_scored_attempts(attempts: list[dict[str, Any]]) -> None:
    scored_keys = set()
    for attempt in attempts:
        if not _attempt_is_scored(attempt):
            continue
        key = (
            attempt.get("track"),
            attempt.get("model_name"),
            attempt.get("attempt_key"),
        )
        if key in scored_keys:
            raise ValueError(
                f"Duplicate scored attempt: {key[0]} / {key[1]} / {key[2]}"
            )
        scored_keys.add(key)


def render_markdown(summary: dict[str, Any]) -> str:
    lines = ["# RDST Text-to-SQL Benchmark", ""]
    if summary.get("benchmark_elapsed_ms") is not None:
        elapsed_minutes = float(summary["benchmark_elapsed_ms"]) / 60_000
        lines.extend([f"Benchmark wall time: **{elapsed_minutes:.2f} minutes**", ""])
    publication_status = summary.get("publication_status")
    if publication_status and publication_status != "not_applicable":
        lines.extend([f"Publication status: **{publication_status}**", ""])
    run_stop = summary.get("run_stop")
    if isinstance(run_stop, dict) and run_stop.get("reason"):
        lines.extend(
            [
                f"Run stopped before complete coverage: **{run_stop['reason']}**",
                "",
            ]
        )
    warnings = summary.get("comparison", {}).get("compatibility_warnings", [])
    if warnings:
        lines.extend(["## Compatibility warnings", ""])
        lines.extend(f"- {warning}" for warning in warnings)
        lines.append("")
    pipeline_comparisons = summary.get("pipeline_comparisons", [])
    if pipeline_comparisons:
        lines.extend(
            [
                "## RDST pipeline uplift",
                "",
                "Positive delta means `rdst ask` outperformed direct generation.",
                "",
                "| Model | EX delta | 95% CI | RDST-only wins | Direct-only wins | McNemar p |",
                "|---|---:|---:|---:|---:|---:|",
            ]
        )
        for comparison in pipeline_comparisons:
            low, high = comparison["accuracy_delta_ci_95"]
            p_value = comparison["mcnemar_exact_p"]
            p_text = f"{p_value:.4f}" if p_value is not None else "n/a"
            lines.append(
                f"| {comparison['model_name']} | "
                f"{comparison['accuracy_delta']:+.1%} | "
                f"{low:+.1%}-{high:+.1%} | "
                f"{len(comparison['first_only_correct'])} | "
                f"{len(comparison['second_only_correct'])} | {p_text} |"
            )
        lines.append("")
    excluded = summary.get("headline_excluded_case_ids", [])
    if excluded:
        lines.extend(
            [
                "Internal headline EX excludes unstable gold cases: "
                + ", ".join(str(case_id) for case_id in excluded),
                "",
            ]
        )
    for board in summary["leaderboards"]:
        lines.extend(
            [
                (
                    f"## {board['track']} / {board['context_mode']} / "
                    f"{board['interaction_mode']} / {board['transport']}"
                ),
                "",
                "| Model | Reasoning | Coverage | Unscored | Official EX | Stable EX | 95% CI | Cold/task | Cold/correct | Billed/task | Mean/task | P95/task | Eligible | Pareto |",
                "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|:---:|",
            ]
        )
        for model in board["models"]:
            cost = model["cost_per_correct_usd"]
            cost_text = f"${Decimal(cost):.6f}" if cost is not None else "n/a"
            task_cost = model["cost_per_question_usd"]
            task_cost_text = (
                f"${Decimal(task_cost):.6f}" if task_cost is not None else "n/a"
            )
            billed_cost = model["billed_cost_per_question_usd"]
            billed_cost_text = (
                f"${Decimal(billed_cost):.6f}" if billed_cost is not None else "n/a"
            )
            interval = model["headline_accuracy_ci_95"]
            lines.append(
                f"| {model['model_name']} | {model['reasoning_label']} | "
                f"{model['coverage']:.1%} | {model['unscored_attempt_count']} | "
                f"{model['official_execution_accuracy']:.1%} | "
                f"{model['headline_execution_accuracy']:.1%} | "
                f"{interval[0]:.1%}-{interval[1]:.1%} | {task_cost_text} | "
                f"{cost_text} | {billed_cost_text} | "
                f"{model['mean_latency_ms'] / 1000:.2f}s | "
                f"{model['p95_latency_ms'] / 1000:.2f}s | "
                f"{'yes' if model['cost_efficiency_eligible'] else 'no'} | "
                f"{'yes' if model['pareto_optimal'] else 'no'} |"
            )
        winner = next(
            (model for model in board["models"] if model["cost_efficiency_winner"]),
            None,
        )
        lines.extend(
            [
                "",
                f"Cost-efficient winner: **{winner['model_name']}**"
                if winner
                else "Cost-efficient winner: unavailable",
                (
                    "Statistical warning: paired uncertainty does not establish "
                    "the winner's accuracy ordering."
                    if board.get("winner_statistically_unstable")
                    else ""
                ),
                "",
            ]
        )
        if board["track"] == "rdst-ask" or any(
            model.get("ambiguity_attempt_count") for model in board["models"]
        ):
            lines.extend(
                [
                    "### Pipeline diagnostics",
                    "",
                    (
                        "`EX given injection` is downstream execution accuracy "
                        "conditioned on an auto injection; it is not resolver-option "
                        "accuracy."
                    ),
                    "",
                    "| Model | Alternatives reported | Detector recommended clarification | Actual clarification stops | Auto-injected | Abstained options | EX given injection | Filter bypass rate | Mean generation output | Schema expansion rate | Validation repair rate |",
                    "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
                ]
            )
            for model in board["models"]:
                conditional_ex = model.get("end_to_end_ex_given_auto_injection")
                conditional_text = (
                    f"{conditional_ex:.1%}" if conditional_ex is not None else "n/a"
                )
                repair_rate = model.get("validation_repair_rate", 0.0)
                repair_text = f"{repair_rate:.1%}"
                if model.get("validation_repair_rate_exceeds_qualification_threshold"):
                    repair_text += " (over 10% gate)"
                lines.append(
                    f"| {model['model_name']} | "
                    f"{model.get('ambiguity_attempt_count', 0)} | "
                    f"{model.get('detector_clarification_recommended_attempt_count', 0)} | "
                    f"{model.get('clarification_required_attempt_count', 0)} | "
                    f"{model.get('auto_injection_attempt_count', 0)} | "
                    f"{model.get('auto_abstention_count', 0)} | "
                    f"{conditional_text} | "
                    f"{model.get('schema_filter_bypass_rate', 0.0):.1%} | "
                    f"{model.get('mean_generation_output_tokens', 0.0):.1f} | "
                    f"{model.get('schema_expansion_rate', 0.0):.1%} | "
                    f"{repair_text} |"
                )
            lines.append("")
        if any(model.get("cost_by_stage") for model in board["models"]):
            lines.extend(
                [
                    "### Model stage timings",
                    "",
                    "| Model | Stage | Calls | Mean latency | P95 latency | Mean input | Mean output |",
                    "|---|---|---:|---:|---:|---:|---:|",
                ]
            )
            for model in board["models"]:
                for stage, stats in model.get("cost_by_stage", {}).items():
                    calls = int(stats.get("call_count", 0))
                    lines.append(
                        f"| {model['model_name']} | {stage} | {calls} | "
                        f"{float(stats.get('mean_latency_ms', 0.0)) / 1000:.2f}s | "
                        f"{float(stats.get('p95_latency_ms', 0.0)) / 1000:.2f}s | "
                        f"{float(stats.get('mean_input_tokens', 0.0)):.1f} | "
                        f"{float(stats.get('mean_output_tokens', 0.0)):.1f} |"
                    )
            lines.append("")
        if board.get("paired_comparisons"):
            lines.extend(
                [
                    "### Paired comparisons",
                    "",
                    "| Models | Accuracy delta | 95% CI | Discordant | McNemar p |",
                    "|---|---:|---:|---:|---:|",
                ]
            )
            for comparison in board["paired_comparisons"]:
                interval = comparison["accuracy_delta_ci_95"]
                discordant = len(comparison["first_only_correct"]) + len(
                    comparison["second_only_correct"]
                )
                p_value = comparison["mcnemar_exact_p"]
                p_text = f"{p_value:.4f}" if p_value is not None else "n/a"
                lines.append(
                    f"| {comparison['first_model']} - {comparison['second_model']} | "
                    f"{comparison['accuracy_delta']:+.1%} | "
                    f"{interval[0]:+.1%}-{interval[1]:+.1%} | "
                    f"{discordant} | {p_text} |"
                )
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _summarize_model(
    model_name: str,
    attempts: list[dict[str, Any]],
    expected_attempts: int,
    call_receipts: list[dict[str, Any]],
) -> dict[str, Any]:
    scored_attempts = [attempt for attempt in attempts if _attempt_is_scored(attempt)]
    unscored_attempts = [
        attempt for attempt in attempts if not _attempt_is_scored(attempt)
    ]
    correctness = [
        bool(attempt.get("execution_correct")) for attempt in scored_attempts
    ]
    correct_count = sum(correctness)
    headline_attempts = [
        attempt
        for attempt in scored_attempts
        if attempt.get("diagnostics", {}).get("headline_eligible", True)
    ]
    headline_correct_count = sum(
        bool(attempt.get("execution_correct")) for attempt in headline_attempts
    )
    normalized_total = sum(
        (
            Decimal(str(attempt.get("normalized_cold_cost_usd", "0")))
            for attempt in scored_attempts
        ),
        Decimal(0),
    )
    headline_normalized_total = sum(
        (
            Decimal(str(attempt.get("normalized_cold_cost_usd", "0")))
            for attempt in headline_attempts
        ),
        Decimal(0),
    )
    official_cost_per_correct = (
        normalized_total / correct_count if correct_count else None
    )
    official_cost_per_question = (
        normalized_total / len(scored_attempts) if scored_attempts else None
    )
    cost_per_correct = (
        headline_normalized_total / headline_correct_count
        if headline_correct_count
        else None
    )
    cost_per_question = (
        headline_normalized_total / len(headline_attempts)
        if headline_attempts
        else None
    )
    scored_actual_values = [
        attempt.get("actual_cost_usd") for attempt in scored_attempts
    ]
    scored_actual_total = _complete_decimal_sum(scored_actual_values)
    repaired_keys = {
        (int(attempt["question_id"]), int(attempt.get("repetition", 0)))
        for attempt in unscored_attempts
    } & {
        (int(attempt["question_id"]), int(attempt.get("repetition", 0)))
        for attempt in scored_attempts
    }

    all_call_records = [
        call
        for attempt in attempts
        for call in attempt.get("call_records", [])
        if isinstance(call, dict)
    ]
    operational_calls = call_receipts or all_call_records
    scored_invocation_ids = {
        str(attempt["invocation_id"])
        for attempt in scored_attempts
        if attempt.get("invocation_id")
    }
    attempt_invocation_ids = {
        str(attempt["invocation_id"])
        for attempt in attempts
        if attempt.get("invocation_id")
    }
    if call_receipts:
        repair_calls = [
            call
            for call in call_receipts
            if call.get("attempt_id")
            and str(call["attempt_id"]) not in scored_invocation_ids
        ]
    else:
        repair_calls = [
            call
            for attempt in unscored_attempts
            for call in attempt.get("call_records", [])
            if isinstance(call, dict)
        ]
    repair_invocation_ids = {
        str(call["attempt_id"]) for call in repair_calls if call.get("attempt_id")
    }
    orphan_receipt_invocation_ids = repair_invocation_ids - attempt_invocation_ids
    if repair_calls:
        repair_actual_total = _complete_decimal_sum(
            [call.get("actual_cost_usd") for call in repair_calls]
        )
        repair_normalized_total = sum(
            (
                Decimal(str(call.get("normalized_cold_cost_usd", "0")))
                for call in repair_calls
            ),
            Decimal(0),
        )
    else:
        repair_actual_total = _complete_decimal_sum(
            [attempt.get("actual_cost_usd") for attempt in unscored_attempts]
        )
        repair_normalized_total = sum(
            (
                Decimal(str(attempt.get("normalized_cold_cost_usd", "0")))
                for attempt in unscored_attempts
            ),
            Decimal(0),
        )
    if operational_calls:
        operational_actual_total = _complete_decimal_sum(
            [call.get("actual_cost_usd") for call in operational_calls]
        )
        expected_billed_total = _complete_decimal_sum(
            [call.get("expected_billed_cost_usd") for call in operational_calls]
        )
    else:
        operational_actual_total = _complete_decimal_sum(
            [attempt.get("actual_cost_usd") for attempt in attempts]
        )
        expected_billed_total = None
    billed_cost_per_question = (
        operational_actual_total / len(scored_attempts)
        if operational_actual_total is not None and scored_attempts
        else None
    )
    billed_cost_per_correct = (
        operational_actual_total / correct_count
        if operational_actual_total is not None and correct_count
        else None
    )
    billing_variance = (
        operational_actual_total - expected_billed_total
        if operational_actual_total is not None and expected_billed_total is not None
        else None
    )

    outcome_counts = Counter(
        str(attempt.get("outcome", "unknown")) for attempt in attempts
    )
    scored_call_records = [
        call
        for attempt in scored_attempts
        for call in attempt.get("call_records", [])
        if isinstance(call, dict)
    ]
    gold_table_recalls = [
        float(value)
        for attempt in scored_attempts
        if (value := attempt.get("diagnostics", {}).get("gold_table_recall"))
        is not None
    ]
    ambiguity_attempts = [
        attempt
        for attempt in scored_attempts
        if attempt.get("diagnostics", {}).get("ambiguity_report", {}).get("ambiguities")
    ]
    detector_clarification_recommended_attempts = [
        attempt
        for attempt in scored_attempts
        if attempt.get("diagnostics", {})
        .get("ambiguity_report", {})
        .get("requires_clarification")
        is True
    ]
    clarification_required_attempts = [
        attempt
        for attempt in scored_attempts
        if attempt.get("outcome") == "clarification_required"
    ]
    selected_resolution_attempts = [
        attempt
        for attempt in scored_attempts
        if any(
            item.get("action") == "select" and item.get("applied", True) is True
            for item in attempt.get("diagnostics", {}).get(
                "clarification_resolutions", []
            )
        )
    ]
    abstained_resolution_count = sum(
        item.get("action") == "abstain"
        for attempt in scored_attempts
        for item in attempt.get("diagnostics", {}).get("clarification_resolutions", [])
    )
    generation_output_tokens = sum(
        int(call.get("output_tokens", 0))
        for call in scored_call_records
        if call.get("stage") == "sql_generation"
    )
    validation_repair_attempts = [
        attempt
        for attempt in scored_attempts
        if int(attempt.get("diagnostics", {}).get("retry_count", 0)) > 0
    ]
    schema_expansion_attempts = [
        attempt
        for attempt in scored_attempts
        if int(attempt.get("diagnostics", {}).get("schema_expansion_count", 0)) > 0
    ]
    schema_filter_bypass_attempts = [
        attempt
        for attempt in scored_attempts
        if attempt.get("diagnostics", {}).get("schema_filter_strategy")
        in {"full-schema-below-budget", "full-schema-unfiltered"}
    ]
    latencies = [float(attempt.get("latency_ms", 0.0)) for attempt in scored_attempts]
    total_latency_ms = sum(latencies)
    reasoning_efforts = {
        str(attempt.get("reasoning_effort") or "provider-default")
        for attempt in attempts
    }
    max_token_values = {
        int(attempt["max_tokens"])
        for attempt in attempts
        if attempt.get("max_tokens") is not None
    }
    reasoning_effort = (
        next(iter(reasoning_efforts)) if len(reasoning_efforts) == 1 else "mixed"
    )
    max_tokens = next(iter(max_token_values)) if len(max_token_values) == 1 else None
    reasoning_label = reasoning_effort
    if max_tokens:
        reasoning_label += f"; {max_tokens:,} output cap"
    accuracy = correct_count / len(scored_attempts) if scored_attempts else 0.0
    headline_accuracy = (
        headline_correct_count / len(headline_attempts) if headline_attempts else 0.0
    )
    return {
        "model_name": model_name,
        "reasoning_effort": reasoning_effort,
        "max_tokens": max_tokens,
        "reasoning_label": reasoning_label,
        "attempt_count": len(attempts),
        "scored_attempt_count": len(scored_attempts),
        "unscored_attempt_count": len(unscored_attempts),
        "repaired_pair_count": len(repaired_keys),
        "repair_invocation_count": len(repair_invocation_ids),
        "orphan_receipt_invocation_count": len(orphan_receipt_invocation_ids),
        "repair_rate": len(repaired_keys) / expected_attempts
        if expected_attempts
        else 0.0,
        "repair_actual_cost_usd": str(repair_actual_total)
        if repair_actual_total is not None
        else None,
        "repair_normalized_cold_cost_usd": str(repair_normalized_total),
        "coverage": len(scored_attempts) / expected_attempts
        if expected_attempts
        else 0.0,
        "correct_count": correct_count,
        "execution_accuracy": headline_accuracy,
        "accuracy_ci_95": list(
            _accuracy_interval(f"{model_name}:headline", headline_attempts)
        ),
        "official_execution_accuracy": accuracy,
        "official_accuracy_ci_95": list(
            _accuracy_interval(model_name, scored_attempts)
        ),
        "headline_attempt_count": len(headline_attempts),
        "headline_correct_count": headline_correct_count,
        "headline_execution_accuracy": headline_accuracy,
        "headline_accuracy_ci_95": list(
            _accuracy_interval(f"{model_name}:headline", headline_attempts)
        ),
        "mean_soft_f1": sum(
            float(attempt.get("soft_f1", 0.0)) for attempt in scored_attempts
        )
        / len(scored_attempts)
        if scored_attempts
        else 0.0,
        "silent_wrong_rate": outcome_counts["incorrect_result"] / len(scored_attempts)
        if scored_attempts
        else 0.0,
        "outcome_counts": dict(sorted(outcome_counts.items())),
        "accuracy_by_difficulty": _accuracy_breakdown(scored_attempts, "difficulty"),
        "accuracy_by_database": _accuracy_breakdown(scored_attempts, "db_id"),
        "mean_latency_ms": total_latency_ms / len(latencies) if latencies else 0.0,
        "median_latency_ms": median(latencies) if latencies else 0.0,
        "p95_latency_ms": _percentile(latencies, 0.95),
        "total_latency_ms": total_latency_ms,
        "mean_candidate_execution_ms": sum(
            float(attempt.get("candidate_execution_ms", 0.0))
            for attempt in scored_attempts
        )
        / len(scored_attempts)
        if scored_attempts
        else 0.0,
        "total_oracle_latency_ms": sum(
            float(attempt.get("oracle_latency_ms", 0.0)) for attempt in scored_attempts
        ),
        "tasks_per_minute": len(latencies) * 60_000 / total_latency_ms
        if total_latency_ms
        else 0.0,
        "mean_model_call_latency_ms": sum(
            float(call.get("latency_ms", 0.0)) for call in scored_call_records
        )
        / len(scored_call_records)
        if scored_call_records
        else 0.0,
        "input_tokens": sum(
            int(call.get("input_tokens", 0)) for call in scored_call_records
        ),
        "output_tokens": sum(
            int(call.get("output_tokens", 0)) for call in scored_call_records
        ),
        "reasoning_tokens": sum(
            int(call.get("reasoning_tokens", 0)) for call in scored_call_records
        ),
        "cached_input_tokens": sum(
            int(call.get("cached_input_tokens", 0)) for call in scored_call_records
        ),
        "cost_by_stage": _stage_costs(scored_call_records),
        "operational_cost_by_stage": _stage_costs(operational_calls),
        "mean_gold_table_recall": sum(gold_table_recalls) / len(gold_table_recalls)
        if gold_table_recalls
        else None,
        "schema_filter_bypass_attempt_count": len(schema_filter_bypass_attempts),
        "schema_filter_bypass_rate": (
            len(schema_filter_bypass_attempts) / len(scored_attempts)
            if scored_attempts
            else 0.0
        ),
        "ambiguity_attempt_count": len(ambiguity_attempts),
        "detector_clarification_recommended_attempt_count": len(
            detector_clarification_recommended_attempts
        ),
        "clarification_required_attempt_count": len(clarification_required_attempts),
        "auto_injection_attempt_count": len(selected_resolution_attempts),
        "auto_abstention_count": abstained_resolution_count,
        "end_to_end_ex_given_auto_injection": (
            sum(
                bool(attempt.get("execution_correct"))
                for attempt in selected_resolution_attempts
            )
            / len(selected_resolution_attempts)
            if selected_resolution_attempts
            else None
        ),
        "generation_output_tokens": generation_output_tokens,
        "mean_generation_output_tokens": (
            generation_output_tokens / len(scored_attempts) if scored_attempts else 0.0
        ),
        "schema_expansion_attempt_count": len(schema_expansion_attempts),
        "schema_expansion_rate": (
            len(schema_expansion_attempts) / len(scored_attempts)
            if scored_attempts
            else 0.0
        ),
        "validation_repair_attempt_count": len(validation_repair_attempts),
        "validation_repair_rate": (
            len(validation_repair_attempts) / len(scored_attempts)
            if scored_attempts
            else 0.0
        ),
        "validation_repair_rate_exceeds_qualification_threshold": (
            len(validation_repair_attempts) / len(scored_attempts) > 0.10
            if scored_attempts
            else False
        ),
        "actual_cost_usd": str(operational_actual_total)
        if operational_actual_total is not None
        else None,
        "scored_actual_cost_usd": str(scored_actual_total)
        if scored_actual_total is not None
        else None,
        "expected_billed_cost_usd": str(expected_billed_total)
        if expected_billed_total is not None
        else None,
        "billing_variance_usd": str(billing_variance)
        if billing_variance is not None
        else None,
        "normalized_cold_cost_usd": str(headline_normalized_total),
        "official_normalized_cold_cost_usd": str(normalized_total),
        "cost_basis": "normalized_cold",
        "cost_denominator": "stable_cases",
        "cost_per_correct_usd": str(cost_per_correct)
        if cost_per_correct is not None
        else None,
        "cost_per_question_usd": str(cost_per_question)
        if cost_per_question is not None
        else None,
        "official_cost_per_correct_usd": str(official_cost_per_correct)
        if official_cost_per_correct is not None
        else None,
        "official_cost_per_question_usd": str(official_cost_per_question)
        if official_cost_per_question is not None
        else None,
        "billed_cost_per_correct_usd": str(billed_cost_per_correct)
        if billed_cost_per_correct is not None
        else None,
        "billed_cost_per_question_usd": str(billed_cost_per_question)
        if billed_cost_per_question is not None
        else None,
        "cost_per_100_questions_usd": str(cost_per_question * 100)
        if cost_per_question is not None
        else None,
        "cost_per_1000_questions_usd": str(cost_per_question * 1000)
        if cost_per_question is not None
        else None,
        "cost_efficiency_eligible": False,
        "cost_efficiency_winner": False,
        "pareto_optimal": False,
    }


def _complete_decimal_sum(values: list[Any]) -> Decimal | None:
    if not values:
        return Decimal(0)
    if any(value is None for value in values):
        return None
    return sum((Decimal(str(value)) for value in values), Decimal(0))


def _attempt_is_scored(attempt: dict[str, Any]) -> bool:
    if "scored" in attempt:
        return bool(attempt["scored"])
    return attempt.get("outcome") not in {"transport_error", "uncontrolled_route"}


def _wilson_interval(successes: int, count: int) -> tuple[float, float]:
    if count <= 0:
        return 0.0, 0.0
    z = 1.959963984540054
    proportion = successes / count
    denominator = 1 + z * z / count
    center = (proportion + z * z / (2 * count)) / denominator
    radius = (
        z
        * sqrt(proportion * (1 - proportion) / count + z * z / (4 * count * count))
        / denominator
    )
    return max(center - radius, 0.0), min(center + radius, 1.0)


def _accuracy_interval(
    model_name: str, attempts: list[dict[str, Any]]
) -> tuple[float, float]:
    by_question: dict[int, list[float]] = defaultdict(list)
    for attempt in attempts:
        by_question[int(attempt["question_id"])].append(
            float(bool(attempt.get("execution_correct")))
        )
    if not by_question:
        return 0.0, 0.0
    if all(len(values) == 1 for values in by_question.values()):
        successes = sum(values[0] for values in by_question.values())
        return _wilson_interval(int(successes), len(by_question))
    question_means = [sum(values) / len(values) for values in by_question.values()]
    seed = int.from_bytes(hashlib.sha256(model_name.encode()).digest()[:8], "big")
    randomizer = random.Random(seed)
    size = len(question_means)
    estimates = sorted(
        sum(question_means[randomizer.randrange(size)] for _ in range(size)) / size
        for _ in range(2_000)
    )
    return estimates[50], estimates[1_950]


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(ceil(percentile * len(ordered)) - 1, 0)]


def _stage_costs(call_records: list[dict[str, Any]]):
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for call in call_records:
        groups[str(call.get("stage", "unknown"))].append(call)
    result = {}
    for stage, calls in sorted(groups.items()):
        actual = [call.get("actual_cost_usd") for call in calls]
        latencies = [float(call.get("latency_ms", 0.0)) for call in calls]
        input_tokens = sum(int(call.get("input_tokens", 0)) for call in calls)
        output_tokens = sum(int(call.get("output_tokens", 0)) for call in calls)
        result[stage] = {
            "call_count": len(calls),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "mean_input_tokens": input_tokens / len(calls),
            "mean_output_tokens": output_tokens / len(calls),
            "total_latency_ms": sum(latencies),
            "mean_latency_ms": sum(latencies) / len(calls),
            "p95_latency_ms": _percentile(latencies, 0.95),
            "actual_cost_usd": str(
                sum((Decimal(str(value)) for value in actual), Decimal(0))
            )
            if all(value is not None for value in actual)
            else None,
            "normalized_cold_cost_usd": str(
                sum(
                    (
                        Decimal(str(call.get("normalized_cold_cost_usd", "0")))
                        for call in calls
                    ),
                    Decimal(0),
                )
            ),
            "expected_billed_cost_usd": str(
                sum(
                    (
                        Decimal(str(call.get("expected_billed_cost_usd", "0")))
                        for call in calls
                    ),
                    Decimal(0),
                )
            ),
        }
    return result


def _accuracy_breakdown(attempts: list[dict[str, Any]], field: str):
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for attempt in attempts:
        groups[str(attempt.get(field, "unknown"))].append(attempt)
    return {
        key: {
            "attempt_count": len(rows),
            "execution_accuracy": sum(
                bool(row.get("execution_correct")) for row in rows
            )
            / len(rows),
            "accuracy_ci_95": list(_accuracy_interval(f"{field}:{key}", rows)),
        }
        for key, rows in sorted(groups.items())
    }


def _mark_cost_efficiency(models: list[dict[str, Any]]):
    complete = [model for model in models if model["coverage"] == 1.0]
    if not complete:
        return models
    best_accuracy = max(model["headline_execution_accuracy"] for model in complete)
    eligible = [
        model
        for model in complete
        if model["headline_execution_accuracy"] >= best_accuracy - 0.02
        and model["cost_per_correct_usd"] is not None
    ]
    for model in eligible:
        model["cost_efficiency_eligible"] = True
    if eligible:
        winner = min(
            eligible,
            key=lambda model: (
                Decimal(model["cost_per_correct_usd"]),
                -model["headline_execution_accuracy"],
                model["model_name"],
            ),
        )
        winner["cost_efficiency_winner"] = True

    candidates = [
        model for model in complete if model["cost_per_question_usd"] is not None
    ]
    for model in candidates:
        model_cost = Decimal(model["cost_per_question_usd"])
        model["pareto_optimal"] = not any(
            other is not model
            and other["headline_execution_accuracy"]
            >= model["headline_execution_accuracy"]
            and Decimal(other["cost_per_question_usd"]) <= model_cost
            and (
                other["headline_execution_accuracy"]
                > model["headline_execution_accuracy"]
                or Decimal(other["cost_per_question_usd"]) < model_cost
            )
            for other in candidates
        )
    return models


def _winner_instability(
    models: list[dict[str, Any]], comparisons: list[dict[str, Any]]
) -> bool:
    winner = next(
        (model for model in models if model.get("cost_efficiency_winner")), None
    )
    if winner is None:
        return False
    best = max(models, key=lambda model: model["headline_execution_accuracy"])
    if winner is best:
        return False
    comparison = next(
        (
            item
            for item in comparisons
            if {item["first_model"], item["second_model"]}
            == {winner["model_name"], best["model_name"]}
        ),
        None,
    )
    if comparison is None:
        return True
    low, high = comparison["accuracy_delta_ci_95"]
    return low <= 0 <= high


def _paired_comparisons(models: dict[str, list[dict[str, Any]]]):
    return [
        paired_comparison(
            first_name,
            models[first_name],
            second_name,
            models[second_name],
        )
        for first_name, second_name in combinations(sorted(models), 2)
    ]


def paired_comparison(
    first_name: str,
    first_attempts: list[dict[str, Any]],
    second_name: str,
    second_attempts: list[dict[str, Any]],
):
    first = [
        attempt
        for attempt in first_attempts
        if _attempt_is_scored(attempt)
        and attempt.get("diagnostics", {}).get("headline_eligible", True)
    ]
    second = [
        attempt
        for attempt in second_attempts
        if _attempt_is_scored(attempt)
        and attempt.get("diagnostics", {}).get("headline_eligible", True)
    ]
    first_by_key = {
        (int(attempt["question_id"]), int(attempt.get("repetition", 0))): bool(
            attempt.get("execution_correct")
        )
        for attempt in first
    }
    second_by_key = {
        (int(attempt["question_id"]), int(attempt.get("repetition", 0))): bool(
            attempt.get("execution_correct")
        )
        for attempt in second
    }
    shared_keys = sorted(first_by_key.keys() & second_by_key.keys())
    first_only = [
        {"question_id": key[0], "repetition": key[1]}
        for key in shared_keys
        if first_by_key[key] and not second_by_key[key]
    ]
    second_only = [
        {"question_id": key[0], "repetition": key[1]}
        for key in shared_keys
        if second_by_key[key] and not first_by_key[key]
    ]
    delta, interval = _paired_question_bootstrap(
        first_name,
        second_name,
        first_by_key,
        second_by_key,
    )
    repetitions_by_question = Counter(key[0] for key in shared_keys)
    mcnemar_p = (
        _mcnemar_exact_p(len(first_only), len(second_only))
        if all(count == 1 for count in repetitions_by_question.values())
        else None
    )
    return {
        "first_model": first_name,
        "second_model": second_name,
        "shared_attempt_count": len(shared_keys),
        "shared_question_count": len(repetitions_by_question),
        "accuracy_delta": delta,
        "accuracy_delta_ci_95": list(interval),
        "first_only_correct": first_only,
        "second_only_correct": second_only,
        "mcnemar_exact_p": mcnemar_p,
    }


def _paired_question_bootstrap(
    first_name: str,
    second_name: str,
    first: dict[tuple[int, int], bool],
    second: dict[tuple[int, int], bool],
    *,
    samples: int = 2_000,
):
    shared_keys = sorted(first.keys() & second.keys())
    if not shared_keys:
        return 0.0, (0.0, 0.0)
    deltas: dict[int, list[float]] = defaultdict(list)
    for question_id, repetition in shared_keys:
        key = (question_id, repetition)
        deltas[question_id].append(float(first[key]) - float(second[key]))
    deltas_by_question = [sum(values) / len(values) for values in deltas.values()]
    if not deltas_by_question:
        return 0.0, (0.0, 0.0)
    point = sum(deltas_by_question) / len(deltas_by_question)
    seed_material = f"{first_name}\0{second_name}".encode()
    seed = int.from_bytes(hashlib.sha256(seed_material).digest()[:8], "big")
    randomizer = random.Random(seed)
    size = len(deltas_by_question)
    estimates = sorted(
        sum(deltas_by_question[randomizer.randrange(size)] for _ in range(size)) / size
        for _ in range(samples)
    )
    return point, (
        estimates[int(samples * 0.025)],
        estimates[min(int(samples * 0.975), samples - 1)],
    )


def _mcnemar_exact_p(first_only: int, second_only: int) -> float:
    discordant = first_only + second_only
    if discordant == 0:
        return 1.0
    tail = sum(
        comb(discordant, index) for index in range(min(first_only, second_only) + 1)
    )
    return min(1.0, 2 * tail / (2**discordant))
