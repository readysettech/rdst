"""The emailable HTML artifact for a saved audit or fleet run.

The CLI writes the report it emails to ~/.rdst/reports/<id>.html. The web UI
emails that same file, so both surfaces deliver identical output. When the
file is absent (older runs, or a run saved before the report step) the same
generator regenerates it from the stored snapshot.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from shared.constants import rdst_data_dir

from ..storage import find_audit_run
from .report import render_v3_fleet_html, render_v3_single_html

logger = logging.getLogger(__name__)


def reports_dir() -> Path:
    return rdst_data_dir() / "reports"


def save_report_locally(run_id: str, html: str) -> str | None:
    """Persist the report artifact for `run_id`. Best-effort: returns the
    path it wrote, or None when the write failed."""
    try:
        directory = reports_dir()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{run_id}.html"
        path.write_text(html)
        return str(path)
    except Exception as exc:
        logger.warning("Could not save report for %s: %s", run_id, exc)
        return None


def _is_fleet(data: dict[str, Any]) -> bool:
    return "snapshot_id" in data and isinstance(data.get("results"), list)


def _is_capture(data: dict[str, Any]) -> bool:
    """Capture runs carry the WorkloadRun shape. Mirrors the frontend
    discriminator in web-apps/apps/rdst/src/lib/useAudit.ts."""
    return "queries" in data and "run_id" in data


def _capture_as_audit(data: dict[str, Any]) -> dict[str, Any]:
    """Reshape a capture run into the single-target report shape, where the
    workload lives under a "workload" key."""
    lifted = ("queries", "analysis", "duration_seconds", "total_queries")
    adapted = {key: value for key, value in data.items() if key not in lifted}
    adapted["workload"] = {
        "queries": data.get("queries") or [],
        "analysis": data.get("analysis"),
        "duration_seconds": data.get("duration_seconds") or 0,
        "total_queries": data.get("total_queries") or 0,
    }
    return adapted


def _read_saved_artifact(run_id: str) -> str | None:
    path = reports_dir() / f"{run_id}.html"
    try:
        if path.is_file():
            return path.read_text()
    except OSError as exc:
        logger.warning("Could not read saved report %s: %s", path, exc)
    return None


def report_html_for_run(run_id: str) -> tuple[str, str] | None:
    """(html, email subject) for a saved run, or None when the run has no
    report: a capture with no health analysis has no narrative to ship."""
    data = find_audit_run(run_id)
    if not data:
        return None

    if _is_fleet(data):
        name = data.get("name") or data.get("snapshot_id") or "fleet"
        subject = f"RDST Fleet Audit Report: {name}"
    else:
        subject = f"RDST Audit Report: {data.get('target_name') or 'target'}"

    saved = _read_saved_artifact(run_id)
    if saved:
        return saved, subject

    if _is_fleet(data):
        return render_v3_fleet_html(data, data.get("fleet_insights")), subject
    if _is_capture(data):
        if not data.get("health_analysis"):
            return None
        return render_v3_single_html(_capture_as_audit(data)), subject
    return render_v3_single_html(data), subject
