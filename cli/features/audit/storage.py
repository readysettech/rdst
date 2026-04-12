"""Audit capture persistence — save/load/list workload runs."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import WorkloadRun


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

    def save_run(self, run: WorkloadRun) -> str:
        target_dir = self.base_dir / run.target_name
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{run.run_id}.json"
        with open(path, "w") as file_obj:
            json.dump(asdict(run), file_obj, indent=2, default=str)
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
