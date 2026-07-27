"""Unit tests for the emailable report artifact resolver.

Covers the precedence the web "email me this report" flow depends on: an
artifact the CLI already wrote wins, anything else is regenerated through
the same renderer, and a capture with no health analysis has no report.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from features.audit.report.artifact import (
    report_html_for_run,
    reports_dir,
    save_report_locally,
)


def _write_snapshot(tmp_rdst_home: Path, snapshot_id: str, data: dict) -> None:
    directory = tmp_rdst_home / "fleet" / "snapshots"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{snapshot_id}.json").write_text(json.dumps(data))


def _write_capture(tmp_rdst_home: Path, target: str, run_id: str, data: dict) -> None:
    directory = tmp_rdst_home / "audits" / target
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{run_id}.json").write_text(json.dumps(data))


def _audit_snapshot(target: str = "prod") -> dict:
    return {
        "target_name": target,
        "engine": "postgresql",
        "audited_at": "2026-07-20T10:00:00+00:00",
        "health_analysis": {
            "health_score": 72,
            "health_label": "fair",
            "top_findings": [
                {"severity": "warn", "title": "Low cache hit rate", "body": "94%"},
            ],
        },
    }


def test_missing_run_has_no_report(tmp_rdst_home):
    assert report_html_for_run("nope") is None


def test_saved_artifact_wins_over_regeneration(tmp_rdst_home):
    _write_snapshot(tmp_rdst_home, "audit_prod_1", _audit_snapshot())
    save_report_locally("audit_prod_1", "<html>from the CLI</html>")

    html, subject = report_html_for_run("audit_prod_1")

    assert html == "<html>from the CLI</html>"
    assert subject == "RDST Audit Report: prod"
    assert (reports_dir() / "audit_prod_1.html").is_file()


def test_quick_audit_regenerates_with_health_score(tmp_rdst_home):
    _write_snapshot(tmp_rdst_home, "audit_prod_1", _audit_snapshot())

    html, subject = report_html_for_run("audit_prod_1")

    assert subject == "RDST Audit Report: prod"
    assert ">72</text>" in html
    assert "Low cache hit rate" in html


def test_fleet_snapshot_uses_the_fleet_renderer(tmp_rdst_home):
    _write_snapshot(
        tmp_rdst_home,
        "fleet_1",
        {
            "snapshot_id": "fleet_1",
            "name": "production",
            "created_at": "2026-07-20T10:00:00+00:00",
            "targets_audited": 2,
            "results": [_audit_snapshot("a"), _audit_snapshot("b")],
            "fleet_insights": {"health_score": 55, "health_label": "poor"},
        },
    )

    html, subject = report_html_for_run("fleet_1")

    assert subject == "RDST Fleet Audit Report: production"
    assert ">55</text>" in html
    assert "production" in html


def test_capture_without_health_analysis_has_no_report(tmp_rdst_home):
    _write_capture(
        tmp_rdst_home,
        "prod",
        "20260720_100000_abcdef",
        {
            "run_id": "20260720_100000_abcdef",
            "target_name": "prod",
            "started_at": "2026-07-20T10:00:00+00:00",
            "duration_seconds": 60,
            "queries": [],
            "analysis": None,
            "health_analysis": None,
        },
    )

    assert report_html_for_run("20260720_100000_abcdef") is None


def test_capture_with_health_analysis_lifts_workload_fields(tmp_rdst_home):
    capture = {
        "run_id": "20260720_100000_abcdef",
        "target_name": "prod",
        "engine": "postgresql",
        "started_at": "2026-07-20T10:00:00+00:00",
        "duration_seconds": 120,
        "total_queries": 3,
        "queries": [
            {
                "query_hash": "h1",
                "normalized_sql": "SELECT * FROM orders",
                "calls": 10,
                "mean_time_ms": 5.0,
                "total_time_ms": 50.0,
            }
        ],
        "analysis": {
            "optimization_priorities": [
                {"action": "Add index on orders.id", "details": "hot path"},
            ],
        },
        "health_analysis": _audit_snapshot()["health_analysis"],
    }
    _write_capture(tmp_rdst_home, "prod", "20260720_100000_abcdef", capture)

    html, subject = report_html_for_run("20260720_100000_abcdef")

    assert subject == "RDST Audit Report: prod"
    assert ">72</text>" in html
    # Reachable only through the lifted "workload" key: the renderer reads
    # next steps from workload.analysis.
    assert "Add index on orders.id" in html
