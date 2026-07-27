"""Snapshot store — persistence for fleet audit snapshots."""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .models import FleetAuditSnapshot, FleetDiff, FleetDiffEntry


def _snapshot_entries(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Per-target result dicts: a fleet snapshot's `results`, or the
    single-target snapshot itself."""
    results = data.get("results")
    if isinstance(results, list):
        return [result for result in results if isinstance(result, dict)]
    return [data]


def _capture_duration(entry: dict[str, Any]) -> int:
    """Capture window of one audited target; 0 for a metrics-only audit."""
    workload = entry.get("workload")
    value = workload.get("duration_seconds") if isinstance(workload, dict) else None
    if not value:
        value = entry.get("duration_seconds")
    return int(value) if isinstance(value, (int, float)) else 0


class SnapshotStore:
    """Manages audit snapshots at ~/.rdst/fleet/snapshots/."""

    def __init__(self, base_dir: Path | None = None):
        self.base_dir = base_dir or (Path.home() / ".rdst" / "fleet" / "snapshots")

    def save(self, snapshot: FleetAuditSnapshot) -> str:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        path = self.base_dir / f"{snapshot.snapshot_id}.json"
        with open(path, "w") as file_obj:
            json.dump(asdict(snapshot), file_obj, indent=2, default=str)
        return str(path)

    def save_raw(self, snapshot_id: str, data: Any) -> str:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        path = self.base_dir / f"{snapshot_id}.json"
        with open(path, "w") as file_obj:
            json.dump(data, file_obj, indent=2, default=str)
        return str(path)

    def load(self, snapshot_id: str) -> dict[str, Any] | None:
        path = self.base_dir / f"{snapshot_id}.json"
        if path.exists():
            with open(path) as file_obj:
                return json.load(file_obj)

        if self.base_dir.exists():
            for candidate in sorted(self.base_dir.glob("*.json")):
                if candidate.stem.startswith(snapshot_id):
                    with open(candidate) as file_obj:
                        return json.load(file_obj)
        return None

    def list_snapshots(self) -> list[dict[str, Any]]:
        """List snapshot summaries, newest first.

        `kind` distinguishes fleet-audit snapshots (per-target `results`
        list) from single-target audit saves stored in the same directory.
        """
        if not self.base_dir.exists():
            return []

        snapshots: list[dict[str, Any]] = []
        for path in sorted(self.base_dir.glob("*.json"), reverse=True):
            try:
                with open(path) as file_obj:
                    data = json.load(file_obj)
                is_fleet = isinstance(data.get("results"), list)
                entries = _snapshot_entries(data)
                snapshots.append(
                    {
                        "snapshot_id": data.get("snapshot_id", path.stem),
                        "name": data.get("name", path.stem),
                        "created_at": data.get("created_at", ""),
                        "targets_audited": data.get("targets_audited", 0),
                        "target_names": [
                            str(name)
                            for entry in entries
                            if (name := entry.get("target_name"))
                        ],
                        "duration_seconds": max(
                            (_capture_duration(entry) for entry in entries), default=0
                        ),
                        "kind": "fleet" if is_fleet else "single",
                        "path": str(path),
                    }
                )
            except (json.JSONDecodeError, KeyError):
                snapshots.append(
                    {
                        "snapshot_id": path.stem,
                        "name": path.stem,
                        "created_at": "",
                        "targets_audited": 0,
                        "target_names": [],
                        "duration_seconds": 0,
                        "kind": "single",
                        "path": str(path),
                    }
                )
        return snapshots

    def delete(self, snapshot_id: str) -> bool:
        path = self.base_dir / f"{snapshot_id}.json"
        if path.exists():
            os.unlink(path)
            return True
        return False

    def diff(self, baseline_id: str, current_id: str) -> FleetDiff | None:
        baseline = self.load(baseline_id)
        current = self.load(current_id)
        if baseline is None or current is None:
            return None

        def _results_by_name(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
            results = snapshot.get("results", [])
            if isinstance(results, list) and results:
                return {
                    result.get("target_name", ""): result
                    for result in results
                    if isinstance(result, dict)
                }
            if snapshot.get("target_name"):
                return {snapshot["target_name"]: snapshot}
            return {}

        base_results = _results_by_name(baseline)
        current_results = _results_by_name(current)
        base_targets = set(base_results.keys())
        current_targets = set(current_results.keys())

        entries: list[FleetDiffEntry] = []
        compare_fields = [
            ("cache_hit_rate", "metrics"),
            ("connection_utilization_pct", "metrics"),
            ("database_size_mb", "metrics"),
            ("read_pct", "metrics"),
            ("tracked_query_count", "metrics"),
        ]

        for target in base_targets & current_targets:
            base_result = base_results[target]
            current_result = current_results[target]

            base_verdict = (base_result.get("sizing") or {}).get("verdict")
            current_verdict = (current_result.get("sizing") or {}).get("verdict")
            if base_verdict != current_verdict:
                entries.append(
                    FleetDiffEntry(
                        target_name=target,
                        field_name="sizing_verdict",
                        old_value=base_verdict,
                        new_value=current_verdict,
                    )
                )

            for field_name, section in compare_fields:
                old_value = (base_result.get(section) or {}).get(field_name)
                new_value = (current_result.get(section) or {}).get(field_name)
                if old_value is not None and new_value is not None and old_value != new_value:
                    change_pct = None
                    if old_value != 0:
                        change_pct = round(((new_value - old_value) / abs(old_value)) * 100, 1)
                    entries.append(
                        FleetDiffEntry(
                            target_name=target,
                            field_name=field_name,
                            old_value=old_value,
                            new_value=new_value,
                            change_pct=change_pct,
                        )
                    )

        return FleetDiff(
            baseline_id=baseline_id,
            current_id=current_id,
            baseline_date=baseline.get("created_at", ""),
            current_date=current.get("created_at", ""),
            entries=entries,
            new_targets=sorted(current_targets - base_targets),
            removed_targets=sorted(base_targets - current_targets),
        )
