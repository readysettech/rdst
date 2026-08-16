from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from statistics import mean
from time import perf_counter
from uuid import uuid4

from features.ask.engine.ask3.phases.schema import _format_semantic_schema
from features.ask.sql_generation import generate_sql_from_nl
from features.ask.sql_validation import validate_sql_for_ask
from features.schema.semantic_layer.manager import SemanticLayerManager
from shared.persistence import write_json, write_text

from .anthropic_adapter import AnthropicSDKAdapter
from .artifacts import ArtifactStore
from .bird_dataset import DATASET_REVISION
from .executor import MySQLExecutor
from .models import BenchmarkCase, ContextMode, ModelSpec, QueryResult
from .oracle import score_results
from .pipeline import build_model_only_prompt, extract_sql

ABLATION_SCHEMA_VERSION = 1
ABLATION_CELLS = (
    "full-sql-only",
    "filtered-sql-only",
    "full-structured",
    "filtered-structured",
)


def run_generation_ablation(
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
) -> dict:
    """Run a frozen 2x2 schema/prompt generation diagnostic.

    This is intentionally not an rdst-ask cohort. It reuses only the immutable
    filtered-table decisions from a completed canonical AskService run, then
    crosses schema scope with output contract while holding everything else fixed.
    """
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
    source_by_id = {int(row["question_id"]): row for row in source_attempts}

    context_provenance_path = semantic_dir / "provenance.json"
    output_dir.mkdir(parents=True, exist_ok=True)
    store = ArtifactStore(output_dir)
    manifest = {
        "artifact_schema_version": ABLATION_SCHEMA_VERSION,
        "run_id": output_dir.name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "internal-generation-ablation",
        "publication_label": "internal; never rdst-ask",
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
        "cells": list(ABLATION_CELLS),
        "question_policy": (
            "original BIRD question; no evidence or clarification injection"
        ),
        "schema_policy": {
            "full": "complete frozen LLM-enriched database schema",
            "filtered": "tables frozen in the source AskService attempt",
        },
        "generation_policy": {
            "sql-only": "minimal direct SQL-only prompt",
            "structured": "production RDST structured generation prompt",
            "temperature": 0.0,
            "max_tokens": 800,
            "cell_order": "case-index-rotated-v1",
        },
        "model": asdict(model_spec),
        "route_check": route_check,
        "database_provision": database_provision,
        "query_bounds": asdict(executor.bounds),
        "benchmark_protocol_sha256": protocol_sha256,
    }
    _initialize_manifest(store, manifest)
    completed = store.completed_keys()
    manager = SemanticLayerManager(base_dir=semantic_dir)
    adapter = AnthropicSDKAdapter(model_spec)
    adapter.set_call_receipt_sink(store.append_call_receipt)
    gold = {
        case.question_id: executor.execute(case.gold_sql, db_id=case.db_id)
        for case in cases
    }
    failed_gold = [case_id for case_id, result in gold.items() if not result.succeeded]
    if failed_gold:
        raise RuntimeError(f"Ablation gold execution failed: {failed_gold}")

    with store.run_lock():
        for case_index, case in enumerate(cases):
            layer = manager.load(case.db_id, use_cache=False)
            full_schema = _format_semantic_schema(layer)
            filtered_tables = source_by_id[case.question_id]["diagnostics"][
                "filtered_tables"
            ]
            filtered_layer = deepcopy(layer)
            filtered_layer.tables = {
                name: layer.tables[name]
                for name in filtered_tables
                if name in layer.tables
            }
            if set(filtered_layer.tables) != set(filtered_tables):
                missing = sorted(set(filtered_tables) - set(filtered_layer.tables))
                raise ValueError(
                    "Source filtered tables are absent for "
                    f"{case.question_id}: {missing}"
                )
            filtered_schema = _format_semantic_schema(filtered_layer)
            schemas = {"full": full_schema, "filtered": filtered_schema}

            cell_order = (
                ABLATION_CELLS[case_index % len(ABLATION_CELLS) :]
                + ABLATION_CELLS[: case_index % len(ABLATION_CELLS)]
            )
            for cell in cell_order:
                attempt_key = hashlib.sha256(
                    f"{manifest['source_attempts_sha256']}:{cell}:{case.question_id}".encode()
                ).hexdigest()
                if attempt_key in completed:
                    continue
                schema_scope, contract = cell.split("-", 1)
                invocation_id = uuid4().hex
                adapter.set_attempt_id(invocation_id)
                started = perf_counter()
                result = _run_cell(
                    case=case,
                    schema=schemas[schema_scope],
                    schema_scope=schema_scope,
                    contract=contract,
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
                        "schema_scope": schema_scope,
                        "generation_contract": contract,
                        "scored": not any(call.error for call in calls),
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
                        "schema_sha256": hashlib.sha256(
                            schemas[schema_scope].encode()
                        ).hexdigest(),
                        "schema_chars": len(schemas[schema_scope]),
                        "filtered_tables": filtered_tables,
                    }
                )
                completed.add(attempt_key)

    attempts = store.load_attempts()
    summary = _summarize(manifest, attempts)
    write_json(store.summary_json_path, summary)
    write_text(store.summary_markdown_path, _render_summary(summary))
    return summary


def _run_cell(
    *,
    case: BenchmarkCase,
    schema: str,
    schema_scope: str,
    contract: str,
    adapter: AnthropicSDKAdapter,
    executor: MySQLExecutor,
    gold: QueryResult,
) -> dict:
    stage = f"ablation_{schema_scope}_{contract.replace('-', '_')}"
    try:
        if contract == "sql-only":
            system, prompt = build_model_only_prompt(
                case, schema, ContextMode.LLM_ENRICHED
            )
            response = adapter.query(
                system_message=system,
                user_query=prompt,
                max_tokens=800,
                temperature=0.0,
                purpose=stage,
            )
            sql = extract_sql(response["text"])
        elif contract == "structured":
            response = generate_sql_from_nl(
                nl_question=case.question,
                filtered_schema=schema,
                database_engine=case.dialect,
                target_database=case.db_id,
                llm_manager=adapter,
                purpose=stage,
            )
            if not response.get("success"):
                return {
                    "outcome": "generation_error",
                    "error": response.get("error", "structured generation failed"),
                    "generated_sql": response.get("sql"),
                }
            sql = response["sql"]
        else:
            raise ValueError(f"Unknown generation contract: {contract}")
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


def _validate_source(
    manifest: dict, attempts: list[dict], cases: list[BenchmarkCase]
) -> None:
    if manifest.get("track") != "rdst-ask":
        raise ValueError("Generation ablation source must be a canonical rdst-ask run")
    if manifest.get("dataset_revision") != DATASET_REVISION:
        raise ValueError("Generation ablation source has a different dataset revision")
    if manifest.get("context_mode") != "llm-enriched":
        raise ValueError("Generation ablation source must use llm-enriched context")
    if manifest.get("suite") != "smoke":
        raise ValueError("Generation ablation source must be the frozen smoke suite")
    if manifest.get("interaction_mode") != "auto":
        raise ValueError("Generation ablation source must use auto interaction mode")
    expected = {case.question_id for case in cases}
    if manifest.get("case_ids") != [case.question_id for case in cases]:
        raise ValueError(
            "Source manifest case order differs from the frozen smoke suite"
        )
    scored = [row for row in attempts if row.get("scored")]
    actual = {int(row["question_id"]) for row in scored}
    if actual != expected:
        raise ValueError("Source run does not have complete scored smoke coverage")
    if len(scored) != len(expected):
        raise ValueError(
            "Source run must have exactly one scored attempt per smoke case"
        )
    if any(not row.get("diagnostics", {}).get("filtered_tables") for row in scored):
        raise ValueError("Source run is missing frozen filtered-table diagnostics")


def _verify_source_environment(
    manifest: dict,
    *,
    context_provenance: dict,
    database_provision: dict,
) -> None:
    source_context = manifest.get("context_provenance") or {}
    if source_context.get("output_hashes") != context_provenance.get("output_hashes"):
        raise ValueError("Source run used different LLM-enriched schema snapshots")
    source_database = manifest.get("database_provision") or {}
    identity_fields = (
        "archive_sha256",
        "mysql_image_id",
        "mysql_version",
        "database_prefix",
    )
    if any(
        source_database.get(key) != database_provision.get(key)
        for key in identity_fields
    ):
        raise ValueError("Source run used a different database provision")


def _initialize_manifest(store: ArtifactStore, manifest: dict) -> None:
    if store.manifest_path.exists():
        existing = _load_json(store.manifest_path)
        comparable_existing = {k: v for k, v in existing.items() if k != "created_at"}
        comparable_new = {k: v for k, v in manifest.items() if k != "created_at"}
        if comparable_existing != comparable_new:
            raise ValueError("Generation ablation manifest differs from existing run")
        return
    store.initialize(manifest)


def _summarize(manifest: dict, attempts: list[dict]) -> dict:
    cells = []
    for cell in ABLATION_CELLS:
        rows = [row for row in attempts if row["cell"] == cell and row.get("scored")]
        cells.append(
            {
                "cell": cell,
                "attempts": len(rows),
                "correct": sum(bool(row.get("execution_correct")) for row in rows),
                "accuracy": (
                    sum(bool(row.get("execution_correct")) for row in rows) / len(rows)
                    if rows
                    else 0.0
                ),
                "mean_latency_ms": mean(float(row["latency_ms"]) for row in rows)
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
    return {
        "artifact_schema_version": ABLATION_SCHEMA_VERSION,
        "run_id": manifest["run_id"],
        "source_run_id": manifest["source_run_id"],
        "case_count": len(manifest["case_ids"]),
        "cells": cells,
        "contrasts": _contrasts(attempts),
    }


def _contrasts(attempts: list[dict]) -> list[dict]:
    pairs = (
        ("full-sql-only", "filtered-sql-only", "schema effect under SQL-only"),
        ("full-structured", "filtered-structured", "schema effect under structured"),
        ("full-sql-only", "full-structured", "contract effect under full schema"),
        (
            "filtered-sql-only",
            "filtered-structured",
            "contract effect under filtered schema",
        ),
    )
    by_cell = {
        cell: {
            int(row["question_id"]): bool(row.get("execution_correct"))
            for row in attempts
            if row.get("scored") and row.get("cell") == cell
        }
        for cell in ABLATION_CELLS
    }
    contrasts = []
    for baseline, treatment, label in pairs:
        shared = sorted(set(by_cell[baseline]) & set(by_cell[treatment]))
        baseline_only = [
            case_id
            for case_id in shared
            if by_cell[baseline][case_id] and not by_cell[treatment][case_id]
        ]
        treatment_only = [
            case_id
            for case_id in shared
            if by_cell[treatment][case_id] and not by_cell[baseline][case_id]
        ]
        contrasts.append(
            {
                "label": label,
                "baseline": baseline,
                "treatment": treatment,
                "paired_cases": len(shared),
                "accuracy_delta": (
                    (len(treatment_only) - len(baseline_only)) / len(shared)
                    if shared
                    else 0.0
                ),
                "baseline_only_correct_ids": baseline_only,
                "treatment_only_correct_ids": treatment_only,
            }
        )
    return contrasts


def _render_summary(summary: dict) -> str:
    lines = [
        "# Frozen Generation Ablation",
        "",
        "Internal diagnostic only; no cell is labeled `rdst-ask`.",
        "",
        "| Cell | Accuracy | Correct | Mean latency | Mean output | Cost |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for cell in summary["cells"]:
        lines.append(
            f"| {cell['cell']} | {cell['accuracy']:.1%} | "
            f"{cell['correct']}/{cell['attempts']} | "
            f"{cell['mean_latency_ms'] / 1000:.2f}s | "
            f"{cell['mean_output_tokens']:.1f} | ${cell['cost_usd']} |"
        )
    lines.append("")
    lines.extend(
        [
            "| Contrast | Paired delta | Baseline-only | Treatment-only |",
            "|---|---:|---|---|",
        ]
    )
    for contrast in summary["contrasts"]:
        lines.append(
            f"| {contrast['label']} | {contrast['accuracy_delta']:+.1%} | "
            f"{contrast['baseline_only_correct_ids']} | "
            f"{contrast['treatment_only_correct_ids']} |"
        )
    lines.append("")
    return "\n".join(lines)


def _load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"Expected JSON object in {path}")
    return value


def _load_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _case_inputs_sha256(cases: list[BenchmarkCase]) -> str:
    payload = [asdict(case) for case in cases]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
