from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from filelock import FileLock

from shared.persistence import write_json, write_text

from .models import AttemptRecord, ModelCallRecord


def make_attempt_key(
    *,
    dataset_revision: str,
    dialect: str,
    track: str,
    context_mode: str,
    model_identity: str,
    interaction_mode: str | None = None,
    question_id: int,
    repetition: int,
) -> str:
    identity = [
        dataset_revision,
        dialect,
        track,
        context_mode,
        model_identity,
        question_id,
        repetition,
    ]
    if interaction_mode is not None:
        identity.insert(4, interaction_mode)
    encoded = json.dumps(identity, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


class ArtifactStore:
    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.manifest_path = run_dir / "manifest.json"
        self.attempts_path = run_dir / "attempts.jsonl"
        self.call_receipts_path = run_dir / "calls.jsonl"
        self.summary_json_path = run_dir / "summary.json"
        self.summary_markdown_path = run_dir / "summary.md"

    def initialize(self, manifest: dict[str, Any]) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        if self.manifest_path.exists():
            existing = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            if existing.get("run_id") != manifest.get("run_id"):
                raise ValueError(
                    f"Run directory {self.run_dir} belongs to "
                    f"{existing.get('run_id')!r}"
                )
            resume_fields = (
                "artifact_schema_version",
                "dataset_revision",
                "gold_exclusion_revision",
                "excluded_gold_case_ids",
                "scorer_conformance",
                "gold_replay_receipt",
                "dialect",
                "suite",
                "partition_provenance",
                "holdout_campaign_id",
                "track",
                "context_mode",
                "interaction_mode",
                "clarification_policy",
                "case_ids",
                "gold_result_fingerprint_codec",
                "gold_result_fingerprints",
                "unstable_gold_case_ids",
                "repetitions",
                "models",
                "route_checks",
                "filter_model",
                "database_provision",
                "context_hashes",
                "rdst_revision",
                "rdst_dirty",
                "rdst_diff_sha256",
                "benchmark_protocol_sha256",
                "query_bounds",
                "run_limits",
                "source_revision",
                "public_data_sha256",
                "frozen_examples_sha256",
                "qualification_protocol",
                "official_score",
                "official_score_blocker",
                "excluded_capabilities",
                "simulator_answer_sources",
                "product_max_rows",
                "product_enforce_result_limit",
                "execution_scoring_sql",
                "execution_correctness_policy",
                "reference_result_fingerprints",
                "derived_from",
            )
            for field in resume_fields:
                if _resume_value(field, existing.get(field)) != _resume_value(
                    field, manifest.get(field)
                ):
                    raise ValueError(
                        f"Run {manifest.get('run_id')!r} has different {field} settings"
                    )
            return
        write_json(self.manifest_path, manifest)

    def run_lock(self):
        self.run_dir.mkdir(parents=True, exist_ok=True)
        return FileLock(f"{self.run_dir / '.run'}.lock", timeout=10)

    def append_attempt(self, attempt: AttemptRecord | dict[str, Any]) -> None:
        data = attempt.to_dict() if isinstance(attempt, AttemptRecord) else attempt
        if not data.get("attempt_key"):
            raise ValueError("Attempt record requires attempt_key")
        _append_jsonl(self.attempts_path, data)

    def append_call_receipt(self, record: ModelCallRecord | dict[str, Any]) -> None:
        data = record.to_dict() if isinstance(record, ModelCallRecord) else record
        if not data.get("attempt_id"):
            raise ValueError("Call receipt requires attempt_id")
        _append_jsonl(self.call_receipts_path, data)

    def load_attempts(self) -> list[dict[str, Any]]:
        attempts = _load_jsonl(self.attempts_path, record_name="attempt")
        for attempt in attempts:
            if not attempt.get("attempt_key"):
                raise ValueError(f"Invalid attempt record in {self.attempts_path}")
        return attempts

    def load_call_receipts(self) -> list[dict[str, Any]]:
        receipts = _load_jsonl(self.call_receipts_path, record_name="call receipt")
        for receipt in receipts:
            if not receipt.get("attempt_id"):
                raise ValueError(f"Invalid call receipt in {self.call_receipts_path}")
        return receipts

    def completed_keys(self) -> set[str]:
        return {
            str(attempt["attempt_key"])
            for attempt in self.load_attempts()
            if attempt.get("scored", _legacy_attempt_is_scored(attempt))
        }

    def pending_keys(self, expected: Iterable[str]) -> list[str]:
        completed = self.completed_keys()
        return [key for key in expected if key not in completed]

    def write_summary(self, summary: dict[str, Any], markdown: str) -> None:
        write_json(self.summary_json_path, summary)
        write_text(self.summary_markdown_path, markdown)


def _append_jsonl(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(f"{path}.lock", timeout=10):
        _repair_trailing_record(path)
        with open(path, "a", encoding="utf-8", newline="\n") as file_obj:
            file_obj.write(json.dumps(data, separators=(",", ":"), default=str))
            file_obj.write("\n")
            file_obj.flush()
            os.fsync(file_obj.fileno())


def _load_jsonl(path: Path, *, record_name: str) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    records = []
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            if index == len(lines) - 1:
                continue
            raise ValueError(f"Invalid JSONL record {index + 1} in {path}")
        if not isinstance(parsed, dict):
            raise TypeError(f"Invalid {record_name} record {index + 1} in {path}")
        records.append(parsed)
    return records


def _legacy_attempt_is_scored(attempt: dict[str, Any]) -> bool:
    return attempt.get("outcome") not in {"transport_error", "uncontrolled_route"}


def _repair_trailing_record(path: Path) -> None:
    if not path.exists():
        return
    content = path.read_bytes()
    if not content or content.endswith(b"\n"):
        return
    last_newline = content.rfind(b"\n")
    tail = content[last_newline + 1 :]
    try:
        json.loads(tail)
    except (json.JSONDecodeError, UnicodeDecodeError):
        with open(path, "r+b") as file_obj:
            file_obj.truncate(last_newline + 1)
    else:
        with open(path, "ab") as file_obj:
            file_obj.write(b"\n")


def _normalized(value: Any) -> Any:
    return json.loads(json.dumps(value, sort_keys=True, default=str))


def _resume_value(field: str, value: Any) -> Any:
    normalized = _normalized(value)
    if field != "route_checks":
        return normalized
    return _strip_endpoint_status(normalized)


def _strip_endpoint_status(value: Any, *, in_endpoint: bool = False) -> Any:
    """Remove transient endpoint health without weakening route identity checks."""
    if isinstance(value, list):
        return [_strip_endpoint_status(item, in_endpoint=in_endpoint) for item in value]
    if not isinstance(value, dict):
        return value

    result: dict[str, Any] = {}
    for key, item in value.items():
        if in_endpoint and key == "status":
            continue
        result[key] = _strip_endpoint_status(
            item,
            in_endpoint=in_endpoint or key == "eligible_endpoints",
        )
    return result
