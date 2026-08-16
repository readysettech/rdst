from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from .bird_dataset import (
    DATASET_REVISION,
    GOLD_EXCLUSION_REVISION,
    MYSQL_INVALID_GOLD_CASE_IDS,
    SCORABLE_CASE_COUNT,
)
from .models import BenchmarkCase
from .oracle import (
    GOLD_FINGERPRINT_CODEC,
    UNSTABLE_GOLD_CASE_IDS,
    result_fingerprint,
)


class GoldReplayError(RuntimeError):
    pass


def replay_gold_results(
    cases: list[BenchmarkCase],
    executor,
    *,
    repetitions: int,
    provenance: dict[str, Any] | None = None,
):
    if repetitions < 1:
        raise ValueError("Gold replay repetitions must be positive")
    invalid = sorted(
        case.question_id
        for case in cases
        if case.dialect == "mysql" and case.question_id in MYSQL_INVALID_GOLD_CASE_IDS
    )
    if invalid:
        raise GoldReplayError(
            f"Gold replay received excluded invalid-gold cases: {invalid}"
        )
    observed: dict[int, set[str]] = {case.question_id: set() for case in cases}
    failures = []
    for repetition in range(repetitions):
        for case in cases:
            result = executor.execute(case.gold_sql, db_id=case.db_id)
            if not result.succeeded:
                failures.append(
                    {
                        "question_id": case.question_id,
                        "repetition": repetition,
                        "error": result.error or "Gold query failed",
                    }
                )
                continue
            observed[case.question_id].add(result_fingerprint(result))
    if failures:
        raise GoldReplayError(
            f"Gold replay failed for {len(failures)} executions: {failures[:5]}"
        )

    unstable = sorted(
        question_id for question_id, values in observed.items() if len(values) > 1
    )
    undeclared = sorted(set(unstable) - set(UNSTABLE_GOLD_CASE_IDS))
    if undeclared:
        raise GoldReplayError(
            f"Gold replay found undeclared unstable cases: {undeclared}"
        )
    payload: dict[str, Any] = {
        "dataset": "bird-mini",
        "dataset_revision": DATASET_REVISION,
        "gold_exclusion_revision": GOLD_EXCLUSION_REVISION,
        "excluded_gold_case_ids": list(MYSQL_INVALID_GOLD_CASE_IDS),
        "dialect": "mysql",
        "case_count": len(cases),
        "repetitions": repetitions,
        "gold_result_fingerprint_codec": GOLD_FINGERPRINT_CODEC,
        "declared_unstable_case_ids": list(UNSTABLE_GOLD_CASE_IDS),
        "observed_unstable_case_ids": unstable,
        "fingerprints": {
            str(question_id): sorted(values)
            for question_id, values in sorted(observed.items())
        },
    }
    if provenance:
        payload["provenance"] = provenance
    payload["replay_sha256"] = _receipt_hash(payload)
    payload["created_at"] = datetime.now(timezone.utc).isoformat()
    return payload


def compare_gold_replays(first: dict[str, Any], second: dict[str, Any]) -> list[int]:
    identity_fields = (
        "dataset",
        "dataset_revision",
        "gold_exclusion_revision",
        "excluded_gold_case_ids",
        "dialect",
        "case_count",
        "gold_result_fingerprint_codec",
        "declared_unstable_case_ids",
    )
    for field in identity_fields:
        if first.get(field) != second.get(field):
            raise GoldReplayError(f"Gold replays have incompatible {field}")
    unstable = {str(case_id) for case_id in first.get("declared_unstable_case_ids", [])}
    first_fingerprints = first.get("fingerprints", {})
    second_fingerprints = second.get("fingerprints", {})
    changed = sorted(
        int(case_id)
        for case_id in set(first_fingerprints) | set(second_fingerprints)
        if first_fingerprints.get(case_id) != second_fingerprints.get(case_id)
    )
    stable_changes = [case_id for case_id in changed if str(case_id) not in unstable]
    if stable_changes:
        raise GoldReplayError(
            f"Stable gold fingerprints changed for cases: {stable_changes}"
        )
    return changed


def verify_replay_receipt(receipt: dict[str, Any]) -> None:
    expected_hash = receipt.get("replay_sha256")
    payload = {
        key: value
        for key, value in receipt.items()
        if key not in {"replay_sha256", "created_at"}
    }
    if expected_hash != _receipt_hash(payload):
        raise GoldReplayError("Gold replay receipt hash is invalid")
    expected_identity = {
        "dataset": "bird-mini",
        "dataset_revision": DATASET_REVISION,
        "gold_exclusion_revision": GOLD_EXCLUSION_REVISION,
        "excluded_gold_case_ids": list(MYSQL_INVALID_GOLD_CASE_IDS),
        "dialect": "mysql",
        "case_count": SCORABLE_CASE_COUNT,
        "gold_result_fingerprint_codec": GOLD_FINGERPRINT_CODEC,
        "declared_unstable_case_ids": list(UNSTABLE_GOLD_CASE_IDS),
    }
    for field, expected in expected_identity.items():
        if receipt.get(field) != expected:
            raise GoldReplayError(f"Gold replay receipt has incompatible {field}")
    if int(receipt.get("repetitions", 0)) < 2:
        raise GoldReplayError("Gold replay receipt requires at least two repetitions")
    observed_unstable = set(receipt.get("observed_unstable_case_ids", []))
    if not observed_unstable.issubset(UNSTABLE_GOLD_CASE_IDS):
        raise GoldReplayError("Gold replay receipt has undeclared unstable cases")
    fingerprints = receipt.get("fingerprints")
    if not isinstance(fingerprints, dict) or len(fingerprints) != SCORABLE_CASE_COUNT:
        raise GoldReplayError(
            f"Gold replay receipt requires {SCORABLE_CASE_COUNT} fingerprint entries"
        )
    included_exclusions = sorted(
        str(case_id)
        for case_id in MYSQL_INVALID_GOLD_CASE_IDS
        if str(case_id) in fingerprints
    )
    if included_exclusions:
        raise GoldReplayError(
            "Gold replay receipt includes excluded invalid-gold cases: "
            + ", ".join(included_exclusions)
        )
    if any(
        not isinstance(values, list)
        or not values
        or any(not isinstance(value, str) or len(value) != 64 for value in values)
        for values in fingerprints.values()
    ):
        raise GoldReplayError("Gold replay receipt has invalid fingerprints")


def verify_replay_provenance(
    receipt: dict[str, Any], database_provision: dict[str, Any]
) -> None:
    recorded = receipt.get("provenance", {}).get("database_provision")
    if not isinstance(recorded, dict):
        raise GoldReplayError("Gold replay receipt lacks database provenance")
    for field in (
        "archive_sha256",
        "mysql_image_id",
        "mysql_version",
        "database_prefix",
    ):
        if recorded.get(field) != database_provision.get(field):
            raise GoldReplayError(
                f"Gold replay database provenance changed for {field}"
            )


def _receipt_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    return hashlib.sha256(encoded).hexdigest()
