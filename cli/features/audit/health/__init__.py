"""Database health data collectors.

Gautam's 9-section report adds: vacuum/bloat, index health, connection analysis,
configuration audit, replication health. This package collects that data from
PostgreSQL (and MySQL equivalents where available) and returns a HealthReport
dataclass that can be serialized into the audit snapshot JSON.

Each sub-module is independent and gracefully degrades: missing permissions,
unsupported engine, or no replication setup all return empty lists rather
than raising. The orchestrator collect_health_report() tries each section
and swallows failures per-section so one failure doesn't abort the rest.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from features.audit.health.models import HealthReport


def collect_health_report(
    tc: Dict[str, Any],
    engine: str,
    instance_ram_gb: Optional[float] = None,
    instance_vcpus: Optional[int] = None,
) -> HealthReport:
    """Run all health collectors against a single target.

    Opens one connection, runs every collector, closes. Each collector
    is best-effort and returns empty/None on failure. Never raises.
    """
    from shared.db_connection import create_direct_connection
    from features.audit.health.vacuum_bloat import collect_vacuum_bloat
    from features.audit.health.index_health import collect_index_health
    from features.audit.health.connection_analysis import collect_connection_analysis
    from features.audit.health.config_audit import collect_config_audit
    from features.audit.health.replication_health import collect_replication_health

    report = HealthReport(engine=engine)

    try:
        conn = create_direct_connection(tc)
    except Exception as e:
        report.collection_error = f"connection failed: {e}"
        return report

    try:
        for label, fn in (
            ("vacuum_bloat", lambda c: collect_vacuum_bloat(c, engine)),
            ("index_health", lambda c: collect_index_health(c, engine)),
            ("connections", lambda c: collect_connection_analysis(c, engine)),
            (
                "config",
                lambda c: collect_config_audit(c, engine, instance_ram_gb, instance_vcpus),
            ),
            ("replication", lambda c: collect_replication_health(c, engine)),
        ):
            try:
                result = fn(conn)
                if label == "vacuum_bloat":
                    report.vacuum_bloat = result
                elif label == "index_health":
                    report.index_health = result
                elif label == "connections":
                    report.connections = result
                elif label == "config":
                    report.config_audit = result
                elif label == "replication":
                    report.replication = result
            except Exception as e:  # noqa: BLE001
                report.section_errors[label] = str(e)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return report


__all__ = ["collect_health_report", "HealthReport"]
