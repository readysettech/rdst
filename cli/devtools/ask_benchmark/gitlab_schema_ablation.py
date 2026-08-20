"""Small GitLab verbose versus compact full-schema Ask diagnostic.

This is not an execution-accuracy benchmark. The public GitLab schema contains no
row data, so the diagnostic measures required-table retention, generated SQL schema
validity, latency, tokens, and cost. SQL semantics remain subject to manual review.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Any
from uuid import uuid4

import sqlglot
from sqlglot import exp

from features.ask.engine.ask3.phases.schema import (
    COMPACT_SCHEMA_FORMAT_VERSION,
    _format_semantic_schema,
    format_semantic_schema_compact,
)
from features.ask.events import AskErrorEvent, AskResultEvent
from features.ask.models import AskInput, AskOptions
from features.ask.service import AskService
from features.schema.semantic_layer.manager import SemanticLayerManager
from shared.persistence import write_json, write_text

from .anthropic_adapter import AnthropicSDKAdapter
from .artifacts import ArtifactStore
from .config import load_model_specs
from .models import ModelCallRecord

GITLAB_REVISION = "09c257b5e79181d3efa7a20721cd2583680e0aed"
TARGET = "gitlab-auto-init"
MODEL_NAME = "claude-sonnet-4.6-anthropic-sdk"
SCHEMA_VERSION = 2
SUPPORTED_CONDITIONS = ("full", "compact")
DEFAULT_CONDITIONS = SUPPORTED_CONDITIONS


@dataclass(frozen=True)
class GitLabCase:
    case_id: int
    question: str
    required_tables: tuple[str, ...]


CASES = (
    GitLabCase(
        1,
        "List the ID, name, namespace path, and last activity time for every "
        "non-archived project, newest activity first.",
        ("projects", "namespaces"),
    ),
    GitLabCase(
        2,
        "For each project, return its ID, name, and the number of issues whose "
        "closed_at value is null, highest count first.",
        ("projects", "issues"),
    ),
    GitLabCase(
        3,
        "List merge request ID, title, author username, and target project name "
        "for merge requests created in the last 30 days.",
        ("merge_requests", "users", "projects"),
    ),
    GitLabCase(
        4,
        "List unresolved non-system notes on merge requests, with note text, "
        "author username, and project ID. A note is unresolved when resolved_at "
        "is null.",
        ("notes", "merge_requests", "users"),
    ),
    GitLabCase(
        5,
        "List failed CI jobs from the last 7 days with job ID, job name, pipeline "
        "ID, and pipeline ref.",
        ("p_ci_builds", "p_ci_pipelines"),
    ),
    GitLabCase(
        6,
        "List active runners that have not contacted GitLab in 30 days, with "
        "runner ID, description, and the number of associated CI builds.",
        ("ci_runners", "p_ci_builds"),
    ),
    GitLabCase(
        7,
        "List deployments created in the last 30 days to environments named "
        "production, returning project name, environment name, deployment ref, "
        "and deployer username.",
        ("deployments", "environments", "projects", "users"),
    ),
    GitLabCase(
        8,
        "List project memberships expiring in the next 30 days, returning project "
        "name, username, access level, and expiration date.",
        ("members", "projects", "users"),
    ),
    GitLabCase(
        9,
        "List protected branches that allow force pushes, with project name and "
        "each configured push access level.",
        (
            "protected_branches",
            "protected_branch_push_access_levels",
            "projects",
        ),
    ),
    GitLabCase(
        10,
        "Return each package name and version, its project name, and total bytes "
        "across its package files, largest total first.",
        ("packages_packages", "packages_package_files", "projects"),
    ),
    GitLabCase(
        11,
        "List each vulnerability with its finding name, project name, severity, "
        "state, and detection time.",
        ("vulnerabilities", "vulnerability_occurrences", "projects"),
    ),
    GitLabCase(
        12,
        "Find successful pipelines that contain at least one failed job, returning "
        "project name, pipeline ID, pipeline ref, and failed job count.",
        ("p_ci_pipelines", "p_ci_builds", "projects"),
    ),
)


class _TargetsConfig:
    def __init__(self, target: str, config: dict[str, Any]):
        self.target = target
        self.config = config

    def load(self) -> None:
        return None

    def get_default(self) -> str:
        return self.target

    def get(self, target: str) -> dict[str, Any] | None:
        return self.config if target == self.target else None


class _CallBudget:
    def __init__(
        self,
        *,
        store: ArtifactStore,
        max_calls: int,
        max_cost_usd: Decimal,
        max_wall_seconds: float,
    ):
        receipts = store.load_call_receipts()
        self.calls = len(receipts)
        self.cost = sum(
            (
                Decimal(str(receipt.get("normalized_cold_cost_usd", "0")))
                for receipt in receipts
            ),
            Decimal(0),
        )
        self.max_calls = max_calls
        self.max_cost_usd = max_cost_usd
        self.max_wall_seconds = max_wall_seconds
        self.started = perf_counter()

    def before_call(self) -> None:
        if self.calls >= self.max_calls:
            raise RuntimeError(f"provider-call limit reached ({self.calls})")
        if self.cost >= self.max_cost_usd:
            raise RuntimeError(f"normalized-cost limit reached (${self.cost})")
        if perf_counter() - self.started >= self.max_wall_seconds:
            raise RuntimeError("wall-time limit reached")

    def record(self, call: ModelCallRecord | dict[str, Any]) -> None:
        self.calls += 1
        value = (
            call.normalized_cold_cost_usd
            if isinstance(call, ModelCallRecord)
            else call.get("normalized_cold_cost_usd", "0")
        )
        self.cost += Decimal(str(value))


class _BudgetedAdapter:
    def __init__(self, adapter: AnthropicSDKAdapter, budget: _CallBudget):
        self.adapter = adapter
        self.budget = budget

    def __getattr__(self, name: str) -> Any:
        return getattr(self.adapter, name)

    def query(self, **kwargs: Any) -> dict[str, Any]:
        self.budget.before_call()
        return self.adapter.query(**kwargs)

    def generate_response(self, **kwargs: Any) -> dict[str, Any]:
        self.budget.before_call()
        return self.adapter.generate_response(**kwargs)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _payload_sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _attempt_key(
    schema_sha256: str,
    schema_prompt_sha256: str,
    case: GitLabCase,
    condition: str,
) -> str:
    return _payload_sha256(
        {
            "schema_sha256": schema_sha256,
            "schema_prompt_sha256": schema_prompt_sha256,
            "case": asdict(case),
            "condition": condition,
            "model": MODEL_NAME,
            "protocol": SCHEMA_VERSION,
        }
    )


def _sql_tables(sql: str | None) -> set[str]:
    if not sql:
        return set()
    try:
        tree = sqlglot.parse_one(sql, read="postgres")
    except sqlglot.errors.ParseError:
        return set()
    ctes = {
        cte.alias_or_name.casefold()
        for cte in tree.find_all(exp.CTE)
        if cte.alias_or_name
    }
    return {
        table.name.casefold()
        for table in tree.find_all(exp.Table)
        if table.name and table.name.casefold() not in ctes
    }


async def _collect(service: AskService, question: str) -> list[Any]:
    return [
        event
        async for event in service.ask(
            AskInput(question=question, target=TARGET, source="benchmark"),
            AskOptions(
                dry_run=True,
                no_interactive=True,
                enforce_result_limit=False,
                persist_query=False,
                raise_unexpected_errors=True,
            ),
        )
    ]


def _run_attempt(
    *,
    case: GitLabCase,
    condition: str,
    semantic_manager: SemanticLayerManager,
    adapter: _BudgetedAdapter,
    schema_sha256: str,
    schema_prompt_sha256: str,
) -> dict[str, Any]:
    invocation_id = uuid4().hex
    adapter.set_attempt_id(invocation_id)
    phases: list[dict[str, Any]] = []

    def observe(phase: str, ctx: Any) -> None:
        schema_tables = list(ctx.schema_info.tables) if ctx.schema_info else []
        phases.append(
            {
                "phase": phase,
                "schema_chars": len(ctx.schema_formatted or ""),
                "schema_format": getattr(ctx, "schema_format", ""),
                "schema_format_policy": getattr(ctx, "schema_format_policy", ""),
                "schema_verbose_chars": getattr(ctx, "schema_verbose_chars", 0),
                "schema_compact_chars": getattr(ctx, "schema_compact_chars", 0),
                "schema_compact_savings_ratio": getattr(
                    ctx, "schema_compact_savings_ratio", 0.0
                ),
                "schema_table_count": len(schema_tables),
                "schema_tables": schema_tables,
                "retry_count": ctx.retry_count,
            }
        )

    config = {"engine": "postgresql", "database": "gitlab", "read_only": True}
    service = AskService(
        llm_manager=adapter,
        semantic_manager=semantic_manager,
        targets_config_factory=lambda: _TargetsConfig(TARGET, config),
        persist_queries=False,
        session_store={},
        phase_observer=observe,
        diagnostic_schema_formatter_fn=(
            format_semantic_schema_compact
            if condition == "compact"
            else _format_semantic_schema
        ),
    )
    started = perf_counter()
    error = None
    events: list[Any] = []
    try:
        events = asyncio.run(_collect(service, case.question))
    except Exception as exc:  # noqa: BLE001 - persist each paid-attempt failure
        error = str(exc)
    latency_ms = (perf_counter() - started) * 1000
    calls = adapter.drain_call_records()
    result = next(
        (event for event in reversed(events) if isinstance(event, AskResultEvent)),
        None,
    )
    error_event = next(
        (event for event in reversed(events) if isinstance(event, AskErrorEvent)),
        None,
    )
    if error_event is not None:
        error = error_event.message
    sql = result.sql if result is not None else None
    schema_phase = next(
        (phase for phase in reversed(phases) if phase["phase"] == "schema"), {}
    )
    schema_tables = {
        str(table).casefold() for table in schema_phase.get("schema_tables", [])
    }
    required_tables = {table.casefold() for table in case.required_tables}
    generated_tables = _sql_tables(sql)
    return {
        "attempt_key": _attempt_key(
            schema_sha256, schema_prompt_sha256, case, condition
        ),
        "invocation_id": invocation_id,
        "case_id": case.case_id,
        "condition": condition,
        "schema_prompt_sha256": schema_prompt_sha256,
        "schema_format_version": (
            COMPACT_SCHEMA_FORMAT_VERSION if condition == "compact" else "verbose-v1"
        ),
        "question": case.question,
        "required_tables": sorted(required_tables),
        "schema_tables": sorted(schema_tables),
        "schema_required_table_recall": (
            len(required_tables & schema_tables) / len(required_tables)
        ),
        "generated_sql": sql,
        "generated_tables": sorted(generated_tables),
        "generated_required_table_recall": (
            len(required_tables & generated_tables) / len(required_tables)
        ),
        "unexpected_generated_tables": sorted(generated_tables - required_tables),
        "success": result is not None,
        "scored": error is None,
        "error": error,
        "latency_ms": latency_ms,
        "input_tokens": sum(call.input_tokens for call in calls),
        "output_tokens": sum(call.output_tokens for call in calls),
        "normalized_cold_cost_usd": str(
            sum((call.normalized_cold_cost_usd for call in calls), Decimal(0))
        ),
        "call_count": len(calls),
        "calls_by_stage": [call.stage for call in calls],
        "phases": phases,
    }


def _summary(
    manifest: dict[str, Any], attempts: list[dict[str, Any]]
) -> dict[str, Any]:
    selected_conditions = tuple(manifest.get("conditions", DEFAULT_CONDITIONS))
    conditions = []
    for condition in selected_conditions:
        rows = [
            row
            for row in attempts
            if row["condition"] == condition and row.get("scored")
        ]
        conditions.append(
            {
                "condition": condition,
                "attempts": len(rows),
                "successful": sum(bool(row["success"]) for row in rows),
                "mean_schema_required_table_recall": mean(
                    row["schema_required_table_recall"] for row in rows
                )
                if rows
                else 0.0,
                "mean_generated_required_table_recall": mean(
                    row["generated_required_table_recall"] for row in rows
                )
                if rows
                else 0.0,
                "all_required_tables_generated": sum(
                    row["generated_required_table_recall"] == 1.0 for row in rows
                ),
                "mean_schema_tables": mean(len(row["schema_tables"]) for row in rows)
                if rows
                else 0.0,
                "min_schema_tables": min(
                    (len(row["schema_tables"]) for row in rows), default=0
                ),
                "max_schema_tables": max(
                    (len(row["schema_tables"]) for row in rows), default=0
                ),
                "mean_latency_ms": mean(row["latency_ms"] for row in rows)
                if rows
                else 0.0,
                "input_tokens": sum(row["input_tokens"] for row in rows),
                "output_tokens": sum(row["output_tokens"] for row in rows),
                "cost_usd": str(
                    sum(
                        (Decimal(row["normalized_cold_cost_usd"]) for row in rows),
                        Decimal(0),
                    )
                ),
            }
        )
    scored_by_condition = {
        condition: {
            int(row["case_id"])
            for row in attempts
            if row["condition"] == condition and row.get("scored")
        }
        for condition in selected_conditions
    }
    completed_paired_case_ids = sorted(
        set.intersection(
            *(scored_by_condition[condition] for condition in selected_conditions)
        )
    )
    budget_stops = [
        {
            "case_id": int(row["case_id"]),
            "condition": str(row["condition"]),
            "error": str(row.get("error") or ""),
        }
        for row in attempts
        if not row.get("scored")
    ]
    return {
        "artifact_schema_version": SCHEMA_VERSION,
        "run_id": manifest["run_id"],
        "purpose": manifest["purpose"],
        "table_count": int(manifest["table_count"]),
        "verbose_schema_chars": int(manifest["formatted_schema_chars"]),
        "compact_schema_chars": int(manifest["compact_formatted_schema_chars"]),
        "compact_to_verbose_char_ratio": float(
            manifest["compact_to_verbose_char_ratio"]
        ),
        "planned_case_count": len(CASES),
        "completed_paired_case_count": len(completed_paired_case_ids),
        "completed_paired_case_ids": completed_paired_case_ids,
        "unscored_stops": budget_stops,
        "conditions": conditions,
        "semantic_accuracy_policy": (
            "not scored because the public schema contains no row data; review "
            "generated SQL side by side"
        ),
    }


def _markdown(summary: dict[str, Any], attempts: list[dict[str, Any]]) -> str:
    lines = [
        "# GitLab schema-prompt diagnostic",
        "",
        "Internal diagnostic only. This artifact does not claim execution accuracy.",
        "",
        (
            "Frozen prompt sizes: "
            f"{summary['verbose_schema_chars']:,} verbose characters and "
            f"{summary['compact_schema_chars']:,} compact characters "
            f"({summary['compact_to_verbose_char_ratio']:.1%})."
        ),
        "",
        (
            f"Completed {summary['completed_paired_case_count']}/"
            f"{summary['planned_case_count']} paired questions. Unscored budget-stop "
            "records are excluded from every aggregate."
        ),
        "",
        "| Condition | Mean tables sent | Range | Share of schema |",
        "|---|---:|---:|---:|",
    ]
    total_tables = int(summary["table_count"])
    for row in summary["conditions"]:
        lines.append(
            f"| {row['condition']} | {row['mean_schema_tables']:.1f} | "
            f"{row['min_schema_tables']}-{row['max_schema_tables']} | "
            f"{row['mean_schema_tables'] / total_tables:.1%} |"
        )
    lines.extend(
        [
            "",
            "| Condition | Completed | Schema recall | SQL recall | All required | Mean latency | Input tokens | Output tokens | Cost |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in summary["conditions"]:
        lines.append(
            f"| {row['condition']} | {row['successful']}/{row['attempts']} | "
            f"{row['mean_schema_required_table_recall']:.1%} | "
            f"{row['mean_generated_required_table_recall']:.1%} | "
            f"{row['all_required_tables_generated']}/{row['attempts']} | "
            f"{row['mean_latency_ms'] / 1000:.2f}s | {row['input_tokens']} | "
            f"{row['output_tokens']} | ${row['cost_usd']} |"
        )
    lines.extend(["", "## Generated SQL", ""])
    by_case = {(int(row["case_id"]), str(row["condition"])): row for row in attempts}
    for case in CASES:
        lines.extend([f"### {case.case_id}. {case.question}", ""])
        lines.append(f"Required tables: `{', '.join(case.required_tables)}`")
        lines.append("")
        for condition in (row["condition"] for row in summary["conditions"]):
            row = by_case.get((case.case_id, condition))
            lines.append(f"#### {condition}")
            lines.append("")
            if row is None:
                lines.append("Not run.")
            elif row.get("generated_sql"):
                lines.extend(["```sql", row["generated_sql"], "```"])
                lines.append(
                    f"Tables: `{', '.join(row['generated_tables'])}`. "
                    f"Schema recall: {row['schema_required_table_recall']:.0%}. "
                    f"Latency: {row['latency_ms'] / 1000:.2f}s. "
                    f"Cost: ${row['normalized_cold_cost_usd']}."
                )
            else:
                lines.append(f"Error: {row.get('error') or 'no SQL returned'}")
            lines.append("")
    return "\n".join(lines)


def run(args: argparse.Namespace) -> int:
    conditions = tuple(args.conditions)
    if not conditions or len(set(conditions)) != len(conditions):
        raise ValueError("Choose one or more unique diagnostic conditions")
    unsupported = sorted(set(conditions) - set(SUPPORTED_CONDITIONS))
    if unsupported:
        raise ValueError(f"Unsupported diagnostic conditions: {unsupported}")
    semantic_path = args.semantic_dir / f"{TARGET}.yaml"
    if not semantic_path.exists():
        raise FileNotFoundError(f"Missing frozen auto-init schema: {semantic_path}")
    provenance_path = args.semantic_dir.parent / "provenance.json"
    schema_sha256 = _sha256(semantic_path)
    model = load_model_specs()[MODEL_NAME]
    manager = SemanticLayerManager(base_dir=args.semantic_dir)
    layer = manager.load(TARGET, use_cache=False)
    expected_tables = {
        table.casefold() for case in CASES for table in case.required_tables
    }
    available_tables = {table.casefold() for table in layer.tables}
    missing_expected = sorted(expected_tables - available_tables)
    if missing_expected:
        raise ValueError(f"GitLab auto-init schema is missing: {missing_expected}")
    formatted_schema = _format_semantic_schema(layer)
    compact_formatted_schema = format_semantic_schema_compact(layer)
    formatted_schema_sha256 = _payload_sha256(formatted_schema)
    compact_formatted_schema_sha256 = _payload_sha256(compact_formatted_schema)
    output_dir = args.output / args.run_id
    store = ArtifactStore(output_dir)
    manifest = {
        "artifact_schema_version": SCHEMA_VERSION,
        "run_id": args.run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "internal-gitlab-schema-prompt-ablation",
        "publication_label": "internal diagnostic; never rdst-ask accuracy",
        "source_revision": GITLAB_REVISION,
        "schema_provenance_sha256": (
            _sha256(provenance_path) if provenance_path.exists() else None
        ),
        "schema_sha256": schema_sha256,
        "schema_yaml_bytes": semantic_path.stat().st_size,
        "formatted_schema_chars": len(formatted_schema),
        "formatted_schema_sha256": formatted_schema_sha256,
        "compact_schema_format_version": COMPACT_SCHEMA_FORMAT_VERSION,
        "compact_formatted_schema_chars": len(compact_formatted_schema),
        "compact_formatted_schema_sha256": compact_formatted_schema_sha256,
        "compact_to_verbose_char_ratio": (
            len(compact_formatted_schema) / len(formatted_schema)
        ),
        "table_count": len(layer.tables),
        "column_count": sum(len(table.columns) for table in layer.tables.values()),
        "relationship_count": sum(
            len(table.relationships) for table in layer.tables.values()
        ),
        "schema_policy": "frozen RDST auto-init with no row samples or AI descriptions",
        "questions": [asdict(case) for case in CASES],
        "questions_sha256": _payload_sha256([asdict(case) for case in CASES]),
        "conditions": list(conditions),
        "condition_order": "case-parity-rotated-v1",
        "model": asdict(model),
        "interaction_mode": "noninteractive-deterministic-only",
        "dry_run": True,
        "execution_accuracy_scored": False,
        "run_limits": {
            "max_provider_calls": args.max_calls,
            "max_normalized_cost_usd": str(args.max_cost_usd),
            "max_wall_time_seconds": args.max_wall_seconds,
        },
    }
    if store.manifest_path.exists():
        existing = json.loads(store.manifest_path.read_text(encoding="utf-8"))
        if {k: v for k, v in existing.items() if k != "created_at"} != {
            k: v for k, v in manifest.items() if k != "created_at"
        }:
            raise ValueError("Existing GitLab diagnostic manifest differs")
    else:
        store.initialize(manifest)

    budget = _CallBudget(
        store=store,
        max_calls=args.max_calls,
        max_cost_usd=args.max_cost_usd,
        max_wall_seconds=args.max_wall_seconds,
    )
    inner = AnthropicSDKAdapter(model)

    def persist_call(call: ModelCallRecord) -> None:
        store.append_call_receipt(call)
        budget.record(call)

    inner.set_call_receipt_sink(persist_call)
    adapter = _BudgetedAdapter(inner, budget)
    completed = store.completed_keys()

    with store.run_lock():
        for case_index, case in enumerate(CASES):
            order = conditions if case_index % 2 == 0 else tuple(reversed(conditions))
            for condition in order:
                schema_prompt_sha256 = (
                    compact_formatted_schema_sha256
                    if condition == "compact"
                    else formatted_schema_sha256
                )
                key = _attempt_key(schema_sha256, schema_prompt_sha256, case, condition)
                if key in completed:
                    continue
                attempt = _run_attempt(
                    case=case,
                    condition=condition,
                    semantic_manager=manager,
                    adapter=adapter,
                    schema_sha256=schema_sha256,
                    schema_prompt_sha256=schema_prompt_sha256,
                )
                store.append_attempt(attempt)
                if attempt["scored"]:
                    completed.add(key)

    attempts = store.load_attempts()
    summary = _summary(manifest, attempts)
    write_json(store.summary_json_path, summary)
    write_text(store.summary_markdown_path, _markdown(summary, attempts))
    print(store.summary_markdown_path)
    return 0


def main() -> int:
    default_semantic = (
        Path.home() / ".cache/rdst/benchmarks/gitlab-schema-ablation/09c257b5/auto-init"
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--semantic-dir", type=Path, default=default_semantic)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("test-results/ask_benchmark"),
    )
    parser.add_argument("--run-id", default="gitlab-auto-init-schema-prompt-12-v3")
    parser.add_argument(
        "--conditions",
        nargs="+",
        choices=SUPPORTED_CONDITIONS,
        default=DEFAULT_CONDITIONS,
        help="Paired full-schema prompt conditions to run (default: full compact)",
    )
    parser.add_argument("--max-calls", type=int, default=48)
    parser.add_argument("--max-cost-usd", type=Decimal, default=Decimal(12))
    parser.add_argument("--max-wall-seconds", type=float, default=3600)
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
