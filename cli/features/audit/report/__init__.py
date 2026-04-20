"""Report generation package — HTML audit/fleet health reports."""

from features.audit.report.report import (
    render_report_html,
    compute_fleet_savings,
)

__all__ = [
    "render_report_html",
    "compute_fleet_savings",
]
