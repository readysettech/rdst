"""Audit capture persistence — save/load/list workload runs."""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import WorkloadRun

logger = logging.getLogger(__name__)


class AuditStorage:
    """Manages audit capture runs at ~/.rdst/audits/<target>/<run_id>.json."""

    def __init__(self, base_dir: Path | None = None):
        self.base_dir = base_dir or (Path.home() / ".rdst" / "audits")

    def generate_run_id(self, target_name: str = "") -> str:
        """Generate a unique run ID."""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        if target_name:
            return f"audit_{target_name}_{timestamp}"
        return f"{timestamp}_{uuid.uuid4().hex[:6]}"

    def save_run(self, run: WorkloadRun, extra: dict[str, Any] | None = None) -> str:
        """Persist a run. `extra` carries audit fields that live alongside the
        WorkloadRun shape in the saved JSON."""
        target_dir = self.base_dir / run.target_name
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{run.run_id}.json"
        data = asdict(run)
        if extra:
            data.update(extra)
        with open(path, "w") as file_obj:
            json.dump(data, file_obj, indent=2, default=str)
        return str(path)

    def load_run(self, target: str, run_id: str) -> dict[str, Any] | None:
        path = self.base_dir / target / f"{run_id}.json"
        if path.exists():
            with open(path) as file_obj:
                return json.load(file_obj)

        target_dir = self.base_dir / target
        if target_dir.exists():
            for candidate in sorted(target_dir.glob("*.json"), reverse=True):
                if candidate.stem.startswith(run_id):
                    with open(candidate) as file_obj:
                        return json.load(file_obj)
        return None

    def list_runs(
        self, target: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []

        if target:
            target_dir = self.base_dir / target
            if target_dir.exists():
                results = self._list_from_dir(target_dir)
        elif self.base_dir.exists():
            for target_dir in sorted(self.base_dir.iterdir()):
                if target_dir.is_dir():
                    results.extend(self._list_from_dir(target_dir))

        results.sort(key=lambda result: result.get("started_at", ""), reverse=True)
        return results[:limit]

    def _list_from_dir(self, target_dir: Path) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for path in sorted(target_dir.glob("*.json"), reverse=True):
            try:
                with open(path) as file_obj:
                    data = json.load(file_obj)
                results.append(
                    {
                        "run_id": data.get("run_id", path.stem),
                        "target_name": data.get("target_name", target_dir.name),
                        # A capture that merged an audit carries both keys; the
                        # audit's engine wins, matching the report header.
                        "engine": data.get("engine") or data.get("db_engine", ""),
                        "started_at": data.get("started_at", ""),
                        "duration_seconds": data.get("duration_seconds", 0),
                        "total_queries": data.get("total_queries", 0),
                        "source": data.get("source", ""),
                        "has_analysis": data.get("analysis") is not None,
                        "path": str(path),
                    }
                )
            except (json.JSONDecodeError, KeyError):
                pass
        return results

    def delete_run(self, target: str, run_id: str) -> bool:
        path = self.base_dir / target / f"{run_id}.json"
        if path.exists():
            os.unlink(path)
            return True
        return False


def list_audit_runs(
    target: str | None = None, limit: int = 20
) -> list[dict[str, Any]]:
    """Merged run history: duration captures plus quick-audit snapshots.

    Captures come from AuditStorage; quick audits (no capture window) are
    saved to the fleet snapshot store as `audit_*.json`. Newest first.
    """
    runs = AuditStorage().list_runs(target=target, limit=limit)

    from features.fleet import SnapshotStore

    store = SnapshotStore()
    if store.base_dir.exists():
        for path in sorted(store.base_dir.glob("audit_*.json"), reverse=True):
            try:
                with open(path) as file_obj:
                    data = json.load(file_obj)
                snap_target = data.get("target_name", "")
                if target and snap_target != target:
                    continue
                # Skip snapshots that also have a workload run — those are
                # already listed via their capture entry.
                if (data.get("workload") or {}).get("run_id"):
                    continue
                # A failed health-LLM attempt leaves {"error": ...} in
                # health_analysis; that is not an analyzed run.
                health = data.get("health_analysis")
                has_analysis = bool(health) and not (
                    isinstance(health, dict) and set(health) == {"error"}
                )
                runs.append(
                    {
                        "run_id": path.stem,
                        "target_name": snap_target,
                        "engine": data.get("engine", ""),
                        "started_at": (data.get("audited_at") or "")[:19],
                        "duration_seconds": 0,
                        "total_queries": len(data.get("top_queries", [])),
                        "source": "quick",
                        "has_analysis": has_analysis,
                        "path": str(path),
                    }
                )
            except Exception:
                continue
    runs.sort(key=lambda run: run.get("started_at", ""), reverse=True)
    return runs[:limit]


def find_audit_run(run_id: str, target: str | None = None) -> dict[str, Any] | None:
    """Locate a run by ID (exact or prefix): snapshots first, then captures."""
    from features.fleet import SnapshotStore

    store = SnapshotStore()
    if store.base_dir.exists():
        candidates = [store.base_dir / f"{run_id}.json"]
        candidates.extend(
            path
            for path in sorted(store.base_dir.glob("*.json"), reverse=True)
            if path.stem.startswith(run_id)
        )
        for path in candidates:
            if not path.exists():
                continue
            try:
                with open(path) as file_obj:
                    return json.load(file_obj)
            except (OSError, json.JSONDecodeError) as e:
                logger.warning("Skipping unreadable audit snapshot %s: %s", path, e)

    storage = AuditStorage()
    if not target:
        for run in storage.list_runs(limit=200):
            if run["run_id"] == run_id or run["run_id"].startswith(run_id):
                target = run["target_name"]
                run_id = run["run_id"]
                break
    if target:
        return storage.load_run(target, run_id)
    return None
