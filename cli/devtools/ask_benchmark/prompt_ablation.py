from __future__ import annotations

import hashlib
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from statistics import mean
from time import perf_counter
from uuid import uuid4

from features.ask.engine.ask3.phases.schema import _format_semantic_schema
from features.ask.prompts.ask_prompts import (
    ENUM_GROUNDING_RULES,
    PLAIN_SQL_ASK_PROMPT,
    SQL_GENERATION_SYSTEM_PROMPT,
)
from features.ask.sql_validation import validate_sql_for_ask
from features.schema.semantic_layer.manager import SemanticLayerManager
from shared.persistence import write_json, write_text

from .anthropic_adapter import AnthropicSDKAdapter
from .artifacts import ArtifactStore
from .executor import MySQLExecutor
from .generation_ablation import (
    _case_inputs_sha256,
    _initialize_manifest,
    _load_json,
    _load_jsonl,
    _sha256_file,
    _validate_source,
    _verify_source_environment,
)
from .models import BenchmarkCase, ContextMode, ModelSpec, QueryResult
from .oracle import score_results
from .pipeline import build_model_only_prompt, extract_sql

PROMPT_ABLATION_SCHEMA_VERSION = 1
PROMPT_ABLATION_CELLS = ("lean-direct-prompt", "detailed-product-prompt")
PROMPT_ABLATION_EXPERIMENTS = {
    "lean-vs-detailed-v1": {
        "cells": PROMPT_ABLATION_CELLS,
        "baseline": "detailed-product-prompt",
        "treatment": "lean-direct-prompt",
        "delta_label": "lean-prompt",
    },
    "enum-grounding-v1": {
        "cells": ("detailed-product-prompt", "enum-grounded-product-prompt"),
        "baseline": "detailed-product-prompt",
        "treatment": "enum-grounded-product-prompt",
        "delta_label": "enum-grounding",
    },
}
RDST_ROOT = Path(__file__).resolve().parents[2]


def run_prompt_ablation(
    *,
    source_run_dir: Path,
    output_dir: Path,
    cases: list[BenchmarkCase],
    semantic_dir: Path,
    executor: MySQLExecutor,
    model_spec: ModelSpec,
    route_check: dict,
    context_provenance: dict,
    database_provision: dict,
    protocol_sha256: str,
    rdst_revision: str,
    rdst_dirty: bool,
    rdst_diff_sha256: str,
    experiment: str = "lean-vs-detailed-v1",
) -> dict:
    """Run a registered paired user-prompt experiment under plain SQL."""
    try:
        experiment_spec = PROMPT_ABLATION_EXPERIMENTS[experiment]
    except KeyError as exc:
        raise ValueError(f"Unknown prompt ablation experiment: {experiment}") from exc
    cells = tuple(experiment_spec["cells"])
    source_manifest_path = source_run_dir / "manifest.json"
    source_attempts_path = source_run_dir / "attempts.jsonl"
    source_manifest = _load_json(source_manifest_path)
    source_attempts = _load_jsonl(source_attempts_path)
    _validate_source(source_manifest, source_attempts, cases)
    _verify_source_environment(
        source_manifest,
        context_provenance=context_provenance,
        database_provision=database_provision,
    )
    source_by_id = {
        int(row["question_id"]): row for row in source_attempts if row.get("scored")
    }

    implementation_hashes = _implementation_hashes()
    context_provenance_path = semantic_dir / "provenance.json"
    output_dir.mkdir(parents=True, exist_ok=True)
    store = ArtifactStore(output_dir)
    manifest = {
        "artifact_schema_version": PROMPT_ABLATION_SCHEMA_VERSION,
        "run_id": output_dir.name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "internal-user-prompt-ablation",
        "publication_label": "internal; never rdst-ask",
        "experiment": experiment,
        "source_run_id": source_manifest["run_id"],
        "source_manifest_sha256": _sha256_file(source_manifest_path),
        "source_attempts_sha256": _sha256_file(source_attempts_path),
        "context_provenance_sha256": _sha256_file(context_provenance_path),
        "context_provenance": context_provenance,
        "dataset_revision": source_manifest["dataset_revision"],
        "dialect": "mysql",
        "suite": "smoke",
        "case_ids": [case.question_id for case in cases],
        "case_inputs_sha256": _case_inputs_sha256(cases),
        "cells": list(cells),
        "baseline_cell": experiment_spec["baseline"],
        "treatment_cell": experiment_spec["treatment"],
        "delta_label": experiment_spec["delta_label"],
        "question_policy": (
            "original BIRD question; no evidence or clarification injection"
        ),
        "schema_policy": "exact filtered tables frozen in the source AskService run",
        "generation_policy": {
            "common_system_message": SQL_GENERATION_SYSTEM_PROMPT,
            "common_response": "plain SQL; no tool or JSON response schema",
            "lean_prompt": "dialect, schema, and question only",
            "detailed_prompt": (
                "production SQL requirements with plain-response instructions"
            ),
            "enum_grounding_rules": ENUM_GROUNDING_RULES,
            "temperature": 0.0,
            "max_tokens": 800,
            "cell_order": "case-index-alternating-v1",
        },
        "model": asdict(model_spec),
        "route_check": route_check,
        "database_provision": database_provision,
        "query_bounds": asdict(executor.bounds),
        "rdst_revision": rdst_revision,
        "rdst_dirty": rdst_dirty,
        "rdst_diff_sha256": rdst_diff_sha256,
        "benchmark_protocol_sha256": protocol_sha256,
        "implementation_hashes": implementation_hashes,
    }
    _initialize_manifest(store, manifest)
    completed = store.completed_keys()
    manager = SemanticLayerManager(base_dir=semantic_dir)
    adapter = AnthropicSDKAdapter(
        model_spec, call_receipt_sink=store.append_call_receipt
    )
    gold = {
        case.question_id: executor.execute(case.gold_sql, db_id=case.db_id)
        for case in cases
    }
    failed_gold = [case_id for case_id, result in gold.items() if not result.succeeded]
    if failed_gold:
        raise RuntimeError(f"Prompt ablation gold execution failed: {failed_gold}")

    with store.run_lock():
        for case_index, case in enumerate(cases):
            layer = manager.load(case.db_id, use_cache=False)
            filtered_tables = source_by_id[case.question_id]["diagnostics"][
                "filtered_tables"
            ]
            filtered_layer = deepcopy(layer)
            filtered_layer.tables = {
                name: layer.tables[name]
                for name in filtered_tables
                if name in layer.tables
            }
            missing = sorted(set(filtered_tables) - set(filtered_layer.tables))
            if missing:
                raise ValueError(
                    f"Source filtered tables are absent for {case.question_id}: {missing}"
                )
            schema = _format_semantic_schema(filtered_layer)
            cell_order = cells if case_index % 2 == 0 else tuple(reversed(cells))
            for cell in cell_order:
                attempt_key = hashlib.sha256(
                    f"{manifest['source_attempts_sha256']}:{cell}:{case.question_id}".encode()
                ).hexdigest()
                if attempt_key in completed:
                    continue
                invocation_id = uuid4().hex
                adapter.set_attempt_id(invocation_id)
                started = perf_counter()
                result = _run_cell(
                    case=case,
                    schema=schema,
                    cell=cell,
                    adapter=adapter,
                    executor=executor,
                    gold=gold[case.question_id],
                )
                calls = adapter.drain_call_records()
                store.append_attempt(
                    {
                        "attempt_key": attempt_key,
                        "invocation_id": invocation_id,
                        "run_id": manifest["run_id"],
                        "question_id": case.question_id,
                        "db_id": case.db_id,
                        "cell": cell,
                        "schema_scope": "filtered",
                        "generation_contract": "plain-sql",
                        "scored": bool(calls) and not any(call.error for call in calls),
                        "outcome": result["outcome"],
                        "execution_correct": result.get("execution_correct", False),
                        "soft_f1": result.get("soft_f1", 0.0),
                        "generated_sql": result.get("generated_sql"),
                        "validated_sql": result.get("validated_sql"),
                        "error": result.get("error"),
                        "latency_ms": (perf_counter() - started) * 1000,
                        "normalized_cold_cost_usd": str(
                            sum(
                                (call.normalized_cold_cost_usd for call in calls),
                                Decimal(0),
                            )
                        ),
                        "input_tokens": sum(call.input_tokens for call in calls),
                        "output_tokens": sum(call.output_tokens for call in calls),
                        "schema_sha256": hashlib.sha256(schema.encode()).hexdigest(),
                        "schema_chars": len(schema),
                        "filtered_tables": filtered_tables,
                    }
                )
                completed.add(attempt_key)

    if _implementation_hashes() != implementation_hashes:
        raise RuntimeError("Prompt ablation implementation changed during execution")
    attempts = store.load_attempts()
    summary = _summarize(manifest, attempts)
    write_json(store.summary_json_path, summary)
    write_text(store.summary_markdown_path, _render_summary(summary))
    if all(cell["attempts"] == len(cases) for cell in summary["cells"]):
        _write_completion_receipt(store, implementation_hashes)
    return summary


def _run_cell(
    *,
    case: BenchmarkCase,
    schema: str,
    cell: str,
    adapter: AnthropicSDKAdapter,
    executor: MySQLExecutor,
    gold: QueryResult,
) -> dict:
    try:
        if cell == "lean-direct-prompt":
            _, prompt = build_model_only_prompt(case, schema, ContextMode.LLM_ENRICHED)
        elif cell == "detailed-product-prompt":
            prompt = PLAIN_SQL_ASK_PROMPT.format(
                database_engine=case.dialect,
                target_database=case.db_id,
                nl_question=case.question,
                provided_context_block="",
                matched_database_values_block="",
                filtered_schema=schema,
            )
        elif cell == "enum-grounded-product-prompt":
            prompt = PLAIN_SQL_ASK_PROMPT.format(
                database_engine=case.dialect,
                target_database=case.db_id,
                nl_question=case.question,
                provided_context_block="",
                matched_database_values_block="",
                filtered_schema=schema,
            ).replace("Requirements:\n", f"Requirements:\n{ENUM_GROUNDING_RULES}\n")
        else:
            raise ValueError(f"Unknown prompt ablation cell: {cell}")
        response = adapter.query(
            system_message=SQL_GENERATION_SYSTEM_PROMPT,
            user_query=prompt,
            max_tokens=800,
            temperature=0.0,
            purpose=f"ablation_{cell.replace('-', '_')}",
        )
        sql = extract_sql(response["text"])
    except Exception as exc:  # noqa: BLE001 - persist per-cell model failures
        return {"outcome": "generation_error", "error": str(exc)}

    validation = validate_sql_for_ask(sql, enforce_result_limit=False)
    if not validation["is_valid"]:
        return {
            "outcome": "validation_error",
            "error": "; ".join(validation.get("issues", [])),
            "generated_sql": sql,
            "validated_sql": validation.get("validated_sql"),
        }
    validated_sql = validation.get("validated_sql") or sql
    candidate = executor.execute(validated_sql, db_id=case.db_id)
    if not candidate.succeeded:
        return {
            "outcome": "execution_error",
            "error": candidate.error,
            "generated_sql": sql,
            "validated_sql": validated_sql,
        }
    score = score_results(candidate, gold)
    return {
        "outcome": "correct" if score.execution_correct else "incorrect_result",
        "execution_correct": score.execution_correct,
        "soft_f1": score.soft_f1,
        "generated_sql": sql,
        "validated_sql": validated_sql,
    }


def _summarize(manifest: dict, attempts: list[dict]) -> dict:
    cells = []
    by_cell: dict[str, dict[int, bool]] = {}
    cell_names = tuple(manifest.get("cells", PROMPT_ABLATION_CELLS))
    for cell in cell_names:
        rows = [row for row in attempts if row.get("scored") and row["cell"] == cell]
        by_cell[cell] = {
            int(row["question_id"]): bool(row.get("execution_correct")) for row in rows
        }
        cells.append(
            {
                "cell": cell,
                "attempts": len(rows),
                "correct": sum(by_cell[cell].values()),
                "accuracy": sum(by_cell[cell].values()) / len(rows) if rows else 0.0,
                "mean_latency_ms": mean(float(row["latency_ms"]) for row in rows)
                if rows
                else 0.0,
                "mean_input_tokens": mean(int(row["input_tokens"]) for row in rows)
                if rows
                else 0.0,
                "mean_output_tokens": mean(int(row["output_tokens"]) for row in rows)
                if rows
                else 0.0,
                "cost_usd": str(
                    sum(
                        (Decimal(row["normalized_cold_cost_usd"]) for row in rows),
                        Decimal(0),
                    )
                ),
            }
        )
    baseline_name = manifest.get("baseline_cell", "detailed-product-prompt")
    treatment_name = manifest.get("treatment_cell", "lean-direct-prompt")
    baseline = by_cell[baseline_name]
    treatment = by_cell[treatment_name]
    shared = sorted(set(baseline) & set(treatment))
    baseline_only = [qid for qid in shared if baseline[qid] and not treatment[qid]]
    treatment_only = [qid for qid in shared if treatment[qid] and not baseline[qid]]
    return {
        "artifact_schema_version": PROMPT_ABLATION_SCHEMA_VERSION,
        "run_id": manifest["run_id"],
        "source_run_id": manifest["source_run_id"],
        "case_count": len(manifest["case_ids"]),
        "cells": cells,
        "paired_comparison": {
            "baseline": baseline_name,
            "treatment": treatment_name,
            "delta_label": manifest.get("delta_label", "lean-prompt"),
            "paired_cases": len(shared),
            "accuracy_delta": (
                (len(treatment_only) - len(baseline_only)) / len(shared)
                if shared
                else 0.0
            ),
            "baseline_only_correct_ids": baseline_only,
            "treatment_only_correct_ids": treatment_only,
        },
    }


def _render_summary(summary: dict) -> str:
    lines = [
        "# User-Prompt Ablation",
        "",
        "Internal diagnostic only; neither cell is labeled `rdst-ask`.",
        "",
        "| Cell | Accuracy | Correct | Mean latency | Mean input | Mean output | Cost |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for cell in summary["cells"]:
        lines.append(
            f"| {cell['cell']} | {cell['accuracy']:.1%} | "
            f"{cell['correct']}/{cell['attempts']} | "
            f"{cell['mean_latency_ms'] / 1000:.2f}s | "
            f"{cell['mean_input_tokens']:.1f} | "
            f"{cell['mean_output_tokens']:.1f} | ${cell['cost_usd']} |"
        )
    paired = summary["paired_comparison"]
    lines.extend(
        [
            "",
            f"Paired {paired['delta_label']} delta: {paired['accuracy_delta']:+.1%}",
            "",
            f"{paired['baseline']} only correct: {paired['baseline_only_correct_ids']}",
            (
                f"{paired['treatment']} only correct: "
                f"{paired['treatment_only_correct_ids']}"
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _implementation_hashes() -> dict[str, str]:
    paths = (
        Path(__file__),
        RDST_ROOT / "devtools" / "ask_benchmark" / "cli.py",
        RDST_ROOT / "devtools" / "ask_benchmark" / "pipeline.py",
        RDST_ROOT / "features" / "ask" / "prompts" / "ask_prompts.py",
    )
    return {str(path.relative_to(RDST_ROOT)): _sha256_file(path) for path in paths}


def _write_completion_receipt(
    store: ArtifactStore, implementation_hashes: dict[str, str]
) -> None:
    receipt = {
        "artifact_schema_version": PROMPT_ABLATION_SCHEMA_VERSION,
        "run_id": store.run_dir.name,
        "manifest_sha256": _sha256_file(store.manifest_path),
        "attempts_sha256": _sha256_file(store.attempts_path),
        "calls_sha256": _sha256_file(store.call_receipts_path),
        "summary_sha256": _sha256_file(store.summary_json_path),
        "implementation_hashes": implementation_hashes,
    }
    path = store.run_dir / "completion-receipt.json"
    if path.exists() and _load_json(path) != receipt:
        raise ValueError("Prompt ablation completion receipt changed")
    if not path.exists():
        write_json(path, receipt)
