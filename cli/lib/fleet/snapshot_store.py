"""Snapshot store — persistence for fleet audit snapshots."""

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from lib.fleet.models import FleetAuditSnapshot, FleetDiff, FleetDiffEntry


class SnapshotStore:
    """Manages audit snapshots at ~/.rdst/fleet/snapshots/."""

    def __init__(self, base_dir: Optional[Path] = None):
        self.base_dir = base_dir or (Path.home() / ".rdst" / "fleet" / "snapshots")

    def save(self, snapshot: FleetAuditSnapshot) -> str:
        """Save a fleet audit snapshot. Returns the file path."""
        self.base_dir.mkdir(parents=True, exist_ok=True)
        path = self.base_dir / f"{snapshot.snapshot_id}.json"
        data = asdict(snapshot)
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)
        return str(path)

    def save_raw(self, snapshot_id: str, data: Any) -> str:
        """Save raw dict/result as a snapshot."""
        self.base_dir.mkdir(parents=True, exist_ok=True)
        path = self.base_dir / f"{snapshot_id}.json"
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)
        return str(path)

    def load(self, snapshot_id: str) -> Optional[Dict[str, Any]]:
        """Load a snapshot by ID (or name prefix match)."""
        # Try exact match first
        path = self.base_dir / f"{snapshot_id}.json"
        if path.exists():
            with open(path) as f:
                return json.load(f)

        # Try prefix match
        if self.base_dir.exists():
            for p in sorted(self.base_dir.glob("*.json")):
                if p.stem.startswith(snapshot_id):
                    with open(p) as f:
                        return json.load(f)

        return None

    def list_snapshots(self) -> List[Dict[str, Any]]:
        """List all saved snapshots with metadata."""
        if not self.base_dir.exists():
            return []

        result = []
        for path in sorted(self.base_dir.glob("*.json"), reverse=True):
            try:
                with open(path) as f:
                    data = json.load(f)
                result.append({
                    "snapshot_id": data.get("snapshot_id", path.stem),
                    "name": data.get("name", path.stem),
                    "created_at": data.get("created_at", ""),
                    "targets_audited": data.get("targets_audited", 0),
                    "path": str(path),
                })
            except (json.JSONDecodeError, KeyError):
                result.append({
                    "snapshot_id": path.stem,
                    "name": path.stem,
                    "created_at": "",
                    "targets_audited": 0,
                    "path": str(path),
                })

        return result

    def delete(self, snapshot_id: str) -> bool:
        """Delete a snapshot by ID."""
        path = self.base_dir / f"{snapshot_id}.json"
        if path.exists():
            os.unlink(path)
            return True
        return False

    def diff(self, baseline_id: str, current_id: str) -> Optional[FleetDiff]:
        """Compute diff between two fleet audit snapshots."""
        baseline = self.load(baseline_id)
        current = self.load(current_id)

        if baseline is None or current is None:
            return None

        # Extract results by target name
        # Handles both fleet snapshots (results is a list) and single-target audits (top-level dict)
        def _results_by_name(snap):
            results = snap.get("results", [])
            if isinstance(results, list) and results:
                return {r.get("target_name", ""): r for r in results if isinstance(r, dict)}
            # Single-target audit: the snapshot IS the result
            if snap.get("target_name"):
                return {snap["target_name"]: snap}
            return {}

        base_results = _results_by_name(baseline)
        curr_results = _results_by_name(current)

        base_targets = set(base_results.keys())
        curr_targets = set(curr_results.keys())

        entries = []
        compare_fields = [
            ("cache_hit_rate", "metrics"),
            ("connection_utilization_pct", "metrics"),
            ("database_size_mb", "metrics"),
            ("read_pct", "metrics"),
            ("tracked_query_count", "metrics"),
        ]

        for target in base_targets & curr_targets:
            base_r = base_results[target]
            curr_r = curr_results[target]

            # Compare sizing verdict
            base_verdict = (base_r.get("sizing") or {}).get("verdict")
            curr_verdict = (curr_r.get("sizing") or {}).get("verdict")
            if base_verdict != curr_verdict:
                entries.append(FleetDiffEntry(
                    target_name=target,
                    field_name="sizing_verdict",
                    old_value=base_verdict,
                    new_value=curr_verdict,
                ))

            # Compare numeric metrics
            for field, section in compare_fields:
                old_val = (base_r.get(section) or {}).get(field)
                new_val = (curr_r.get(section) or {}).get(field)
                if old_val is not None and new_val is not None and old_val != new_val:
                    change_pct = None
                    if old_val != 0:
                        change_pct = round(((new_val - old_val) / abs(old_val)) * 100, 1)
                    entries.append(FleetDiffEntry(
                        target_name=target,
                        field_name=field,
                        old_value=old_val,
                        new_value=new_val,
                        change_pct=change_pct,
                    ))

        return FleetDiff(
            baseline_id=baseline_id,
            current_id=current_id,
            baseline_date=baseline.get("created_at", ""),
            current_date=current.get("created_at", ""),
            entries=entries,
            new_targets=sorted(curr_targets - base_targets),
            removed_targets=sorted(base_targets - curr_targets),
        )
