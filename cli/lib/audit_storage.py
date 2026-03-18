"""Audit capture persistence — save/load/list workload runs."""

import json
import os
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from lib.fleet.models import WorkloadRun


class AuditStorage:
    """Manages audit capture runs at ~/.rdst/audits/<target>/<run_id>.json."""

    def __init__(self, base_dir: Optional[Path] = None):
        self.base_dir = base_dir or (Path.home() / ".rdst" / "audits")

    def generate_run_id(self, target_name: str = "") -> str:
        """Generate a unique run ID: audit_<target>_<timestamp>."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        if target_name:
            return f"audit_{target_name}_{ts}"
        short = uuid.uuid4().hex[:6]
        return f"{ts}_{short}"

    def save_run(self, run: WorkloadRun) -> str:
        """Save an audit capture run. Returns the file path."""
        target_dir = self.base_dir / run.target_name
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{run.run_id}.json"
        data = asdict(run)
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)
        return str(path)

    def load_run(self, target: str, run_id: str) -> Optional[Dict[str, Any]]:
        """Load an audit capture run by target and run_id."""
        path = self.base_dir / target / f"{run_id}.json"
        if path.exists():
            with open(path) as f:
                return json.load(f)

        # Try prefix match within target dir
        target_dir = self.base_dir / target
        if target_dir.exists():
            for p in sorted(target_dir.glob("*.json"), reverse=True):
                if p.stem.startswith(run_id):
                    with open(p) as f:
                        return json.load(f)
        return None

    def list_runs(self, target: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
        """List audit capture runs, optionally filtered by target."""
        results = []

        if target:
            target_dir = self.base_dir / target
            if target_dir.exists():
                results = self._list_from_dir(target_dir)
        else:
            if self.base_dir.exists():
                for target_dir in sorted(self.base_dir.iterdir()):
                    if target_dir.is_dir():
                        results.extend(self._list_from_dir(target_dir))

        # Sort by started_at descending
        results.sort(key=lambda r: r.get("started_at", ""), reverse=True)
        return results[:limit]

    def _list_from_dir(self, target_dir: Path) -> List[Dict[str, Any]]:
        results = []
        for path in sorted(target_dir.glob("*.json"), reverse=True):
            try:
                with open(path) as f:
                    data = json.load(f)
                results.append({
                    "run_id": data.get("run_id", path.stem),
                    "target_name": data.get("target_name", target_dir.name),
                    "started_at": data.get("started_at", ""),
                    "duration_seconds": data.get("duration_seconds", 0),
                    "total_queries": data.get("total_queries", 0),
                    "source": data.get("source", ""),
                    "has_analysis": data.get("analysis") is not None,
                    "path": str(path),
                })
            except (json.JSONDecodeError, KeyError):
                pass
        return results

    def delete_run(self, target: str, run_id: str) -> bool:
        """Delete an audit capture run."""
        path = self.base_dir / target / f"{run_id}.json"
        if path.exists():
            os.unlink(path)
            return True
        return False
