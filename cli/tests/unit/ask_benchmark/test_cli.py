import hashlib
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from filelock import FileLock, Timeout

from devtools.ask_benchmark import cli
from devtools.ask_benchmark.bird_dataset import DATASET_REVISION, SCORABLE_CASE_COUNT
from devtools.ask_benchmark.models import ContextMode
from devtools.ask_benchmark.semantic import semantic_content_hash
from devtools.ask_benchmark.value_profiles import (
    VALUE_GROUNDING_CONTEXT_VERSION,
    VALUE_PROFILE_CONTEXT_VERSION,
    VALUE_PROFILE_FORMAT_VERSION,
)


@pytest.mark.skipif(cli.fcntl is None, reason="shared locks require Unix flock")
def test_parallel_benchmark_read_locks_share_the_preparation_lock(tmp_path):
    lock_path = tmp_path / "prepare.lock"

    with (
        cli._shared_file_lock(lock_path, timeout=0),
        cli._shared_file_lock(lock_path, timeout=0),
        pytest.raises(Timeout),
        FileLock(str(lock_path), timeout=0),
    ):
        pass


@pytest.mark.skipif(cli.fcntl is None, reason="shared locks require Unix flock")
def test_exclusive_preparation_lock_blocks_benchmark_reader(tmp_path):
    lock_path = tmp_path / "prepare.lock"

    with (
        FileLock(str(lock_path), timeout=0),
        pytest.raises(Timeout),
        cli._shared_file_lock(lock_path, timeout=0),
    ):
        pass


def test_run_parser_accepts_positive_paid_run_limits():
    args = cli.build_parser().parse_args(
        [
            "run",
            "--models",
            "claude-sonnet-4.6-anthropic-sdk",
            "--max-provider-calls",
            "12",
            "--max-normalized-cost-usd",
            "1.25",
            "--max-wall-time-seconds",
            "90.5",
        ]
    )

    assert args.max_provider_calls == 12
    assert args.max_normalized_cost_usd == Decimal("1.25")
    assert args.max_wall_time_seconds == 90.5
    assert args.context == ContextMode.AUTO_INIT.value
    assert args.schema_format == "rdst-adaptive-schema-v2"


def test_run_parser_accepts_compact_v2_schema_format():
    args = cli.build_parser().parse_args(
        [
            "run",
            "--models",
            "claude-sonnet-4.6-anthropic-sdk",
            "--schema-format",
            "rdst-compact-schema-v2",
        ]
    )

    assert args.schema_format == "rdst-compact-schema-v2"


def test_protocol_fingerprint_covers_canonical_schema_loading():
    relative_paths = {
        path.relative_to(cli.RDST_ROOT).as_posix()
        for path in cli._benchmark_protocol_paths()
    }

    assert "features/ask/engine/ask3/phases/schema.py" in relative_paths
    assert "features/ask/service.py" in relative_paths
    assert "features/analyze/functions/shallow_analysis.py" in relative_paths


def test_frozen_pipeline_receipt_matches_current_protocol():
    receipt_path = Path(cli.__file__).with_name("frozen_pipeline_v3.json")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))

    assert receipt["freeze_id"] == "rdst-ask-auto-init-no-evidence-v3"
    assert receipt["benchmark_protocol_sha256"] == cli._benchmark_protocol_sha256()
    assert receipt["holdout_partition"]["opened"] is False
    assert receipt["acceptance"]["required_baseline_repetitions"] == 3


def test_prepare_parser_accepts_an_isolated_mysql_root_password():
    args = cli.build_parser().parse_args(
        ["prepare", "--mysql-root-password", "ci-root-password"]
    )

    assert args.mysql_root_password == "ci-root-password"


def test_profile_values_derives_databases_without_loading_questions(
    tmp_path, monkeypatch
):
    source_dir = tmp_path / "semantic-auto-init"
    source_dir.mkdir()
    for db_id in cli.BIRD_MINI_DATABASE_IDS:
        (source_dir / f"{db_id}.yaml").write_text(f"target: {db_id}\ntables: {{}}\n")

    def fail_if_questions_are_loaded(*_args, **_kwargs):
        raise AssertionError("question metadata must not be loaded")

    monkeypatch.setattr(cli, "load_cached_cases", fail_if_questions_are_loaded)
    monkeypatch.setattr(cli, "verify_mysql_provision", lambda *_args: {})
    observed = {}

    def build(db_ids, source, output, _connection, *, force):
        observed.update(db_ids=db_ids, source=source, output=output, force=force)
        return {
            "database_stats": {
                db_id: {
                    "indexed_columns": 0,
                    "indexed_values": 0,
                    "truncated_columns": 0,
                }
                for db_id in db_ids
            },
            "wall_time_seconds": 1.0,
            "_reused": True,
        }

    monkeypatch.setattr(cli, "build_exact_value_profiles", build)
    args = SimpleNamespace(
        cache_dir=tmp_path,
        mysql_host="127.0.0.1",
        mysql_port=13316,
        mysql_user="rdst_bird",
        mysql_password="rdst-benchmark",
        mysql_database_prefix="bird_",
        force=False,
    )

    assert cli._profile_values(args) == 0
    assert observed["db_ids"] == list(cli.BIRD_MINI_DATABASE_IDS)
    assert observed["source"] == source_dir
    assert observed["force"] is False


@pytest.mark.parametrize(
    ("flag", "value"),
    [
        ("--max-provider-calls", "0"),
        ("--max-normalized-cost-usd", "NaN"),
        ("--max-wall-time-seconds", "-1"),
    ],
)
def test_run_parser_rejects_invalid_paid_run_limits(flag, value):
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(
            [
                "run",
                "--models",
                "claude-sonnet-4.6-anthropic-sdk",
                flag,
                value,
            ]
        )


def test_full_publication_status_requires_run_local_zero_mismatch_receipt(tmp_path):
    manifest = {"suite": "full", "run_id": "full-run"}

    assert cli._publication_status(tmp_path, manifest) == "pending_official_replay"

    (tmp_path / "official-scorer-receipt.json").write_text(
        json.dumps(
            {
                "scope": "full_dataset",
                "full_dataset_case_count": SCORABLE_CASE_COUNT,
                "full_dataset_mismatch_count": 0,
                "full_dataset_replay_sha256": "a" * 64,
                "source_run_id": "full-run",
                "source_artifact_digests": cli._run_artifact_digests(tmp_path),
            }
        )
    )
    assert cli._publication_status(tmp_path, manifest) == "verified"

    (tmp_path / "attempts.jsonl").write_text("tampered\n")
    assert cli._publication_status(tmp_path, manifest) == "failed_official_replay"

    manifest["run_id"] = "other-run"
    assert cli._publication_status(tmp_path, manifest) == "failed_official_replay"


def test_direct_anthropic_doctor_does_not_require_pydantic_ai(monkeypatch, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only")
    monkeypatch.setattr(
        cli,
        "_module_available",
        lambda name: "missing" if name == "pydantic_ai" else "installed",
    )
    monkeypatch.setattr(cli.shutil, "which", lambda _name: "/usr/bin/docker")
    args = SimpleNamespace(
        models="claude-sonnet-4.6-anthropic-sdk",
        structured=True,
    )

    result = cli._doctor(args)

    assert result == 0
    output = capsys.readouterr().out
    assert "PydanticAI: not required" in output
    assert "Anthropic SDK: installed" in output


def test_llm_enriched_context_must_match_frozen_provenance(tmp_path):
    content = b"target: fixture\ntables: {}\n"
    (tmp_path / "fixture.yaml").write_bytes(content)
    (tmp_path / "provenance.json").write_text(
        json.dumps(
            {
                "run_id": "enrichment-v1",
                "dataset_revision": DATASET_REVISION,
                "source_context": ContextMode.AUTO_INIT.value,
                "output_context": ContextMode.LLM_ENRICHED.value,
                "output_hashes": {"fixture": hashlib.sha256(content).hexdigest()},
            }
        )
    )

    provenance = cli._verify_context_provenance(
        tmp_path, ContextMode.LLM_ENRICHED, ["fixture"]
    )
    assert provenance["run_id"] == "enrichment-v1"

    (tmp_path / "fixture.yaml").write_text("tampered")
    with pytest.raises(ValueError, match="differ from frozen provenance"):
        cli._verify_context_provenance(tmp_path, ContextMode.LLM_ENRICHED, ["fixture"])


def test_profiled_values_context_must_match_frozen_provenance(tmp_path):
    schema_path = tmp_path / "fixture.yaml"
    schema_path.write_text("target: fixture\ntables: {}\n")
    profile_path = tmp_path / "fixture.values.json"
    profile_path.write_text(
        json.dumps(
            {
                "format_version": VALUE_PROFILE_FORMAT_VERSION,
                "target": "fixture",
                "columns": [],
            }
        )
    )
    profile_hash = hashlib.sha256(profile_path.read_bytes()).hexdigest()
    (tmp_path / "provenance.json").write_text(
        json.dumps(
            {
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
                "output_schema_hashes": {"fixture": semantic_content_hash(schema_path)},
                "value_profile_hashes": {"fixture": profile_hash},
            }
        )
    )

    provenance = cli._verify_context_provenance(
        tmp_path, ContextMode.AUTO_INIT_PROFILED_VALUES, ["fixture"]
    )
    assert provenance["value_profile_hashes"] == {"fixture": profile_hash}
    assert provenance["matching_policy_version"] == VALUE_GROUNDING_CONTEXT_VERSION

    profile_path.write_text("tampered")
    with pytest.raises(ValueError, match="differs from frozen provenance"):
        cli._verify_context_provenance(
            tmp_path, ContextMode.AUTO_INIT_PROFILED_VALUES, ["fixture"]
        )


@pytest.mark.parametrize(
    "context_mode", [ContextMode.BIRD_CURATED, ContextMode.EVIDENCE]
)
def test_bird_curated_context_must_match_source_and_output_provenance(
    tmp_path, context_mode
):
    archive_sha256 = "a" * 64
    semantic_dir = tmp_path / "semantic"
    semantic_dir.mkdir()
    content = b"target: fixture\ntables: {}\n"
    (semantic_dir / "fixture.yaml").write_bytes(content)
    source_path = tmp_path / "extracted" / archive_sha256 / "dev_tables.json"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"[]")
    (semantic_dir / "provenance.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "dataset_revision": DATASET_REVISION,
                "source_archive_sha256": archive_sha256,
                "source_context": "bird-mini-dev-metadata",
                "output_context": ContextMode.BIRD_CURATED.value,
                "construction_policy": ("dev-tables-plus-database-description-csv-v1"),
                "contains_questions": False,
                "contains_evidence": False,
                "contains_gold_sql": False,
                "source_hashes": {"dev_tables.json": hashlib.sha256(b"[]").hexdigest()},
                "output_hashes": {"fixture": hashlib.sha256(content).hexdigest()},
            }
        )
    )

    provenance = cli._verify_context_provenance(semantic_dir, context_mode, ["fixture"])
    assert provenance["contains_evidence"] is False

    source_path.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="source metadata differs"):
        cli._verify_context_provenance(semantic_dir, context_mode, ["fixture"])


def _holdout_manifest(*, track: str, run_id: str, campaign: str = "frozen-v1"):
    return {
        "dataset": "bird-mini",
        "dataset_revision": DATASET_REVISION,
        "gold_exclusion_revision": "invalid-mysql-gold-v1",
        "excluded_gold_case_ids": [208, 212, 227, 327],
        "dialect": "mysql",
        "suite": "holdout",
        "partition_provenance": {"revision": "partition-v1"},
        "holdout_campaign_id": campaign,
        "context_mode": "llm-enriched",
        "case_ids": list(range(50, 500)),
        "models": [{"name": "sonnet"}],
        "rdst_revision": "revision",
        "rdst_dirty": False,
        "rdst_diff_sha256": "diff",
        "benchmark_protocol_sha256": "protocol",
        "track": track,
        "run_id": run_id,
    }


def test_holdout_access_allows_one_resumable_paired_campaign(tmp_path):
    direct = _holdout_manifest(track="model-only", run_id="direct-v1")
    product = _holdout_manifest(track="rdst-ask", run_id="product-v1")

    cli._reserve_holdout_access(tmp_path, direct)
    cli._reserve_holdout_access(tmp_path, direct)
    cli._reserve_holdout_access(tmp_path, product)

    ledger = json.loads((tmp_path / "holdout-access.json").read_text())
    assert ledger["tracks"] == {
        "model-only": "direct-v1",
        "rdst-ask": "product-v1",
    }


def test_holdout_access_rejects_new_run_or_changed_pipeline(tmp_path):
    direct = _holdout_manifest(track="model-only", run_id="direct-v1")
    cli._reserve_holdout_access(tmp_path, direct)

    replacement = _holdout_manifest(track="model-only", run_id="direct-v2")
    with pytest.raises(ValueError, match="resume that run"):
        cli._reserve_holdout_access(tmp_path, replacement)

    changed = _holdout_manifest(track="rdst-ask", run_id="product-v1")
    changed["benchmark_protocol_sha256"] = "changed"
    with pytest.raises(ValueError, match="different frozen pipeline"):
        cli._reserve_holdout_access(tmp_path, changed)


def test_run_parser_exposes_explicit_holdout_confirmation():
    args = cli.build_parser().parse_args(
        [
            "run",
            "--suite",
            "holdout",
            "--models",
            "claude-sonnet-4.6-anthropic-sdk",
            "--run-id",
            "direct-holdout-v1",
            "--holdout-campaign-id",
            "frozen-v1",
            "--confirm-open-holdout",
        ]
    )

    assert args.suite == "holdout"
    assert args.confirm_open_holdout is True
    assert args.holdout_campaign_id == "frozen-v1"


def test_clarification_commands_use_gold_alignment_language():
    align = cli.build_parser().parse_args(
        [
            "clarification-align",
            "source-run",
            "--output",
            "alignment.json",
        ]
    )
    qualify = cli.build_parser().parse_args(
        [
            "clarification-qualify",
            "alignment.json",
            "--replay-current-policy",
        ]
    )

    assert align.command == "clarification-align"
    assert qualify.alignment_packet.name == "alignment.json"
    assert qualify.replay_current_policy is True


def test_bird_interact_commands_keep_live_and_rescore_workflows_separate():
    qualify = cli.build_parser().parse_args(
        ["bird-interact-qualify", "--run-id", "interact-v1"]
    )
    rescore = cli.build_parser().parse_args(
        [
            "bird-interact-rescore",
            "source-run",
            "--run-id",
            "interact-v2-rescore",
        ]
    )

    assert qualify.model == "claude-sonnet-4.6-anthropic-sdk"
    assert qualify.case_ids == "alien_3,alien_8"
    assert qualify.product_max_rows == 100
    assert rescore.source_run_dir.name == "source-run"
    assert rescore.run_id == "interact-v2-rescore"


def test_generation_ablation_parser_is_explicitly_internal():
    args = cli.build_parser().parse_args(
        [
            "generation-ablation",
            "source-run",
            "--run-id",
            "ablation-v1",
        ]
    )

    assert args.command == "generation-ablation"
    assert args.source_run_dir.name == "source-run"
    assert args.model == "claude-sonnet-4.6-anthropic-sdk"
    assert args.run_id == "ablation-v1"


def test_structured_system_ablation_parser_is_explicitly_internal():
    args = cli.build_parser().parse_args(
        [
            "structured-system-ablation",
            "source-run",
            "--run-id",
            "system-ablation-v1",
        ]
    )

    assert args.command == "structured-system-ablation"
    assert args.source_run_dir.name == "source-run"
    assert args.model == "claude-sonnet-4.6-anthropic-sdk"
    assert args.run_id == "system-ablation-v1"


def test_structured_response_ablation_parser_is_explicitly_internal():
    args = cli.build_parser().parse_args(
        [
            "structured-response-ablation",
            "source-run",
            "--run-id",
            "response-ablation-v1",
        ]
    )

    assert args.command == "structured-response-ablation"
    assert args.source_run_dir.name == "source-run"
    assert args.model == "claude-sonnet-4.6-anthropic-sdk"
    assert args.run_id == "response-ablation-v1"


def test_prompt_ablation_parser_is_explicitly_internal():
    args = cli.build_parser().parse_args(
        [
            "prompt-ablation",
            "source-run",
            "--run-id",
            "prompt-ablation-v1",
        ]
    )

    assert args.command == "prompt-ablation"
    assert args.source_run_dir.name == "source-run"
    assert args.model == "claude-sonnet-4.6-anthropic-sdk"
    assert args.run_id == "prompt-ablation-v1"
    assert args.experiment == "lean-vs-detailed-v1"


def test_prompt_ablation_parser_accepts_enum_grounding_experiment():
    args = cli.build_parser().parse_args(
        [
            "prompt-ablation",
            "source-run",
            "--run-id",
            "enum-ablation-v1",
            "--experiment",
            "enum-grounding-v1",
        ]
    )

    assert args.experiment == "enum-grounding-v1"
