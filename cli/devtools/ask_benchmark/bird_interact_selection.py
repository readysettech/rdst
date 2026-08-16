from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

BIRD_INTERACT_LITE_REVISION = "f7881a9c2b9630cc4fc13b0c39279740b0a2fd87"
BIRD_INTERACT_LITE_PUBLIC_SHA256 = (
    "d155fa0855bc1885f77df2fcc357d3056e10426cd6093c0042aa99d79067af08"
)
BIRD_INTERACT_OFFICIAL_SELECTION_PATH = Path(__file__).with_name(
    "bird_interact_official_qualification_v1.json"
)

_SAFE_ID = re.compile(r"[A-Za-z0-9_]+")


def load_official_qualification_selection(path: Path | None = None) -> dict[str, Any]:
    """Load and validate the preregistered official qualification case selection."""
    selection_path = path or BIRD_INTERACT_OFFICIAL_SELECTION_PATH
    payload = json.loads(selection_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("Unsupported BIRD-Interact qualification selection schema")
    if payload.get("suite") != "bird-interact-lite-official-qualification-v1":
        raise ValueError("Unexpected BIRD-Interact qualification suite")

    source = payload.get("source")
    if not isinstance(source, dict):
        raise TypeError("BIRD-Interact qualification source must be an object")
    if source.get("revision") != BIRD_INTERACT_LITE_REVISION:
        raise ValueError("BIRD-Interact qualification source revision differs")
    if source.get("public_data_sha256") != BIRD_INTERACT_LITE_PUBLIC_SHA256:
        raise ValueError("BIRD-Interact qualification public data hash differs")

    selection = payload.get("selection")
    if not isinstance(selection, dict):
        raise TypeError("BIRD-Interact qualification selection must be an object")
    cases = selection.get("cases")
    if not isinstance(cases, list) or len(cases) != 10:
        raise ValueError("BIRD-Interact qualification must contain 10 cases")

    seed = selection.get("seed")
    if not isinstance(seed, str) or not seed:
        raise ValueError("BIRD-Interact qualification selection seed is missing")
    excluded = set(selection.get("excluded_inspected_case_ids", []))
    instance_ids: set[str] = set()
    databases: set[str] = set()
    ranks: list[str] = []
    for case in cases:
        if not isinstance(case, dict):
            raise TypeError("BIRD-Interact qualification case must be an object")
        instance_id = case.get("instance_id")
        database = case.get("database")
        rank = case.get("rank_sha256")
        if not isinstance(instance_id, str) or not _SAFE_ID.fullmatch(instance_id):
            raise ValueError("Unsafe BIRD-Interact qualification instance ID")
        if not isinstance(database, str) or not _SAFE_ID.fullmatch(database):
            raise ValueError("Unsafe BIRD-Interact qualification database ID")
        expected_rank = hashlib.sha256(f"{seed}\0{instance_id}".encode()).hexdigest()
        if rank != expected_rank:
            raise ValueError(f"Rank hash differs for {instance_id}")
        if instance_id in excluded:
            raise ValueError(
                f"Inspected case selected for qualification: {instance_id}"
            )
        if instance_id in instance_ids:
            raise ValueError(f"Duplicate qualification case: {instance_id}")
        if database in databases:
            raise ValueError(f"Duplicate qualification database: {database}")
        instance_ids.add(instance_id)
        databases.add(database)
        ranks.append(rank)
    if ranks != sorted(ranks):
        raise ValueError("BIRD-Interact qualification cases are not rank ordered")
    return payload
