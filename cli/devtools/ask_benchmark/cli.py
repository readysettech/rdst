from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path, PurePosixPath
from time import perf_counter
from typing import Any
from uuid import uuid4

import psycopg2
from filelock import FileLock, Timeout

try:
    import fcntl
except ImportError:  # pragma: no cover - retain exclusive locking without flock.
    fcntl = None

from features.ask.ambiguity_detection import (
    AMBIGUITY_RESPONSE_MAX_TOKENS,
    NON_INTERACTIVE_CLARIFICATION_POLICY,
)
from features.ask.engine.ask3.phases.schema import (
    ADAPTIVE_SCHEMA_FORMAT_VERSION,
    COMPACT_SCHEMA_FORMAT_VERSION,
)
from features.schema.semantic_layer.ai_annotator import (
    SAMPLE_ROWS_PER_PROMPT,
    AIAnnotator,
)
from shared.persistence import require_safe_file_stem, write_json

from .anthropic_adapter import AnthropicSDKAdapter
from .artifacts import ArtifactStore
from .bird_conformance import (
    OFFICIAL_EVALUATOR_FILES,
    OFFICIAL_EVALUATOR_REVISION,
    conformance_receipt_path,
    prepare_official_evaluator,
    verify_conformance_receipt,
    verify_full_candidate_replay,
    verify_official_oracle_conformance,
)
from .bird_dataset import (
    BIRD_MINI_DATABASE_IDS,
    DATASET_REVISION,
    GOLD_EXCLUSION_REVISION,
    MYSQL_INVALID_GOLD_CASE_IDS,
    SCORABLE_CASE_COUNT,
    default_cache_dir,
    download_metadata,
    exclude_invalid_gold_cases,
    load_cached_cases,
    partition_provenance,
    select_canary,
    select_holdout,
    select_pipeline_smoke,
)
from .bird_interact import (
    BIRD_INTERACT_POSTGRES_IMAGE,
    BIRD_INTERACT_QUALIFICATION_CASE_IDS,
    BIRD_INTERACT_SOURCE_REVISION,
    PostgresConnectionConfig,
    PostgresQualificationExecutor,
    PostgresQueryBounds,
    default_bird_interact_cache_dir,
    load_frozen_qualification_cases,
    prepare_frozen_qualification_inputs,
    rescore_frozen_qualification,
    run_frozen_qualification,
    verify_postgres_provision,
    write_preparation_receipt,
)
from .clarification_qualification import (
    build_gold_alignment_packet,
    load_gold_alignment_fixture,
    score_gold_alignment_packet,
)
from .claude_subscription_adapter import (
    CLAUDE_SUBSCRIPTION_MODEL,
    CLAUDE_SUBSCRIPTION_TRANSPORT,
    PINNED_CLAUDE_CODE_VERSION,
)
from .comparison import compare_runs
from .config import load_filter_spec, load_model_specs, select_models
from .dashboard import write_dashboard
from .executor import (
    MySQLConnectionConfig,
    MySQLExecutor,
    QueryBounds,
)
from .generation_ablation import run_generation_ablation
from .gold_replay import (
    compare_gold_replays,
    replay_gold_results,
    verify_replay_provenance,
    verify_replay_receipt,
)
from .models import ContextMode, EvaluationTrack, InteractionMode, RunLimits
from .openrouter import check_model_route
from .oracle import GOLD_FINGERPRINT_CODEC, UNSTABLE_GOLD_CASE_IDS
from .prompt_ablation import PROMPT_ABLATION_EXPERIMENTS, run_prompt_ablation
from .provision import (
    DEFAULT_ROOT_PASSWORD,
    download_archive,
    extract_archive,
    provision_mysql,
    verify_mysql_provision,
)
from .report import build_summary, render_markdown
from .runner import (
    ASK_ACCURACY_PROFILE_BASELINE,
    ASK_ACCURACY_PROFILE_CANDIDATE_V1,
    ASK_ACCURACY_PROFILE_CANDIDATE_V2,
    ASK_ACCURACY_PROFILES,
    BenchmarkRunner,
)
from .schema import MySQLSchemaLoader, load_semantic_schema
from .semantic import (
    build_auto_init_layers,
    build_llm_enriched_layers,
    build_semantic_layers,
    install_frozen_llm_enriched_layers,
    semantic_content_hash,
    semantic_dir_for_context,
)
from .structured_response_ablation import run_structured_response_ablation
from .structured_system_ablation import run_structured_system_ablation
from .value_profiles import (
    VALUE_GROUNDING_CONTEXT_VERSION,
    VALUE_PROFILE_CONTEXT_VERSION,
    VALUE_PROFILE_FORMAT_VERSION,
    build_exact_value_profiles,
)

RDST_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RESULTS_DIR = RDST_ROOT / "test-results" / "ask_benchmark"
DEFAULT_CLARIFICATION_FIXTURE = (
    Path(__file__).with_name("clarification_fixtures") / "development-v4.json"
)
BIRD_LICENSE_URL = "https://bird-bench.github.io/"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Benchmark RDST text-to-SQL quality and cost"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Check benchmark prerequisites")
    doctor.add_argument("--models", help="Comma-separated model names")
    doctor.add_argument("--structured", action="store_true")

    conformance = subparsers.add_parser(
        "conformance", help="Verify scoring against the official BIRD evaluator"
    )
    conformance.add_argument("--run-dir", type=Path)
    _add_cache_argument(conformance)
    _add_mysql_arguments(conformance, include_root=False)

    oracle_replay = subparsers.add_parser(
        "oracle-replay", help="Replay and fingerprint all BIRD gold queries"
    )
    oracle_replay.add_argument("--dialect", choices=["mysql"], default="mysql")
    oracle_replay.add_argument("--repetitions", type=_positive_int, default=2)
    oracle_replay.add_argument("--output", type=Path, required=True)
    oracle_replay.add_argument("--compare", type=Path)
    _add_cache_argument(oracle_replay)
    _add_mysql_arguments(oracle_replay, include_root=False)

    prepare = subparsers.add_parser("prepare", help="Download and provision BIRD")
    prepare.add_argument("--dialect", choices=["mysql"], default="mysql")
    prepare.add_argument("--accept-license", action="store_true")
    prepare.add_argument("--metadata-only", action="store_true")
    prepare.add_argument("--force", action="store_true")
    _add_cache_argument(prepare)
    _add_mysql_arguments(prepare, include_root=True)

    enrich = subparsers.add_parser(
        "enrich-schema", help="Freeze RDST AI-generated schema annotations"
    )
    enrich.add_argument(
        "--model",
        default="claude-sonnet-4.6-anthropic-sdk",
        help="Pinned annotator model configuration name",
    )
    enrich.add_argument("--run-id", default="sonnet46-schema-enrichment-v1")
    enrich.add_argument("--sample-rows", type=_positive_int, default=5)
    enrich.add_argument("--force", action="store_true")
    _add_cache_argument(enrich)
    _add_mysql_arguments(enrich, include_root=False)

    profile_values = subparsers.add_parser(
        "profile-values",
        help="Build deterministic local exact-value profiles for BIRD",
    )
    profile_values.add_argument("--force", action="store_true")
    _add_cache_argument(profile_values)
    _add_mysql_arguments(profile_values, include_root=False)

    interact_prepare = subparsers.add_parser(
        "bird-interact-prepare",
        help="Prepare the pinned frozen c-Interact qualification inputs",
    )
    interact_prepare.add_argument("--accept-license", action="store_true")
    interact_prepare.add_argument(
        "--cache-dir", type=Path, default=default_bird_interact_cache_dir()
    )
    interact_prepare.add_argument("--postgres-port", type=_positive_int, default=15435)
    interact_prepare.add_argument("--postgres-user", default="root")
    interact_prepare.add_argument("--postgres-password", default="123123")
    interact_prepare.add_argument(
        "--container-name", default="rdst-bird-interact-postgresql"
    )

    interact_qualify = subparsers.add_parser(
        "bird-interact-qualify",
        help="Run canonical AskService against frozen c-Interact example turns",
    )
    interact_qualify.add_argument("--model", default="claude-sonnet-4.6-anthropic-sdk")
    interact_qualify.add_argument("--run-id", required=True)
    interact_qualify.add_argument("--output", type=Path, default=DEFAULT_RESULTS_DIR)
    interact_qualify.add_argument(
        "--cache-dir", type=Path, default=default_bird_interact_cache_dir()
    )
    interact_qualify.add_argument(
        "--case-ids",
        default=",".join(BIRD_INTERACT_QUALIFICATION_CASE_IDS),
        help="Comma-separated pinned read-only qualification case IDs",
    )
    interact_qualify.add_argument("--postgres-host", default="127.0.0.1")
    interact_qualify.add_argument("--postgres-port", type=_positive_int, default=15435)
    interact_qualify.add_argument("--postgres-user", default="root")
    interact_qualify.add_argument("--postgres-password", default="123123")
    interact_qualify.add_argument("--query-timeout", type=_positive_int, default=30)
    interact_qualify.add_argument(
        "--max-result-rows", type=_positive_int, default=100_000
    )
    interact_qualify.add_argument(
        "--max-result-bytes", type=_positive_int, default=64 * 1024 * 1024
    )
    interact_qualify.add_argument("--product-max-rows", type=_positive_int, default=100)

    interact_rescore = subparsers.add_parser(
        "bird-interact-rescore",
        help="Apply corrected order-aware scoring to a frozen paid qualification",
    )
    interact_rescore.add_argument("source_run_dir", type=Path)
    interact_rescore.add_argument("--run-id", required=True)
    interact_rescore.add_argument("--output", type=Path, default=DEFAULT_RESULTS_DIR)
    interact_rescore.add_argument(
        "--cache-dir", type=Path, default=default_bird_interact_cache_dir()
    )

    ablation = subparsers.add_parser(
        "generation-ablation",
        help="Run the frozen internal 2x2 schema/prompt generation diagnostic",
    )
    ablation.add_argument("source_run_dir", type=Path)
    ablation.add_argument(
        "--model",
        default="claude-sonnet-4.6-anthropic-sdk",
        help="Pinned direct Anthropic model configuration name",
    )
    ablation.add_argument("--run-id", required=True)
    ablation.add_argument("--output", type=Path, default=DEFAULT_RESULTS_DIR)
    _add_cache_argument(ablation)
    _add_mysql_arguments(ablation, include_root=False)

    system_ablation = subparsers.add_parser(
        "structured-system-ablation",
        help="Compare generic and expert system messages for structured generation",
    )
    system_ablation.add_argument("source_run_dir", type=Path)
    system_ablation.add_argument(
        "--model",
        default="claude-sonnet-4.6-anthropic-sdk",
        help="Pinned direct Anthropic model configuration name",
    )
    system_ablation.add_argument("--run-id", required=True)
    system_ablation.add_argument("--output", type=Path, default=DEFAULT_RESULTS_DIR)
    _add_cache_argument(system_ablation)
    _add_mysql_arguments(system_ablation, include_root=False)

    response_ablation = subparsers.add_parser(
        "structured-response-ablation",
        help="Compare plain SQL and structured JSON generation responses",
    )
    response_ablation.add_argument("source_run_dir", type=Path)
    response_ablation.add_argument(
        "--model",
        default="claude-sonnet-4.6-anthropic-sdk",
        help="Pinned direct Anthropic model configuration name",
    )
    response_ablation.add_argument("--run-id", required=True)
    response_ablation.add_argument("--output", type=Path, default=DEFAULT_RESULTS_DIR)
    _add_cache_argument(response_ablation)
    _add_mysql_arguments(response_ablation, include_root=False)

    prompt_ablation = subparsers.add_parser(
        "prompt-ablation",
        help="Compare lean direct-style and detailed product user prompts",
    )
    prompt_ablation.add_argument("source_run_dir", type=Path)
    prompt_ablation.add_argument(
        "--model",
        default="claude-sonnet-4.6-anthropic-sdk",
        help="Pinned direct Anthropic model configuration name",
    )
    prompt_ablation.add_argument("--run-id", required=True)
    prompt_ablation.add_argument(
        "--experiment",
        choices=sorted(PROMPT_ABLATION_EXPERIMENTS),
        default="lean-vs-detailed-v1",
    )
    prompt_ablation.add_argument("--output", type=Path, default=DEFAULT_RESULTS_DIR)
    _add_cache_argument(prompt_ablation)
    _add_mysql_arguments(prompt_ablation, include_root=False)

    run = subparsers.add_parser("run", help="Run a benchmark")
    run.add_argument("--dialect", choices=["mysql"], default="mysql")
    run.add_argument(
        "--suite",
        choices=["smoke", "canary", "holdout", "full"],
        default="canary",
    )
    run.add_argument(
        "--track",
        choices=[track.value for track in EvaluationTrack],
        default=EvaluationTrack.MODEL_ONLY.value,
    )
    run.add_argument(
        "--context",
        choices=[mode.value for mode in ContextMode],
        default=ContextMode.AUTO_INIT.value,
    )
    run.add_argument("--models", required=True, help="Comma-separated model names")
    run.add_argument("--repetitions", type=_positive_int, default=1)
    run.add_argument(
        "--interaction-mode",
        choices=[
            InteractionMode.AUTO.value,
            InteractionMode.INTERACTIVE_NO_ANSWER.value,
        ],
        default=InteractionMode.AUTO.value,
        help="Clarification policy for rdst-ask; model-only records not-applicable",
    )
    run.add_argument(
        "--schema-format",
        choices=[
            ADAPTIVE_SCHEMA_FORMAT_VERSION,
            "verbose-v1",
            COMPACT_SCHEMA_FORMAT_VERSION,
        ],
        default=ADAPTIVE_SCHEMA_FORMAT_VERSION,
        help=(
            "Semantic schema serialization. Forced verbose and compact formats are "
            "restricted to internal rdst-ask smoke diagnostics."
        ),
    )
    run.add_argument(
        "--ask-accuracy-profile",
        choices=ASK_ACCURACY_PROFILES,
        default=ASK_ACCURACY_PROFILE_BASELINE,
        help=(
            "RDST Ask accuracy treatment. candidate-v1 enables the model intent "
            "router, bounded alternate selection, and bounded value grounding. "
            "candidate-v2 additionally enables model-routed, database-proven "
            "encoded-identifier, empty-result time-storage, and full-month "
            "axis-storage repairs."
        ),
    )
    run.add_argument("--max-cases", type=_positive_int)
    run.add_argument(
        "--max-provider-calls",
        type=_positive_int,
        help="Stop before the next provider call after this cumulative count",
    )
    run.add_argument(
        "--max-normalized-cost-usd",
        type=_positive_decimal,
        help="Stop at a completed-call boundary after this normalized cost",
    )
    run.add_argument(
        "--max-wall-time-seconds",
        type=_positive_float,
        help="Stop at the next case or provider-call boundary after this time",
    )
    run.add_argument("--run-id")
    run.add_argument(
        "--holdout-campaign-id",
        help="Shared immutable ID for the paired direct/RDST holdout campaign",
    )
    run.add_argument(
        "--confirm-open-holdout",
        action="store_true",
        help="Explicitly acknowledge the one-time 450-case holdout opening",
    )
    run.add_argument("--gold-replay-receipt", type=Path)
    run.add_argument("--output", type=Path, default=DEFAULT_RESULTS_DIR)
    _add_cache_argument(run)
    _add_mysql_arguments(run, include_root=False)

    report = subparsers.add_parser("report", help="Rebuild a run report")
    report.add_argument("run_dir", type=Path)
    report.add_argument("--output", type=Path)

    compare = subparsers.add_parser("compare", help="Compare compatible runs")
    compare.add_argument("run_dirs", type=Path, nargs="+")
    compare.add_argument("--output", type=Path, required=True)

    review = subparsers.add_parser(
        "clarification-align",
        aliases=["clarification-review"],
        help="Bind ranked detector output to BIRD-gold qualification labels",
    )
    review.add_argument("run_dir", type=Path)
    review.add_argument("--fixture", type=Path, default=DEFAULT_CLARIFICATION_FIXTURE)
    review.add_argument("--output", type=Path, required=True)
    _add_cache_argument(review)

    qualify = subparsers.add_parser(
        "clarification-qualify",
        help="Score a BIRD-gold-aligned clarification packet",
    )
    qualify.add_argument("alignment_packet", type=Path)
    qualify.add_argument("--output", type=Path)
    qualify.add_argument("--fixture", type=Path, default=DEFAULT_CLARIFICATION_FIXTURE)
    qualify.add_argument(
        "--replay-current-policy",
        action="store_true",
        help="Replay the current deterministic resolver policy over stored output",
    )
    _add_cache_argument(qualify)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "doctor":
            return _doctor(args)
        if args.command == "conformance":
            return _conformance(args)
        if args.command == "oracle-replay":
            return _oracle_replay(args)
        if args.command == "prepare":
            return _prepare(args)
        if args.command == "enrich-schema":
            return _enrich_schema(args)
        if args.command == "profile-values":
            return _profile_values(args)
        if args.command == "bird-interact-prepare":
            return _bird_interact_prepare(args)
        if args.command == "bird-interact-qualify":
            return _bird_interact_qualify(args)
        if args.command == "bird-interact-rescore":
            return _bird_interact_rescore(args)
        if args.command == "generation-ablation":
            return _generation_ablation(args)
        if args.command == "structured-system-ablation":
            return _structured_system_ablation(args)
        if args.command == "structured-response-ablation":
            return _structured_response_ablation(args)
        if args.command == "prompt-ablation":
            return _prompt_ablation(args)
        if args.command == "run":
            return _run(args)
        if args.command == "report":
            return _report(args)
        if args.command == "compare":
            return _compare(args)
        if args.command in {"clarification-align", "clarification-review"}:
            return _clarification_align(args)
        if args.command == "clarification-qualify":
            return _clarification_qualify(args)
    except (TypeError, ValueError, RuntimeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    raise AssertionError(f"Unknown command: {args.command}")


def _doctor(args) -> int:
    specs = load_model_specs()
    selected = select_models(specs, _split_models(args.models))
    requires_pydantic = any(spec.transport == "openrouter" for spec in selected)
    requires_anthropic = any(spec.transport == "anthropic" for spec in selected)
    requires_claude_subscription = any(
        spec.transport == CLAUDE_SUBSCRIPTION_TRANSPORT for spec in selected
    )
    pydantic_state = (
        _module_available("pydantic_ai") if requires_pydantic else "not required"
    )
    anthropic_state = (
        _module_available("anthropic") if requires_anthropic else "not required"
    )
    docker_path = shutil.which("docker")
    claude_path = shutil.which("claude") if requires_claude_subscription else None
    credentials = {
        variable: bool(os.getenv(variable))
        for variable in sorted({_credential_env(spec.transport) for spec in selected})
    }
    failures = (
        int(pydantic_state == "missing")
        + int(anthropic_state == "missing")
        + int(not docker_path)
        + int(requires_claude_subscription and not claude_path)
        + sum(not present for present in credentials.values())
    )
    print(f"PydanticAI: {pydantic_state}")
    print(f"Anthropic SDK: {anthropic_state}")
    print(f"Docker: {docker_path or 'missing'}")
    if requires_claude_subscription:
        print(
            "Claude Code CLI: "
            f"{claude_path or 'missing'}; required-version={PINNED_CLAUDE_CODE_VERSION}"
        )
    for variable, present in credentials.items():
        print(f"{variable}: {'set' if present else 'missing'}")
    for spec in selected:
        check = check_model_route(spec, structured_output=args.structured)
        state = "ok" if check.scoreable else "failed"
        pricing = "changed" if check.pricing_changed else "pinned"
        print(
            f"{spec.name}: {state}; provider={check.matched_provider}; "
            f"pricing={pricing}; endpoint={','.join(check.endpoint_model_ids) or 'none'}; "
            f"reasoning={spec.reasoning_effort or 'provider-default'}; "
            f"supported_reasoning={','.join(check.supported_reasoning_efforts) or 'unknown'}; "
            f"training={spec.provider_data_training}; "
            f"retains_prompts={spec.provider_retains_prompts}"
        )
        if check.missing_parameters:
            print(f"  missing parameters: {', '.join(check.missing_parameters)}")
        if check.error:
            print(f"  error: {check.error}")
        failures += not check.scoreable
    return 1 if failures else 0


def _conformance(args) -> int:
    evaluator_dir = prepare_official_evaluator(args.cache_dir)
    fixture_count = verify_official_oracle_conformance(evaluator_dir)
    receipt = {
        "revision": OFFICIAL_EVALUATOR_REVISION,
        "files": OFFICIAL_EVALUATOR_FILES,
        "fixture_count": fixture_count,
        "scope": "sentinel_fixtures",
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }
    if args.run_dir:
        store = ArtifactStore(args.run_dir)
        manifest = json.loads(store.manifest_path.read_text(encoding="utf-8"))
        cases = exclude_invalid_gold_cases(
            load_cached_cases(args.dialect, cache_dir=args.cache_dir)
        )
        connection = MySQLConnectionConfig(
            host=args.mysql_host,
            port=args.mysql_port,
            user=args.mysql_user,
            password=args.mysql_password,
            database_prefix=args.mysql_database_prefix,
        )
        verify_mysql_provision(args.cache_dir, connection, cases[0].db_id)
        executor = MySQLExecutor(
            connection,
            QueryBounds(
                timeout_seconds=max(300, args.query_timeout),
                max_rows=args.max_result_rows,
                max_result_bytes=args.max_result_bytes,
            ),
        )
        replay = verify_full_candidate_replay(
            evaluator_dir, cases, store.load_attempts(), executor
        )
        receipt.update(replay)
        receipt.update(
            {
                "scope": "full_dataset",
                "source_run_id": manifest.get("run_id"),
                "gold_exclusion_revision": manifest.get("gold_exclusion_revision"),
                "excluded_gold_case_ids": manifest.get("excluded_gold_case_ids"),
                "source_artifact_digests": _run_artifact_digests(args.run_dir),
            }
        )
        if replay["full_dataset_mismatch_count"]:
            raise ValueError(
                "Full official scorer replay found "
                f"{replay['full_dataset_mismatch_count']} mismatches"
            )
    write_json(conformance_receipt_path(args.cache_dir), receipt)
    if args.run_dir:
        write_json(args.run_dir / "official-scorer-receipt.json", receipt)
    print(f"Official BIRD scorer conformance passed: {fixture_count} fixtures")
    if args.run_dir:
        print(f"Full {SCORABLE_CASE_COUNT}-case stored-candidate replay passed")
    return 0


def _oracle_replay(args) -> int:
    if args.output.exists():
        raise ValueError(f"Gold replay output already exists: {args.output}")
    cases = exclude_invalid_gold_cases(
        load_cached_cases(args.dialect, cache_dir=args.cache_dir)
    )
    connection = MySQLConnectionConfig(
        host=args.mysql_host,
        port=args.mysql_port,
        user=args.mysql_user,
        password=args.mysql_password,
        database_prefix=args.mysql_database_prefix,
    )
    database_provision = verify_mysql_provision(
        args.cache_dir, connection, cases[0].db_id
    )
    executor = MySQLExecutor(
        connection,
        QueryBounds(
            timeout_seconds=max(300, args.query_timeout),
            max_rows=args.max_result_rows,
            max_result_bytes=args.max_result_bytes,
        ),
    )
    receipt = replay_gold_results(
        cases,
        executor,
        repetitions=args.repetitions,
        provenance={"database_provision": database_provision},
    )
    if args.compare:
        baseline = json.loads(args.compare.read_text(encoding="utf-8"))
        verify_replay_receipt(baseline)
        changed = compare_gold_replays(baseline, receipt)
        if changed:
            print(f"Allowed unstable gold changes: {changed}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output, receipt)
    print(f"Gold replay passed: {len(cases)} cases x {args.repetitions} repetitions")
    return 0


def _prepare(args) -> int:
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    global_lock = _global_prepare_lock()
    with (
        FileLock(str(global_lock), timeout=10),
        FileLock(str(args.cache_dir / "prepare.lock"), timeout=10),
    ):
        return _prepare_locked(args)


def _prepare_locked(args) -> int:
    if not args.accept_license:
        raise ValueError(
            f"Review {BIRD_LICENSE_URL} and pass --accept-license to prepare BIRD"
        )
    cache_dir = args.cache_dir
    metadata = download_metadata(args.dialect, cache_dir=cache_dir)
    print(f"Metadata ready: {metadata}")
    if args.metadata_only:
        return 0
    if shutil.which("docker") is None:
        raise RuntimeError("Docker is required for MySQL provisioning")
    archive = download_archive(cache_dir)
    print(f"Archive ready: {archive}")
    extracted, archive_sha256 = extract_archive(archive, cache_dir)
    print(f"Required archive entries ready: {extracted}")
    semantic_paths = build_semantic_layers(extracted, cache_dir / "semantic")
    print(f"Semantic layers ready: {len(semantic_paths)}")
    manifest = provision_mysql(
        extracted,
        archive_sha256=archive_sha256,
        cache_dir=cache_dir,
        root_password=args.mysql_root_password,
        runtime_user=args.mysql_user,
        runtime_password=args.mysql_password,
        port=args.mysql_port,
        database_prefix=args.mysql_database_prefix,
        force=args.force,
    )
    print(json.dumps(manifest, indent=2))
    connection = MySQLConnectionConfig(
        host="127.0.0.1",
        port=args.mysql_port,
        user=args.mysql_user,
        password=args.mysql_password,
        database_prefix=args.mysql_database_prefix,
    )
    cases = load_cached_cases(args.dialect, cache_dir=cache_dir)
    auto_init_paths = build_auto_init_layers(
        [case.db_id for case in cases],
        cache_dir / "semantic-auto-init",
        connection,
        force=args.force,
    )
    print(f"Auto-init semantic layers ready: {len(auto_init_paths)}")
    llm_enriched_paths = install_frozen_llm_enriched_layers(
        sorted({case.db_id for case in cases}),
        cache_dir / "semantic-auto-init",
        cache_dir / "semantic-llm-enriched",
        force=args.force,
    )
    print(f"Frozen LLM-enriched semantic layers ready: {len(llm_enriched_paths)}")
    return 0


def _enrich_schema(args) -> int:
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise ValueError("Missing model credentials: ANTHROPIC_API_KEY")
    specs = load_model_specs()
    selected = select_models(specs, [args.model])
    spec = selected[0]
    if spec.transport != "anthropic":
        raise ValueError("Schema enrichment requires a direct Anthropic model")
    route = check_model_route(spec, structured_output=False)
    if not route.scoreable:
        raise ValueError(f"Annotator model has no controlled route: {route}")

    cases = load_cached_cases("mysql", cache_dir=args.cache_dir)
    db_ids = sorted({case.db_id for case in cases})
    source_dir = semantic_dir_for_context(args.cache_dir, ContextMode.AUTO_INIT)
    output_dir = semantic_dir_for_context(args.cache_dir, ContextMode.LLM_ENRICHED)
    source_hashes = _semantic_file_hashes(source_dir, db_ids)
    connection = MySQLConnectionConfig(
        host=args.mysql_host,
        port=args.mysql_port,
        user=args.mysql_user,
        password=args.mysql_password,
        database_prefix=args.mysql_database_prefix,
    )
    database_provision = verify_mysql_provision(args.cache_dir, connection, db_ids[0])
    run_id = require_safe_file_stem(args.run_id, "schema enrichment run ID")
    store = ArtifactStore(args.cache_dir / "schema-enrichment" / run_id)
    manifest = {
        "artifact_schema_version": 1,
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "schema-enrichment",
        "dataset_revision": DATASET_REVISION,
        "model": asdict(spec),
        "route_check": asdict(route),
        "sample_rows": args.sample_rows,
        "sample_rows_in_prompt": min(args.sample_rows, SAMPLE_ROWS_PER_PROMPT),
        "sample_order_policy": "primary-key-else-row-sha256-v2",
        "annotation_temperature": 0.0,
        "source_context": ContextMode.AUTO_INIT.value,
        "output_context": ContextMode.LLM_ENRICHED.value,
        "source_hashes": source_hashes,
        "database_provision": database_provision,
        "annotation_input_policy": "auto-init schema, profiles, and database samples only",
        "benchmark_protocol_sha256": _benchmark_protocol_sha256(),
    }
    if store.manifest_path.exists():
        existing = json.loads(store.manifest_path.read_text(encoding="utf-8"))
        for field in (
            "purpose",
            "dataset_revision",
            "model",
            "sample_rows",
            "sample_rows_in_prompt",
            "sample_order_policy",
            "annotation_temperature",
            "source_context",
            "output_context",
            "source_hashes",
            "database_provision",
            "annotation_input_policy",
            "benchmark_protocol_sha256",
        ):
            if existing.get(field) != manifest.get(field):
                raise ValueError(
                    f"Schema enrichment run {run_id!r} has different {field}"
                )
    store.initialize(manifest)
    adapter = AnthropicSDKAdapter(spec, call_receipt_sink=store.append_call_receipt)
    adapter.set_attempt_id(run_id)
    annotator = AIAnnotator(llm_manager=adapter, model=spec.model)
    started = perf_counter()
    paths, stats = build_llm_enriched_layers(
        db_ids,
        source_dir,
        output_dir,
        connection,
        annotator,
        sample_rows=args.sample_rows,
        force=args.force,
    )
    output_hashes = _semantic_file_hashes(output_dir, db_ids)
    calls = store.load_call_receipts()
    normalized_cost = sum(
        (Decimal(str(call.get("normalized_cold_cost_usd", "0"))) for call in calls),
        Decimal(0),
    )
    provenance = {
        **manifest,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "wall_time_ms": (perf_counter() - started) * 1000,
        "source_hashes": source_hashes,
        "output_hashes": output_hashes,
        "output_paths": [str(path) for path in paths],
        "call_count": len(calls),
        "normalized_cold_setup_cost_usd": str(normalized_cost),
        **stats,
    }
    write_json(store.run_dir / "provenance.json", provenance)
    write_json(output_dir / "provenance.json", provenance)
    print(
        f"AI-enriched schemas ready: {len(paths)} databases, {len(calls)} calls, "
        f"${normalized_cost} normalized setup cost"
    )
    return 0


def _profile_values(args) -> int:
    source_dir = semantic_dir_for_context(args.cache_dir, ContextMode.AUTO_INIT)
    expected_db_ids = set(BIRD_MINI_DATABASE_IDS)
    actual_db_ids = {path.stem for path in source_dir.glob("*.yaml")}
    if actual_db_ids != expected_db_ids:
        raise ValueError(
            "Auto-init schemas differ from the pinned BIRD database set: "
            f"expected {sorted(expected_db_ids)}, got {sorted(actual_db_ids)}"
        )
    db_ids = list(BIRD_MINI_DATABASE_IDS)
    output_dir = semantic_dir_for_context(
        args.cache_dir, ContextMode.AUTO_INIT_PROFILED_VALUES
    )
    connection = MySQLConnectionConfig(
        host=args.mysql_host,
        port=args.mysql_port,
        user=args.mysql_user,
        password=args.mysql_password,
        database_prefix=args.mysql_database_prefix,
    )
    verify_mysql_provision(args.cache_dir, connection, db_ids[0])
    provenance = build_exact_value_profiles(
        db_ids,
        source_dir,
        output_dir,
        connection,
        force=args.force,
    )
    stats = provenance["database_stats"]
    action = "verified" if provenance.get("_reused") else "built"
    print(
        f"Exact value profiles {action}: "
        f"{len(stats)} databases, "
        f"{sum(item['indexed_columns'] for item in stats.values())} columns, "
        f"{sum(item['indexed_values'] for item in stats.values())} values, "
        f"frozen setup {provenance['wall_time_seconds']:.2f}s"
    )
    return 0


def _bird_interact_prepare(args) -> int:
    if not args.accept_license:
        raise ValueError(
            "Review the BIRD-Interact CC BY-SA 4.0 license and pass --accept-license"
        )
    if shutil.which("docker") is None:
        raise RuntimeError("Docker is required for BIRD-Interact provisioning")
    require_safe_file_stem(args.container_name, "BIRD-Interact container name")
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    with (
        FileLock(str(_global_prepare_lock()), timeout=10),
        FileLock(str(args.cache_dir / "prepare.lock"), timeout=10),
    ):
        public_path, examples_path = prepare_frozen_qualification_inputs(args.cache_dir)
        subprocess.run(
            ["docker", "pull", BIRD_INTERACT_POSTGRES_IMAGE],
            check=True,
        )
        image_id, image_digest = _docker_image_identity(BIRD_INTERACT_POSTGRES_IMAGE)
        _ensure_bird_interact_container(
            name=args.container_name,
            image_id=image_id,
            image_digest=image_digest,
            port=args.postgres_port,
            user=args.postgres_user,
            password=args.postgres_password,
        )
        connection = PostgresConnectionConfig(
            host="127.0.0.1",
            port=args.postgres_port,
            user=args.postgres_user,
            password=args.postgres_password,
        )
        deadline = time.monotonic() + 300
        last_error = None
        while time.monotonic() < deadline:
            try:
                provision = verify_postgres_provision(
                    connection,
                    image_id=image_id,
                    image_digest=image_digest,
                )
                break
            except (psycopg2.Error, RuntimeError) as exc:
                last_error = exc
                time.sleep(2)
        else:
            raise RuntimeError(
                f"Timed out waiting for BIRD-Interact PostgreSQL: {last_error}"
            )
        receipt = write_preparation_receipt(
            args.cache_dir / "preparation.json",
            public_data_path=public_path,
            examples_path=examples_path,
            image_id=image_id,
            image_digest=image_digest,
        )
        receipt["database_provision"] = provision
        write_json(args.cache_dir / "preparation.json", receipt)
    print(json.dumps(receipt, indent=2))
    return 0


def _bird_interact_qualify(args) -> int:
    if args.model != "claude-sonnet-4.6-anthropic-sdk":
        raise ValueError(
            "BIRD-Interact qualification is frozen to claude-sonnet-4.6-anthropic-sdk"
        )
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise ValueError("Missing model credentials: ANTHROPIC_API_KEY")
    receipt_path = args.cache_dir / "preparation.json"
    if not receipt_path.is_file():
        raise ValueError(
            "BIRD-Interact is not prepared; run bird-interact-prepare "
            "--accept-license first"
        )
    preparation = json.loads(receipt_path.read_text(encoding="utf-8"))
    if preparation.get("source_revision") != BIRD_INTERACT_SOURCE_REVISION:
        raise ValueError("BIRD-Interact preparation source revision differs")

    public_path = (
        args.cache_dir
        / "public"
        / preparation["public_revision"]
        / "mini_interact.jsonl"
    )
    examples_path = (
        args.cache_dir
        / "source"
        / preparation["source_revision"]
        / "c_interact_samples.json"
    )
    requested_case_ids = tuple(
        value.strip() for value in args.case_ids.split(",") if value.strip()
    )
    if not requested_case_ids:
        raise ValueError("At least one BIRD-Interact case ID is required")
    unsupported = set(requested_case_ids).difference(
        BIRD_INTERACT_QUALIFICATION_CASE_IDS
    )
    if unsupported:
        raise ValueError(
            "Only pinned read-only frozen examples are allowed: "
            + ", ".join(sorted(unsupported))
        )
    cases = load_frozen_qualification_cases(
        public_path,
        examples_path,
        case_ids=requested_case_ids,
    )

    model_spec = select_models(load_model_specs(), [args.model])[0]
    if model_spec.transport != "anthropic" or model_spec.model != "claude-sonnet-4-6":
        raise ValueError("Qualification requires direct claude-sonnet-4-6")
    filter_spec, filter_aliases = load_filter_spec()
    filter_spec = replace(
        filter_spec,
        model="claude-haiku-4-5-20251001",
        transport="anthropic",
        provider_order=(),
        reasoning_effort=None,
    )
    model_route = check_model_route(
        model_spec,
        structured_output=True,
        runtime_parameters={"max_tokens"},
    )
    filter_route = check_model_route(
        filter_spec,
        structured_output=True,
        runtime_parameters={"max_tokens"},
    )
    if not model_route.scoreable or not filter_route.scoreable:
        raise ValueError(
            "BIRD-Interact qualification requires controlled Anthropic routes"
        )

    connection = PostgresConnectionConfig(
        host=args.postgres_host,
        port=args.postgres_port,
        user=args.postgres_user,
        password=args.postgres_password,
    )
    image_id, image_digest = _docker_image_identity(BIRD_INTERACT_POSTGRES_IMAGE)
    if image_id != preparation.get("postgres_image_id"):
        raise ValueError("Prepared BIRD-Interact PostgreSQL image ID changed")
    if image_digest != preparation.get("postgres_image_digest"):
        raise ValueError("Prepared BIRD-Interact PostgreSQL image digest changed")
    database_provision = verify_postgres_provision(
        connection,
        image_id=image_id,
        image_digest=image_digest,
    )
    if database_provision != preparation.get("database_provision"):
        raise ValueError("BIRD-Interact database provision differs from preparation")

    executor = PostgresQualificationExecutor(
        connection,
        PostgresQueryBounds(
            timeout_seconds=args.query_timeout,
            max_rows=args.max_result_rows,
            max_result_bytes=args.max_result_bytes,
        ),
    )
    run_id = require_safe_file_stem(args.run_id, "BIRD-Interact run ID")
    summary = run_frozen_qualification(
        run_id=run_id,
        output_dir=args.output / run_id,
        cases=cases,
        model_spec=model_spec,
        filter_spec=filter_spec,
        filter_aliases=filter_aliases,
        route_checks={
            model_spec.name: asdict(model_route),
            filter_spec.name: asdict(filter_route),
        },
        executor=executor,
        database_provision=database_provision,
        protocol_sha256=_benchmark_protocol_sha256(),
        rdst_revision=_rdst_revision(),
        rdst_dirty=_rdst_dirty(),
        rdst_diff_sha256=_rdst_diff_sha256(),
        product_max_rows=args.product_max_rows,
    )
    print(
        f"BIRD-Interact qualification complete: {args.output / run_id / 'summary.md'}"
    )
    return 0 if summary["coverage"] == 1.0 else 2


def _bird_interact_rescore(args) -> int:
    receipt_path = args.cache_dir / "preparation.json"
    if not receipt_path.is_file():
        raise ValueError("BIRD-Interact preparation receipt is missing")
    preparation = json.loads(receipt_path.read_text(encoding="utf-8"))
    public_path = (
        args.cache_dir
        / "public"
        / preparation["public_revision"]
        / "mini_interact.jsonl"
    )
    examples_path = (
        args.cache_dir
        / "source"
        / preparation["source_revision"]
        / "c_interact_samples.json"
    )
    source_manifest_path = args.source_run_dir / "manifest.json"
    if not source_manifest_path.is_file():
        raise ValueError("Source BIRD-Interact manifest is missing")
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    case_ids = tuple(str(value) for value in source_manifest.get("case_ids", []))
    cases = load_frozen_qualification_cases(
        public_path,
        examples_path,
        case_ids=case_ids,
    )
    run_id = require_safe_file_stem(args.run_id, "BIRD-Interact rescore run ID")
    summary = rescore_frozen_qualification(
        source_run_dir=args.source_run_dir,
        output_dir=args.output / run_id,
        run_id=run_id,
        cases=cases,
        protocol_sha256=_benchmark_protocol_sha256(),
        rdst_revision=_rdst_revision(),
        rdst_dirty=_rdst_dirty(),
        rdst_diff_sha256=_rdst_diff_sha256(),
    )
    print(f"BIRD-Interact rescore complete: {args.output / run_id / 'summary.md'}")
    return 0 if summary["coverage"] == 1.0 else 2


def _generation_ablation(args) -> int:
    if args.model != "claude-sonnet-4.6-anthropic-sdk":
        raise ValueError(
            "Generation ablation is frozen to claude-sonnet-4.6-anthropic-sdk"
        )
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise ValueError("Missing model credentials: ANTHROPIC_API_KEY")
    spec = select_models(load_model_specs(), [args.model])[0]
    if spec.transport != "anthropic" or spec.model != "claude-sonnet-4-6":
        raise ValueError("Generation ablation requires direct claude-sonnet-4-6")
    route = check_model_route(
        spec,
        structured_output=True,
        runtime_parameters={"max_tokens"},
    )
    if not route.scoreable:
        raise ValueError(f"Ablation model has no controlled route: {route}")

    all_cases = load_cached_cases("mysql", cache_dir=args.cache_dir)
    if len(all_cases) != 500:
        raise ValueError(
            f"Pinned BIRD Mini-Dev must contain exactly 500 cases, got {len(all_cases)}"
        )
    cases = select_pipeline_smoke(all_cases)
    semantic_dir = semantic_dir_for_context(args.cache_dir, ContextMode.LLM_ENRICHED)
    db_ids = sorted({case.db_id for case in cases})
    context_provenance = _verify_context_provenance(
        semantic_dir, ContextMode.LLM_ENRICHED, db_ids
    )
    if context_provenance is None:
        raise AssertionError("LLM-enriched provenance verification returned no receipt")
    connection = MySQLConnectionConfig(
        host=args.mysql_host,
        port=args.mysql_port,
        user=args.mysql_user,
        password=args.mysql_password,
        database_prefix=args.mysql_database_prefix,
    )
    database_provision = verify_mysql_provision(
        args.cache_dir, connection, cases[0].db_id
    )
    executor = MySQLExecutor(
        connection,
        QueryBounds(
            timeout_seconds=args.query_timeout,
            max_rows=args.max_result_rows,
            max_result_bytes=args.max_result_bytes,
        ),
    )
    run_id = require_safe_file_stem(args.run_id, "generation ablation run ID")
    summary = run_generation_ablation(
        source_run_dir=args.source_run_dir,
        output_dir=args.output / run_id,
        cases=cases,
        semantic_dir=semantic_dir,
        executor=executor,
        model_spec=spec,
        route_check=asdict(route),
        context_provenance=context_provenance,
        database_provision=database_provision,
        protocol_sha256=_benchmark_protocol_sha256(),
    )
    print(f"Generation ablation complete: {args.output / run_id / 'summary.md'}")
    if any(cell["attempts"] != len(cases) for cell in summary["cells"]):
        return 2
    return 0


def _structured_system_ablation(args) -> int:
    if args.model != "claude-sonnet-4.6-anthropic-sdk":
        raise ValueError(
            "Structured system ablation is frozen to claude-sonnet-4.6-anthropic-sdk"
        )
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise ValueError("Missing model credentials: ANTHROPIC_API_KEY")
    spec = select_models(load_model_specs(), [args.model])[0]
    if spec.transport != "anthropic" or spec.model != "claude-sonnet-4-6":
        raise ValueError("Structured system ablation requires direct claude-sonnet-4-6")
    route = check_model_route(
        spec,
        structured_output=True,
        runtime_parameters={"max_tokens"},
    )
    if not route.scoreable:
        raise ValueError(f"Ablation model has no controlled route: {route}")

    all_cases = load_cached_cases("mysql", cache_dir=args.cache_dir)
    if len(all_cases) != 500:
        raise ValueError(
            f"Pinned BIRD Mini-Dev must contain exactly 500 cases, got {len(all_cases)}"
        )
    cases = select_pipeline_smoke(all_cases)
    semantic_dir = semantic_dir_for_context(args.cache_dir, ContextMode.LLM_ENRICHED)
    db_ids = sorted({case.db_id for case in cases})
    context_provenance = _verify_context_provenance(
        semantic_dir, ContextMode.LLM_ENRICHED, db_ids
    )
    if context_provenance is None:
        raise AssertionError("LLM-enriched provenance verification returned no receipt")
    connection = MySQLConnectionConfig(
        host=args.mysql_host,
        port=args.mysql_port,
        user=args.mysql_user,
        password=args.mysql_password,
        database_prefix=args.mysql_database_prefix,
    )
    database_provision = verify_mysql_provision(
        args.cache_dir, connection, cases[0].db_id
    )
    executor = MySQLExecutor(
        connection,
        QueryBounds(
            timeout_seconds=args.query_timeout,
            max_rows=args.max_result_rows,
            max_result_bytes=args.max_result_bytes,
        ),
    )
    run_id = require_safe_file_stem(args.run_id, "structured system ablation run ID")
    summary = run_structured_system_ablation(
        source_run_dir=args.source_run_dir,
        output_dir=args.output / run_id,
        cases=cases,
        semantic_dir=semantic_dir,
        executor=executor,
        model_spec=spec,
        route_check=asdict(route),
        context_provenance=context_provenance,
        database_provision=database_provision,
        protocol_sha256=_benchmark_protocol_sha256(),
        rdst_revision=_rdst_revision(),
        rdst_dirty=_rdst_dirty(),
        rdst_diff_sha256=_rdst_diff_sha256(),
    )
    print(f"Structured system ablation complete: {args.output / run_id / 'summary.md'}")
    if any(cell["attempts"] != len(cases) for cell in summary["cells"]):
        return 2
    return 0


def _structured_response_ablation(args) -> int:
    if args.model != "claude-sonnet-4.6-anthropic-sdk":
        raise ValueError(
            "Structured response ablation is frozen to claude-sonnet-4.6-anthropic-sdk"
        )
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise ValueError("Missing model credentials: ANTHROPIC_API_KEY")
    spec = select_models(load_model_specs(), [args.model])[0]
    if spec.transport != "anthropic" or spec.model != "claude-sonnet-4-6":
        raise ValueError(
            "Structured response ablation requires direct claude-sonnet-4-6"
        )
    route = check_model_route(
        spec,
        structured_output=True,
        runtime_parameters={"max_tokens"},
    )
    if not route.scoreable:
        raise ValueError(f"Ablation model has no controlled route: {route}")

    all_cases = load_cached_cases("mysql", cache_dir=args.cache_dir)
    if len(all_cases) != 500:
        raise ValueError(
            f"Pinned BIRD Mini-Dev must contain exactly 500 cases, got {len(all_cases)}"
        )
    cases = select_pipeline_smoke(all_cases)
    semantic_dir = semantic_dir_for_context(args.cache_dir, ContextMode.LLM_ENRICHED)
    db_ids = sorted({case.db_id for case in cases})
    context_provenance = _verify_context_provenance(
        semantic_dir, ContextMode.LLM_ENRICHED, db_ids
    )
    if context_provenance is None:
        raise AssertionError("LLM-enriched provenance verification returned no receipt")
    connection = MySQLConnectionConfig(
        host=args.mysql_host,
        port=args.mysql_port,
        user=args.mysql_user,
        password=args.mysql_password,
        database_prefix=args.mysql_database_prefix,
    )
    database_provision = verify_mysql_provision(
        args.cache_dir, connection, cases[0].db_id
    )
    executor = MySQLExecutor(
        connection,
        QueryBounds(
            timeout_seconds=args.query_timeout,
            max_rows=args.max_result_rows,
            max_result_bytes=args.max_result_bytes,
        ),
    )
    run_id = require_safe_file_stem(args.run_id, "structured response ablation run ID")
    summary = run_structured_response_ablation(
        source_run_dir=args.source_run_dir,
        output_dir=args.output / run_id,
        cases=cases,
        semantic_dir=semantic_dir,
        executor=executor,
        model_spec=spec,
        route_check=asdict(route),
        context_provenance=context_provenance,
        database_provision=database_provision,
        protocol_sha256=_benchmark_protocol_sha256(),
        rdst_revision=_rdst_revision(),
        rdst_dirty=_rdst_dirty(),
        rdst_diff_sha256=_rdst_diff_sha256(),
    )
    print(
        f"Structured response ablation complete: {args.output / run_id / 'summary.md'}"
    )
    if any(cell["attempts"] != len(cases) for cell in summary["cells"]):
        return 2
    return 0


def _prompt_ablation(args) -> int:
    if args.model != "claude-sonnet-4.6-anthropic-sdk":
        raise ValueError("Prompt ablation is frozen to claude-sonnet-4.6-anthropic-sdk")
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise ValueError("Missing model credentials: ANTHROPIC_API_KEY")
    spec = select_models(load_model_specs(), [args.model])[0]
    if spec.transport != "anthropic" or spec.model != "claude-sonnet-4-6":
        raise ValueError("Prompt ablation requires direct claude-sonnet-4-6")
    route = check_model_route(
        spec,
        structured_output=False,
        runtime_parameters={"max_tokens"},
    )
    if not route.scoreable:
        raise ValueError(f"Ablation model has no controlled route: {route}")

    all_cases = load_cached_cases("mysql", cache_dir=args.cache_dir)
    if len(all_cases) != 500:
        raise ValueError(
            f"Pinned BIRD Mini-Dev must contain exactly 500 cases, got {len(all_cases)}"
        )
    cases = select_pipeline_smoke(all_cases)
    semantic_dir = semantic_dir_for_context(args.cache_dir, ContextMode.LLM_ENRICHED)
    db_ids = sorted({case.db_id for case in cases})
    context_provenance = _verify_context_provenance(
        semantic_dir, ContextMode.LLM_ENRICHED, db_ids
    )
    if context_provenance is None:
        raise AssertionError("LLM-enriched provenance verification returned no receipt")
    connection = MySQLConnectionConfig(
        host=args.mysql_host,
        port=args.mysql_port,
        user=args.mysql_user,
        password=args.mysql_password,
        database_prefix=args.mysql_database_prefix,
    )
    database_provision = verify_mysql_provision(
        args.cache_dir, connection, cases[0].db_id
    )
    executor = MySQLExecutor(
        connection,
        QueryBounds(
            timeout_seconds=args.query_timeout,
            max_rows=args.max_result_rows,
            max_result_bytes=args.max_result_bytes,
        ),
    )
    run_id = require_safe_file_stem(args.run_id, "prompt ablation run ID")
    summary = run_prompt_ablation(
        source_run_dir=args.source_run_dir,
        output_dir=args.output / run_id,
        cases=cases,
        semantic_dir=semantic_dir,
        executor=executor,
        model_spec=spec,
        route_check=asdict(route),
        context_provenance=context_provenance,
        database_provision=database_provision,
        protocol_sha256=_benchmark_protocol_sha256(),
        rdst_revision=_rdst_revision(),
        rdst_dirty=_rdst_dirty(),
        rdst_diff_sha256=_rdst_diff_sha256(),
        experiment=args.experiment,
    )
    print(f"Prompt ablation complete: {args.output / run_id / 'summary.md'}")
    if any(cell["attempts"] != len(cases) for cell in summary["cells"]):
        return 2
    return 0


def _run(args) -> int:
    if args.repetitions <= 0:
        raise ValueError("--repetitions must be positive")
    if args.max_cases is not None and args.max_cases <= 0:
        raise ValueError("--max-cases must be positive")
    if args.suite != "canary" and args.max_cases is not None:
        raise ValueError("--max-cases is allowed only for the development canary")
    if args.suite == "holdout":
        if not args.confirm_open_holdout:
            raise ValueError("Holdout runs require --confirm-open-holdout")
        if not args.holdout_campaign_id:
            raise ValueError("Holdout runs require --holdout-campaign-id")
        if not args.run_id:
            raise ValueError(
                "Holdout runs require an explicit --run-id for safe resume"
            )
        require_safe_file_stem(args.holdout_campaign_id, "holdout campaign ID")
    scorer_conformance = verify_conformance_receipt(args.cache_dir, require_full=False)
    gold_replay_receipt = None
    if args.gold_replay_receipt:
        gold_replay_receipt = json.loads(
            args.gold_replay_receipt.read_text(encoding="utf-8")
        )
        verify_replay_receipt(gold_replay_receipt)
        if gold_replay_receipt.get("case_count") != SCORABLE_CASE_COUNT:
            raise ValueError("Gold replay receipt must cover all scorable BIRD cases")
    specs = load_model_specs()
    selected = select_models(specs, _split_models(args.models))
    if args.suite == "smoke":
        if len(selected) != 1 or args.repetitions != 1:
            raise ValueError("Smoke runs require exactly one model and one repetition")
        if selected[0].name not in {
            "claude-sonnet-4.6-anthropic-sdk",
            "claude-sonnet-4.6-subscription-medium",
        }:
            raise ValueError(
                "Pipeline smoke is frozen to a pinned claude-sonnet-4-6 transport"
            )
        if not args.run_id:
            raise ValueError("Smoke runs require an explicit --run-id")
    if args.suite in {"holdout", "full"} and (
        len(selected) != 1 or args.repetitions != 1
    ):
        raise ValueError(
            f"{args.suite.capitalize()} runs require exactly one model and one repetition"
        )
    filter_spec, filter_aliases = load_filter_spec()
    track = EvaluationTrack(args.track)
    if (
        args.ask_accuracy_profile != ASK_ACCURACY_PROFILE_BASELINE
        and track != EvaluationTrack.RDST
    ):
        raise ValueError("--ask-accuracy-profile applies only to the rdst-ask track")
    interaction_mode = (
        InteractionMode(args.interaction_mode)
        if track == EvaluationTrack.RDST
        else InteractionMode.NOT_APPLICABLE
    )
    if args.schema_format != ADAPTIVE_SCHEMA_FORMAT_VERSION and (
        args.suite != "smoke"
        or track != EvaluationTrack.RDST
        or ContextMode(args.context) == ContextMode.RAW
    ):
        raise ValueError(
            "Forced schema formatting is restricted to semantic rdst-ask smoke runs"
        )
    required_transports = {spec.transport for spec in selected}
    if len(required_transports) != 1:
        raise ValueError("Run each model transport in a separate benchmark cohort")
    selected_transport = next(iter(required_transports))
    if track == EvaluationTrack.RDST and selected_transport == "anthropic":
        filter_spec = replace(
            filter_spec,
            model="claude-haiku-4-5-20251001",
            transport="anthropic",
            provider_order=(),
            reasoning_effort=None,
        )
    elif (
        track == EvaluationTrack.RDST
        and selected_transport == CLAUDE_SUBSCRIPTION_TRANSPORT
    ):
        filter_spec = replace(
            filter_spec,
            name="claude-sonnet-4.6-subscription-medium-filter",
            model=CLAUDE_SUBSCRIPTION_MODEL,
            transport=CLAUDE_SUBSCRIPTION_TRANSPORT,
            provider_order=(),
            pricing=selected[0].pricing,
            reasoning_effort=selected[0].reasoning_effort,
            provider_data_training=selected[0].provider_data_training,
            provider_retains_prompts=selected[0].provider_retains_prompts,
        )
    elif track == EvaluationTrack.RDST:
        required_transports.add(filter_spec.transport)
    missing_credentials = [
        variable
        for variable in sorted(_credential_env(value) for value in required_transports)
        if not os.getenv(variable)
    ]
    if missing_credentials:
        raise ValueError(f"Missing model credentials: {', '.join(missing_credentials)}")
    context_mode = ContextMode(args.context)
    route_checks = {}
    for spec in selected:
        check = check_model_route(
            spec,
            structured_output=track == EvaluationTrack.RDST,
            runtime_parameters={"max_tokens"}
            if track == EvaluationTrack.RDST
            else None,
        )
        route_checks[spec.name] = asdict(check)
        if (
            track == EvaluationTrack.RDST
            and check.max_completion_tokens is not None
            and check.max_completion_tokens < AMBIGUITY_RESPONSE_MAX_TOKENS
        ):
            raise ValueError(
                f"Model {spec.name} route caps completion below the "
                f"{AMBIGUITY_RESPONSE_MAX_TOKENS}-token RDST phase"
            )
        if not check.scoreable:
            raise ValueError(
                f"Model {spec.name} has no controlled compatible route: {check}"
            )
    if track == EvaluationTrack.RDST:
        filter_check = check_model_route(
            filter_spec,
            structured_output=True,
            runtime_parameters={"max_tokens"},
        )
        route_checks[filter_spec.name] = asdict(filter_check)
        if not filter_check.scoreable:
            raise ValueError(
                f"Filter model has no controlled compatible route: {filter_check}"
            )

    args.cache_dir.mkdir(parents=True, exist_ok=True)
    global_lock = _global_prepare_lock()
    with (
        _shared_file_lock(global_lock, timeout=10),
        _shared_file_lock(args.cache_dir / "prepare.lock", timeout=10),
    ):
        return _run_database_locked(
            args,
            selected=selected,
            filter_spec=filter_spec,
            filter_aliases=filter_aliases,
            track=track,
            context_mode=context_mode,
            interaction_mode=interaction_mode,
            route_checks=route_checks,
            scorer_conformance=scorer_conformance,
            gold_replay_receipt=gold_replay_receipt,
        )


def _run_database_locked(
    args,
    *,
    selected,
    filter_spec,
    filter_aliases,
    track,
    context_mode,
    interaction_mode,
    route_checks,
    scorer_conformance,
    gold_replay_receipt,
) -> int:
    all_cases = load_cached_cases(args.dialect, cache_dir=args.cache_dir)
    if len(all_cases) != 500:
        raise ValueError(
            f"Pinned BIRD Mini-Dev must contain exactly 500 cases, got {len(all_cases)}"
        )
    if gold_replay_receipt:
        receipt_case_ids = {
            int(case_id) for case_id in gold_replay_receipt["fingerprints"]
        }
        expected_case_ids = {
            case.question_id for case in exclude_invalid_gold_cases(all_cases)
        }
        if receipt_case_ids != expected_case_ids:
            raise ValueError("Gold replay receipt case IDs differ from the dataset")
    cases = all_cases
    if args.suite == "smoke":
        cases = select_pipeline_smoke(cases)
    elif args.suite == "canary":
        cases = select_canary(cases)
    elif args.suite == "holdout":
        cases = select_holdout(cases)
    cases = exclude_invalid_gold_cases(cases)
    if args.max_cases is not None:
        cases = cases[: args.max_cases]
    partition = partition_provenance(all_cases)

    connection = MySQLConnectionConfig(
        host=args.mysql_host,
        port=args.mysql_port,
        user=args.mysql_user,
        password=args.mysql_password,
        database_prefix=args.mysql_database_prefix,
    )
    database_provision = verify_mysql_provision(
        args.cache_dir, connection, cases[0].db_id
    )
    if gold_replay_receipt:
        verify_replay_provenance(gold_replay_receipt, database_provision)
    schema_loader = MySQLSchemaLoader(connection)
    semantic_dir = semantic_dir_for_context(args.cache_dir, context_mode)
    context_provenance = _verify_context_provenance(
        semantic_dir,
        context_mode,
        sorted({case.db_id for case in cases}),
    )
    context_hashes = {
        db_id: _context_hash(
            schema_loader.load(db_id)
            if context_mode == ContextMode.RAW
            else load_semantic_schema(semantic_dir, db_id)
        )
        for db_id in sorted({case.db_id for case in cases})
    }
    executor = MySQLExecutor(
        connection,
        QueryBounds(
            timeout_seconds=args.query_timeout,
            max_rows=args.max_result_rows,
            max_result_bytes=args.max_result_bytes,
        ),
    )
    run_id = require_safe_file_stem(args.run_id or _new_run_id(), "run ID")
    store = ArtifactStore(args.output / run_id)
    protocol_fingerprint = _benchmark_protocol_sha256()
    benchmark_started = perf_counter()
    runner = BenchmarkRunner(
        run_id=run_id,
        track=track,
        context_mode=context_mode,
        executor=executor,
        schema_loader=schema_loader,
        artifact_store=store,
        filter_spec=filter_spec,
        filter_aliases=filter_aliases,
        semantic_dir=semantic_dir,
        interaction_mode=interaction_mode,
        protocol_fingerprint=protocol_fingerprint,
        run_limits=RunLimits(
            max_provider_calls=args.max_provider_calls,
            max_normalized_cost_usd=args.max_normalized_cost_usd,
            max_wall_time_seconds=args.max_wall_time_seconds,
        ),
        semantic_schema_format=args.schema_format,
        ask_accuracy_profile=args.ask_accuracy_profile,
    )
    cases, gold_failures = runner.preflight_gold(cases)
    if gold_failures:
        failed_ids = ", ".join(str(question_id) for question_id in gold_failures)
        raise RuntimeError(f"BIRD gold preflight failed for questions: {failed_ids}")
    if not cases:
        raise RuntimeError("No BIRD gold queries passed preflight")
    manifest = {
        "artifact_schema_version": 4,
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "rdst_revision": _rdst_revision(),
        "rdst_dirty": _rdst_dirty(),
        "rdst_diff_sha256": _rdst_diff_sha256(),
        "benchmark_protocol_sha256": protocol_fingerprint,
        "dataset": "bird-mini",
        "dataset_revision": DATASET_REVISION,
        "gold_exclusion_revision": GOLD_EXCLUSION_REVISION,
        "excluded_gold_case_ids": list(MYSQL_INVALID_GOLD_CASE_IDS),
        "scorer_conformance": scorer_conformance,
        "gold_replay_receipt": (
            {
                "replay_sha256": gold_replay_receipt["replay_sha256"],
                "dataset_revision": gold_replay_receipt["dataset_revision"],
                "gold_exclusion_revision": gold_replay_receipt[
                    "gold_exclusion_revision"
                ],
                "excluded_gold_case_ids": gold_replay_receipt["excluded_gold_case_ids"],
                "case_count": gold_replay_receipt["case_count"],
                "repetitions": gold_replay_receipt["repetitions"],
                "gold_result_fingerprint_codec": gold_replay_receipt[
                    "gold_result_fingerprint_codec"
                ],
                "declared_unstable_case_ids": gold_replay_receipt[
                    "declared_unstable_case_ids"
                ],
                "database_identity": {
                    key: gold_replay_receipt.get("provenance", {})
                    .get("database_provision", {})
                    .get(key)
                    for key in (
                        "archive_sha256",
                        "mysql_image_id",
                        "mysql_version",
                        "database_prefix",
                    )
                },
            }
            if gold_replay_receipt
            else None
        ),
        "dialect": args.dialect,
        "suite": args.suite,
        "partition_provenance": partition,
        "holdout_campaign_id": (
            args.holdout_campaign_id if args.suite == "holdout" else None
        ),
        "track": track.value,
        "context_mode": context_mode.value,
        "interaction_mode": interaction_mode.value,
        "semantic_schema_format": args.schema_format,
        "ask_accuracy_profile": args.ask_accuracy_profile,
        "ask_accuracy_features": {
            "correction_intent_routing": (
                args.ask_accuracy_profile
                in {
                    ASK_ACCURACY_PROFILE_CANDIDATE_V1,
                    ASK_ACCURACY_PROFILE_CANDIDATE_V2,
                }
            ),
            "dual_candidate_selection": (
                args.ask_accuracy_profile
                in {
                    ASK_ACCURACY_PROFILE_CANDIDATE_V1,
                    ASK_ACCURACY_PROFILE_CANDIDATE_V2,
                }
            ),
            "value_location_normalization": (
                args.ask_accuracy_profile
                in {
                    ASK_ACCURACY_PROFILE_CANDIDATE_V1,
                    ASK_ACCURACY_PROFILE_CANDIDATE_V2,
                }
            ),
            "encoded_identifier_storage": (
                args.ask_accuracy_profile == ASK_ACCURACY_PROFILE_CANDIDATE_V2
            ),
            "temporal_text_storage": (
                args.ask_accuracy_profile == ASK_ACCURACY_PROFILE_CANDIDATE_V2
            ),
            "month_axis_storage": (
                args.ask_accuracy_profile == ASK_ACCURACY_PROFILE_CANDIDATE_V2
            ),
        },
        "clarification_policy": (
            NON_INTERACTIVE_CLARIFICATION_POLICY
            if track == EvaluationTrack.RDST
            and interaction_mode == InteractionMode.AUTO
            else interaction_mode.value
            if track == EvaluationTrack.RDST
            else None
        ),
        "case_ids": [case.question_id for case in cases],
        "gold_failures": gold_failures,
        "gold_result_fingerprint_codec": GOLD_FINGERPRINT_CODEC,
        "gold_result_fingerprints": runner.gold_fingerprints(),
        "unstable_gold_case_ids": list(UNSTABLE_GOLD_CASE_IDS),
        "repetitions": args.repetitions,
        "expected_attempts_per_model": len(cases) * args.repetitions,
        "models": [asdict(spec) for spec in selected],
        "model_configuration_fingerprints": {
            spec.name: runner.configuration_fingerprint(spec) for spec in selected
        },
        "route_checks": route_checks,
        "filter_model": asdict(filter_spec),
        "database_provision": database_provision,
        "context_hashes": context_hashes,
        "context_provenance": context_provenance,
        "query_bounds": asdict(executor.bounds),
        "run_limits": asdict(runner.run_limits),
    }
    if args.suite == "holdout":
        _reserve_holdout_access(args.cache_dir, manifest)
    store.initialize(manifest)
    with store.run_lock():
        previous_elapsed_ms = _existing_benchmark_elapsed_ms(store)
        attempt_count_before = len(store.load_attempts())
        attempts = runner.run(cases, selected, repetitions=args.repetitions)
        final_database_provision = verify_mysql_provision(
            args.cache_dir, connection, cases[0].db_id
        )
        if final_database_provision != database_provision:
            raise RuntimeError("MySQL provision changed during benchmark execution")
        added_elapsed_ms = (
            (perf_counter() - benchmark_started) * 1000
            if len(attempts) != attempt_count_before
            else 0.0
        )
        summary = build_summary(
            attempts,
            expected_attempts_per_model=manifest["expected_attempts_per_model"],
            benchmark_elapsed_ms=previous_elapsed_ms + added_elapsed_ms,
            call_receipts=store.load_call_receipts(),
        )
        summary["publication_status"] = _publication_status(store.run_dir, manifest)
        if runner.budget_stop_reason:
            summary["run_stop"] = {
                "kind": "budget",
                "reason": runner.budget_stop_reason,
            }
        store.write_summary(summary, render_markdown(summary))
        _write_dashboard_if_complete(summary, store.run_dir / "summary.html")
    if runner.budget_stop_reason:
        print(
            f"Run stopped safely: {runner.budget_stop_reason}; "
            f"resume with --run-id {run_id}",
            file=sys.stderr,
        )
        return 4
    incomplete = [
        model["model_name"]
        for board in summary["leaderboards"]
        for model in board["models"]
        if model["coverage"] != 1.0
    ]
    if incomplete:
        print(
            f"Run requires repair for: {', '.join(incomplete)}; "
            f"resume with --run-id {run_id}",
            file=sys.stderr,
        )
        return 2
    publication_status = _publication_status(store.run_dir, manifest)
    if args.suite == "full" and publication_status != "verified":
        print(
            "Full run is complete but publication is blocked; run "
            f"`conformance --run-dir {store.run_dir}` and rebuild the report",
            file=sys.stderr,
        )
        return 3
    print(f"Run complete: {store.run_dir}")
    return 0


def _compare(args) -> int:
    summary = compare_runs(args.run_dirs, args.output)
    if len(summary.get("leaderboards", [])) == 1:
        dashboard_path = write_dashboard(summary, args.output / "leaderboard.html")
        print(dashboard_path)
    else:
        print(args.output / "leaderboard.md")
    return 0


def _clarification_align(args) -> int:
    if args.output.exists():
        raise ValueError(f"Clarification review output already exists: {args.output}")
    store = ArtifactStore(args.run_dir)
    manifest = json.loads(store.manifest_path.read_text(encoding="utf-8"))
    if manifest.get("track") != EvaluationTrack.RDST.value:
        raise ValueError("Clarification review requires an rdst-ask run")
    if manifest.get("suite") not in {"smoke", "canary"}:
        raise ValueError("Clarification review is restricted to development suites")
    if manifest.get("context_mode") == ContextMode.EVIDENCE.value:
        raise ValueError("Development clarification fixtures are no-evidence only")
    all_cases = load_cached_cases("mysql", cache_dir=args.cache_dir)
    fixture = load_gold_alignment_fixture(
        args.fixture, all_cases, require_gold_aligned=True
    )
    packet = build_gold_alignment_packet(fixture, store.load_attempts())
    packet["source_run_id"] = manifest.get("run_id")
    packet["source_manifest_sha256"] = hashlib.sha256(
        store.manifest_path.read_bytes()
    ).hexdigest()
    write_json(args.output, packet)
    print(args.output)
    return 0


def _clarification_qualify(args) -> int:
    try:
        packet = json.loads(args.alignment_packet.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid clarification review packet: {exc}") from exc
    all_cases = load_cached_cases("mysql", cache_dir=args.cache_dir)
    fixture = load_gold_alignment_fixture(
        args.fixture, all_cases, require_gold_aligned=True
    )
    score = score_gold_alignment_packet(
        packet,
        fixture,
        replay_current_policy=args.replay_current_policy,
    )
    if args.output:
        if args.output.exists():
            raise ValueError(
                f"Clarification qualification output already exists: {args.output}"
            )
        write_json(args.output, score)
    print(json.dumps(score, indent=2, sort_keys=True))
    return 0 if score["qualified"] else 2


def _existing_benchmark_elapsed_ms(store: ArtifactStore) -> float:
    try:
        summary = json.loads(store.summary_json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0.0
    return float(summary.get("benchmark_elapsed_ms") or 0.0)


def _report(args) -> int:
    source_store = ArtifactStore(args.run_dir)
    manifest = json.loads(source_store.manifest_path.read_text(encoding="utf-8"))
    schema_version = int(manifest.get("artifact_schema_version", 1))
    if schema_version < 2 and (
        args.output is None or args.output.resolve() == args.run_dir.resolve()
    ):
        raise ValueError(
            "Legacy reports require a distinct --output directory to preserve source artifacts"
        )
    store = ArtifactStore(args.output or args.run_dir)
    attempts = source_store.load_attempts()
    models_by_name = {str(model["name"]): model for model in manifest.get("models", [])}
    for attempt in attempts:
        if model := models_by_name.get(str(attempt.get("model_name"))):
            attempt.setdefault("reasoning_effort", model.get("reasoning_effort"))
            attempt.setdefault("max_tokens", model.get("max_tokens"))
    try:
        existing_summary = json.loads(
            source_store.summary_json_path.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        existing_summary = {}
    summary = build_summary(
        attempts,
        expected_attempts_per_model=int(manifest["expected_attempts_per_model"]),
        benchmark_elapsed_ms=existing_summary.get("benchmark_elapsed_ms"),
        call_receipts=source_store.load_call_receipts(),
    )
    summary["publication_status"] = _publication_status(source_store.run_dir, manifest)
    store.write_summary(summary, render_markdown(summary))
    _write_dashboard_if_complete(summary, store.run_dir / "summary.html")
    print(store.summary_markdown_path)
    return 0


def _publication_status(run_dir: Path, manifest: dict[str, Any]) -> str:
    if manifest.get("suite") != "full":
        return "not_applicable"
    path = run_dir / "official-scorer-receipt.json"
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "pending_official_replay"
    verified = (
        receipt.get("scope") == "full_dataset"
        and int(receipt.get("full_dataset_case_count", 0)) == SCORABLE_CASE_COUNT
        and int(receipt.get("full_dataset_mismatch_count", -1)) == 0
        and len(str(receipt.get("full_dataset_replay_sha256", ""))) == 64
        and receipt.get("source_run_id") == manifest.get("run_id")
        and receipt.get("source_artifact_digests") == _run_artifact_digests(run_dir)
    )
    return "verified" if verified else "failed_official_replay"


def _run_artifact_digests(run_dir: Path) -> dict[str, str]:
    """Bind an official replay receipt to the exact immutable run inputs."""
    digests = {}
    for name in ("manifest.json", "attempts.jsonl", "calls.jsonl"):
        path = run_dir / name
        content = path.read_bytes() if path.exists() else b""
        digests[name] = hashlib.sha256(content).hexdigest()
    return digests


def _write_dashboard_if_complete(summary: dict, path: Path) -> None:
    boards = summary.get("leaderboards", [])
    if len(boards) != 1:
        return
    models = boards[0].get("models", [])
    if not models or any(
        model.get("coverage") != 1.0
        or model.get("cost_per_question_usd") in {None, "0"}
        for model in models
    ):
        return
    write_dashboard(summary, path)


def _docker_image_identity(image: str) -> tuple[str, str]:
    result = subprocess.run(
        ["docker", "image", "inspect", image],
        check=True,
        capture_output=True,
        text=True,
    )
    records = json.loads(result.stdout)
    if len(records) != 1:
        raise RuntimeError(f"Expected one Docker image record for {image}")
    image_id = str(records[0].get("Id") or "")
    digests = sorted(str(value) for value in (records[0].get("RepoDigests") or []))
    if not image_id or not digests:
        raise RuntimeError(f"Docker image {image} has no immutable identity")
    matching = [
        digest for digest in digests if "shawnxxh/bird-interact-postgresql@" in digest
    ]
    return image_id, (matching or digests)[0]


def _ensure_bird_interact_container(
    *,
    name: str,
    image_id: str,
    image_digest: str,
    port: int,
    user: str,
    password: str,
) -> None:
    inspected = subprocess.run(
        ["docker", "container", "inspect", name],
        check=False,
        capture_output=True,
        text=True,
    )
    if inspected.returncode == 0:
        records = json.loads(inspected.stdout)
        if len(records) != 1 or records[0].get("Image") != image_id:
            raise RuntimeError(
                f"Existing container {name!r} uses a different Docker image"
            )
        bindings = (
            records[0].get("HostConfig", {}).get("PortBindings", {}).get("5432/tcp", [])
        )
        observed_ports = {int(binding["HostPort"]) for binding in bindings}
        if observed_ports != {port}:
            raise RuntimeError(
                f"Existing container {name!r} exposes ports {observed_ports}, "
                f"expected {port}"
            )
        subprocess.run(["docker", "start", name], check=True)
        return
    volume = f"{name}-data"
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "-e",
            f"POSTGRES_USER={user}",
            "-e",
            f"POSTGRES_PASSWORD={password}",
            "-e",
            "TZ=Asia/Hong_Kong",
            "-v",
            f"{volume}:/var/lib/postgresql/data",
            "-p",
            f"127.0.0.1:{port}:5432",
            image_digest,
            "-c",
            "max_connections=300",
            "-c",
            "shared_buffers=256MB",
        ],
        check=True,
    )


def _global_prepare_lock() -> Path:
    configured = os.getenv("RDST_BENCHMARK_GLOBAL_LOCK")
    path = (
        Path(configured).expanduser()
        if configured
        else Path.home() / ".cache" / "rdst" / "benchmarks" / "prepare.lock"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def _shared_file_lock(path: Path, *, timeout: float):
    """Keep preparation exclusive while allowing parallel read-only runs."""
    if fcntl is None:
        with FileLock(str(path), timeout=timeout):
            yield
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    with path.open("a+b") as lock_file:
        while True:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise Timeout(str(path))
                time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _add_cache_argument(parser) -> None:
    parser.add_argument("--cache-dir", type=Path, default=default_cache_dir())


def _add_mysql_arguments(parser, *, include_root: bool) -> None:
    if not include_root:
        parser.add_argument(
            "--mysql-host", default=os.getenv("BIRD_MYSQL_HOST", "127.0.0.1")
        )
    else:
        parser.add_argument(
            "--mysql-root-password",
            default=os.getenv("BIRD_MYSQL_ROOT_PASSWORD", DEFAULT_ROOT_PASSWORD),
        )
    parser.add_argument(
        "--mysql-port",
        type=_positive_int,
        default=os.getenv("BIRD_MYSQL_PORT", "13316"),
    )
    parser.add_argument(
        "--mysql-user", default=os.getenv("BIRD_MYSQL_USER", "rdst_bird")
    )
    parser.add_argument(
        "--mysql-password",
        default=os.getenv("BIRD_MYSQL_PASSWORD", "rdst-benchmark"),
    )
    parser.add_argument(
        "--mysql-database-prefix",
        default=os.getenv("BIRD_MYSQL_DATABASE_PREFIX", "bird_"),
    )
    if not include_root:
        parser.add_argument("--query-timeout", type=_positive_int, default=30)
        parser.add_argument("--max-result-rows", type=_positive_int, default=100_000)
        parser.add_argument(
            "--max-result-bytes", type=_positive_int, default=64 * 1024 * 1024
        )


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def _positive_decimal(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except Exception as exc:
        raise argparse.ArgumentTypeError("value must be a decimal") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise argparse.ArgumentTypeError("value must be a positive finite decimal")
    return parsed


def _split_models(value: str | None) -> list[str] | None:
    if value is None:
        return None
    names = [name.strip() for name in value.split(",") if name.strip()]
    if not names:
        raise ValueError("At least one model is required")
    return names


def _new_run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{uuid4().hex[:8]}"


_HOLDOUT_IDENTITY_FIELDS = (
    "dataset",
    "dataset_revision",
    "gold_exclusion_revision",
    "excluded_gold_case_ids",
    "dialect",
    "suite",
    "partition_provenance",
    "holdout_campaign_id",
    "context_mode",
    "case_ids",
    "models",
    "rdst_revision",
    "rdst_dirty",
    "rdst_diff_sha256",
    "benchmark_protocol_sha256",
)


def _reserve_holdout_access(cache_dir: Path, manifest: dict[str, Any]) -> None:
    """Allow one resumable direct/RDST pair to consume the frozen holdout."""
    ledger_path = cache_dir / "holdout-access.json"
    identity = {field: manifest.get(field) for field in _HOLDOUT_IDENTITY_FIELDS}
    track = str(manifest["track"])
    run_id = str(manifest["run_id"])
    if track not in {EvaluationTrack.MODEL_ONLY.value, EvaluationTrack.RDST.value}:
        raise ValueError(f"Unsupported holdout track: {track}")

    if ledger_path.exists():
        try:
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Holdout access ledger is unreadable: {exc}") from exc
        if ledger.get("identity") != identity:
            campaign = ledger.get("identity", {}).get("holdout_campaign_id")
            raise ValueError(
                "The 450-case holdout is already bound to campaign "
                f"{campaign!r} with a different frozen pipeline"
            )
    else:
        ledger = {
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "identity": identity,
            "tracks": {},
        }

    tracks = ledger.setdefault("tracks", {})
    prior_run_id = tracks.get(track)
    if prior_run_id is not None and prior_run_id != run_id:
        raise ValueError(
            f"Holdout track {track!r} is already bound to run {prior_run_id!r}; "
            "resume that run instead"
        )
    tracks[track] = run_id
    write_json(ledger_path, ledger)


def _rdst_revision() -> str:
    try:
        return subprocess.run(
            ["jj", "log", "-r", "@", "--no-graph", "-T", "commit_id"],
            cwd=RDST_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        try:
            return subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=RDST_ROOT,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return "unknown"


def _rdst_dirty() -> bool:
    try:
        output = subprocess.run(
            ["jj", "diff", "--summary"],
            cwd=RDST_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        try:
            output = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=RDST_ROOT,
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        except (OSError, subprocess.CalledProcessError):
            return True
    return bool(output.strip())


def _rdst_diff_sha256() -> str:
    try:
        diff = subprocess.run(
            ["jj", "diff", "--git"],
            cwd=RDST_ROOT,
            check=True,
            capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        try:
            diff = subprocess.run(
                ["git", "diff", "HEAD", "--binary", "--no-ext-diff"],
                cwd=RDST_ROOT,
                check=True,
                capture_output=True,
            ).stdout
        except (OSError, subprocess.CalledProcessError):
            return "unknown"
    return hashlib.sha256(diff).hexdigest()


def _benchmark_protocol_paths() -> tuple[Path, ...]:
    return (
        Path(__file__).with_name("model_matrix.toml"),
        Path(__file__).with_name("models.py"),
        Path(__file__).with_name("bird_dataset.py"),
        Path(__file__).with_name("config.py"),
        Path(__file__).with_name("openrouter.py"),
        Path(__file__).with_name("pydantic_adapter.py"),
        Path(__file__).with_name("pipeline.py"),
        Path(__file__).with_name("runner.py"),
        Path(__file__).with_name("executor.py"),
        Path(__file__).with_name("oracle.py"),
        Path(__file__).with_name("schema.py"),
        Path(__file__).with_name("semantic.py"),
        Path(__file__).with_name("value_profiles.py"),
        Path(__file__).with_name("anthropic_adapter.py"),
        Path(__file__).with_name("claude_subscription_adapter.py"),
        Path(__file__).with_name("bird_interact.py"),
        RDST_ROOT / "features" / "ask" / "ask3.py",
        RDST_ROOT / "features" / "ask" / "events.py",
        RDST_ROOT / "features" / "ask" / "models.py",
        RDST_ROOT / "features" / "ask" / "service.py",
        RDST_ROOT / "features" / "ask" / "prompts" / "ask_prompts.py",
        RDST_ROOT / "features" / "ask" / "prompts" / "ask_prompts_v2.py",
        RDST_ROOT / "features" / "ask" / "sql_generation.py",
        RDST_ROOT / "features" / "ask" / "sql_validation.py",
        RDST_ROOT / "features" / "ask" / "ambiguity_detection.py",
        RDST_ROOT / "features" / "ask" / "correction_intent_state.py",
        RDST_ROOT / "features" / "ask" / "correction_intent_routing.py",
        RDST_ROOT / "features" / "ask" / "aggregate_domain_normalization.py",
        RDST_ROOT / "features" / "ask" / "categorical_normalization.py",
        RDST_ROOT / "features" / "ask" / "derived_metric_normalization.py",
        RDST_ROOT / "features" / "ask" / "numeric_normalization.py",
        RDST_ROOT / "features" / "ask" / "ranking_normalization.py",
        RDST_ROOT / "features" / "ask" / "shared_entity_scope_normalization.py",
        RDST_ROOT / "features" / "ask" / "dual_candidate.py",
        RDST_ROOT / "features" / "ask" / "value_location_normalization.py",
        RDST_ROOT / "features" / "ask" / "value_probe.py",
        RDST_ROOT / "features" / "ask" / "encoded_identifier_storage.py",
        RDST_ROOT / "features" / "ask" / "temporal_text_storage.py",
        RDST_ROOT / "features" / "ask" / "month_axis_storage.py",
        RDST_ROOT / "features" / "ask" / "engine" / "ask3" / "context.py",
        RDST_ROOT / "features" / "ask" / "engine" / "ask3" / "engine.py",
        RDST_ROOT / "features" / "ask" / "engine" / "ask3" / "types.py",
        RDST_ROOT / "features" / "ask" / "engine" / "ask3" / "phases" / "schema.py",
        RDST_ROOT / "features" / "ask" / "engine" / "ask3" / "phases" / "generate.py",
        RDST_ROOT / "features" / "ask" / "engine" / "ask3" / "phases" / "validate.py",
        RDST_ROOT / "features" / "ask" / "engine" / "ask3" / "phases" / "execute.py",
        RDST_ROOT / "shared" / "llm_manager" / "claude_provider.py",
        RDST_ROOT / "shared" / "llm_manager" / "llm_manager.py",
        RDST_ROOT / "features" / "schema" / "semantic_models.py",
        RDST_ROOT / "features" / "schema" / "annotate_service.py",
        RDST_ROOT / "features" / "schema" / "semantic_layer" / "ai_annotator.py",
        RDST_ROOT / "features" / "schema" / "semantic_layer" / "manager.py",
        RDST_ROOT / "features" / "analyze" / "functions" / "shallow_analysis.py",
    )


def _benchmark_protocol_sha256() -> str:
    digest = hashlib.sha256()
    for path in _benchmark_protocol_paths():
        digest.update(str(path.relative_to(RDST_ROOT)).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _semantic_file_hashes(directory: Path, db_ids: list[str]) -> dict[str, str]:
    hashes = {}
    for db_id in db_ids:
        path = directory / f"{db_id}.yaml"
        if not path.exists():
            raise ValueError(f"Semantic snapshot is missing: {path}")
        hashes[db_id] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def _verify_context_provenance(
    semantic_dir: Path,
    context_mode: ContextMode,
    db_ids: list[str],
) -> dict[str, Any] | None:
    """Bind generated semantic contexts to their frozen preparation receipt."""
    if context_mode in {ContextMode.BIRD_CURATED, ContextMode.EVIDENCE}:
        return _verify_bird_curated_provenance(semantic_dir, db_ids)
    if context_mode == ContextMode.AUTO_INIT_PROFILED_VALUES:
        return _verify_profiled_value_provenance(semantic_dir, db_ids)
    if context_mode != ContextMode.LLM_ENRICHED:
        return None
    path = semantic_dir / "provenance.json"
    try:
        raw = path.read_bytes()
        provenance = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"LLM-enriched context requires valid provenance at {path}: {exc}"
        ) from exc
    required = {
        "dataset_revision": DATASET_REVISION,
        "source_context": ContextMode.AUTO_INIT.value,
        "output_context": ContextMode.LLM_ENRICHED.value,
    }
    for field, expected in required.items():
        if provenance.get(field) != expected:
            raise ValueError(
                f"LLM-enriched provenance has {field}={provenance.get(field)!r}; "
                f"expected {expected!r}"
            )
    recorded_content_hashes = provenance.get("output_content_hashes")
    if recorded_content_hashes is not None:
        if not isinstance(recorded_content_hashes, dict):
            raise TypeError("LLM-enriched provenance has invalid output_content_hashes")
        recorded_hashes = recorded_content_hashes
        actual_hashes = {
            db_id: semantic_content_hash(semantic_dir / f"{db_id}.yaml")
            for db_id in db_ids
        }
        output_hash_kind = "semantic-content-v1"
    else:
        recorded_hashes = provenance.get("output_hashes")
        if not isinstance(recorded_hashes, dict):
            raise TypeError("LLM-enriched provenance is missing output hashes")
        actual_hashes = _semantic_file_hashes(semantic_dir, db_ids)
        output_hash_kind = "file-sha256"
    mismatches = {
        db_id
        for db_id, digest in actual_hashes.items()
        if recorded_hashes.get(db_id) != digest
    }
    if mismatches:
        raise ValueError(
            "LLM-enriched schemas differ from frozen provenance: "
            + ", ".join(sorted(mismatches))
        )
    return {
        "provenance_sha256": hashlib.sha256(raw).hexdigest(),
        "run_id": provenance.get("run_id"),
        "model": provenance.get("model"),
        "annotation_input_policy": provenance.get("annotation_input_policy"),
        "sample_rows_requested_per_table": provenance.get("sample_rows"),
        "sample_rows_fetched_total": provenance.get("sample_rows_fetched_total"),
        "sample_order_policy": provenance.get("sample_order_policy"),
        "sample_inputs_sha256": (
            hashlib.sha256(
                json.dumps(
                    provenance.get("sample_inputs"),
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            if provenance.get("sample_inputs") is not None
            else None
        ),
        "output_hashes": actual_hashes,
        "output_hash_kind": output_hash_kind,
    }


def _verify_profiled_value_provenance(
    semantic_dir: Path, db_ids: list[str]
) -> dict[str, Any]:
    path = semantic_dir / "provenance.json"
    try:
        raw = path.read_bytes()
        provenance = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"Profiled-value context requires valid provenance at {path}: {exc}"
        ) from exc
    required = {
        "format_version": VALUE_PROFILE_FORMAT_VERSION,
        "context_version": VALUE_PROFILE_CONTEXT_VERSION,
        "dataset_revision": DATASET_REVISION,
        "source_context": ContextMode.AUTO_INIT.value,
        "output_context": ContextMode.AUTO_INIT_PROFILED_VALUES.value,
        "construction_policy": "local-exact-distinct-values-v1",
        "contains_questions": False,
        "contains_evidence": False,
        "contains_gold_sql": False,
        "contains_bird_curated_descriptions": False,
        "contains_database_values": True,
    }
    for field, expected in required.items():
        if provenance.get(field) != expected:
            raise ValueError(
                f"Profiled-value provenance has {field}="
                f"{provenance.get(field)!r}; expected {expected!r}"
            )
    schema_hashes = provenance.get("output_schema_hashes")
    profile_hashes = provenance.get("value_profile_hashes")
    if not isinstance(schema_hashes, dict) or not isinstance(profile_hashes, dict):
        raise TypeError("Profiled-value provenance is missing output hashes")
    mismatches = []
    actual_profiles = {}
    for db_id in db_ids:
        schema_path = semantic_dir / f"{db_id}.yaml"
        profile_path = semantic_dir / f"{db_id}.values.json"
        schema_hash = semantic_content_hash(schema_path)
        try:
            profile_hash = hashlib.sha256(profile_path.read_bytes()).hexdigest()
        except OSError as exc:
            raise ValueError(
                f"Exact value profile is unreadable: {profile_path}"
            ) from exc
        actual_profiles[db_id] = profile_hash
        if (
            schema_hashes.get(db_id) != schema_hash
            or profile_hashes.get(db_id) != profile_hash
        ):
            mismatches.append(db_id)
    if mismatches:
        raise ValueError(
            "Profiled-value context differs from frozen provenance: "
            + ", ".join(sorted(mismatches))
        )
    return {
        "provenance_sha256": hashlib.sha256(raw).hexdigest(),
        "format_version": provenance["format_version"],
        "context_version": provenance["context_version"],
        "matching_policy_version": VALUE_GROUNDING_CONTEXT_VERSION,
        "construction_policy": provenance["construction_policy"],
        "max_values_per_column": provenance.get("max_values_per_column"),
        "max_value_chars": provenance.get("max_value_chars"),
        "value_profile_hashes": actual_profiles,
        "contains_database_values": True,
    }


def _verify_bird_curated_provenance(
    semantic_dir: Path, db_ids: list[str]
) -> dict[str, Any]:
    """Verify curated schemas came only from pinned BIRD structural metadata."""
    path = semantic_dir / "provenance.json"
    try:
        raw = path.read_bytes()
        provenance = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"BIRD-curated context requires valid provenance at {path}: {exc}"
        ) from exc
    required = {
        "schema_version": 1,
        "dataset_revision": DATASET_REVISION,
        "source_context": "bird-mini-dev-metadata",
        "output_context": ContextMode.BIRD_CURATED.value,
        "construction_policy": "dev-tables-plus-database-description-csv-v1",
        "contains_questions": False,
        "contains_evidence": False,
        "contains_gold_sql": False,
    }
    for field, expected in required.items():
        if provenance.get(field) != expected:
            raise ValueError(
                f"BIRD-curated provenance has {field}={provenance.get(field)!r}; "
                f"expected {expected!r}"
            )

    recorded_outputs = provenance.get("output_hashes")
    if not isinstance(recorded_outputs, dict):
        raise TypeError("BIRD-curated provenance is missing output_hashes")
    actual_outputs = _semantic_file_hashes(semantic_dir, db_ids)
    output_mismatches = {
        db_id
        for db_id, digest in actual_outputs.items()
        if recorded_outputs.get(db_id) != digest
    }
    if output_mismatches:
        raise ValueError(
            "BIRD-curated schemas differ from frozen provenance: "
            + ", ".join(sorted(output_mismatches))
        )

    archive_sha256 = provenance.get("source_archive_sha256")
    source_hashes = provenance.get("source_hashes")
    if not isinstance(archive_sha256, str) or not isinstance(source_hashes, dict):
        raise TypeError("BIRD-curated provenance is missing source hashes")
    extracted_dir = semantic_dir.parent / "extracted" / archive_sha256
    source_mismatches = []
    for relative, expected in source_hashes.items():
        if not isinstance(relative, str) or not isinstance(expected, str):
            raise TypeError("BIRD-curated provenance has invalid source hashes")
        relative_path = PurePosixPath(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError("BIRD-curated provenance has an unsafe source path")
        source_path = extracted_dir.joinpath(*relative_path.parts)
        if (
            not source_path.is_file()
            or hashlib.sha256(source_path.read_bytes()).hexdigest() != expected
        ):
            source_mismatches.append(relative)
    if source_mismatches:
        raise ValueError(
            "BIRD-curated source metadata differs from frozen provenance: "
            + ", ".join(sorted(source_mismatches))
        )
    return {
        "provenance_sha256": hashlib.sha256(raw).hexdigest(),
        "source_archive_sha256": archive_sha256,
        "construction_policy": provenance["construction_policy"],
        "contains_questions": False,
        "contains_evidence": False,
        "contains_gold_sql": False,
        "source_hashes_sha256": hashlib.sha256(
            json.dumps(source_hashes, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "output_hashes": actual_outputs,
    }


def _context_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _module_available(name: str) -> str:
    try:
        __import__(name)
    except ImportError:
        return "missing"
    return "installed"


def _credential_env(transport: str) -> str:
    variables = {
        "anthropic": "ANTHROPIC_API_KEY",
        CLAUDE_SUBSCRIPTION_TRANSPORT: "CLAUDE_CODE_OAUTH_TOKEN",
        "google": "GEMINI_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "openai": "OPENAI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
    }
    try:
        return variables[transport]
    except KeyError as exc:
        raise ValueError(f"Unsupported model transport: {transport}") from exc
