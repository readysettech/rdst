"""Audit service — async generator for single-target and fleet-wide audit."""

import datetime
from dataclasses import asdict
from typing import Any, AsyncGenerator, Dict, List, Optional

from lib.cli.rdst_cli import TargetsConfig
from lib.fleet.models import AuditResult
from lib.services.types import (
    AuditCompleteEvent,
    AuditErrorEvent,
    AuditEvent,
    AuditMetricsCollectedEvent,
    AuditStatusEvent,
    AuditTargetCompleteEvent,
    AuditTargetErrorEvent,
    AuditTargetStartEvent,
)


class AuditService:
    """Service layer for audit operations."""

    def __init__(self, config: Optional[TargetsConfig] = None):
        self._config = config

    def _get_config(self) -> TargetsConfig:
        if self._config is None:
            self._config = TargetsConfig()
            self._config.load()
        return self._config

    async def audit_single(
        self,
        target_name: str,
    ) -> AsyncGenerator[AuditEvent, None]:
        """Run a deep audit on a single target."""
        cfg = self._get_config()
        tc = cfg.get(target_name)
        if tc is None:
            yield AuditErrorEvent(
                type="error",
                message=f"Target '{target_name}' not found",
                phase="config",
            )
            return

        yield AuditStatusEvent(
            type="status",
            phase="connect",
            message=f"Auditing {target_name}...",
        )

        yield AuditTargetStartEvent(
            type="target_start", target_name=target_name, index=0, total=1
        )

        result = self.audit_target(target_name, tc)

        if result.error:
            yield AuditTargetErrorEvent(
                type="target_error",
                target_name=target_name,
                error=result.error,
                index=0,
                total=1,
            )
        else:
            yield AuditMetricsCollectedEvent(
                type="metrics_collected",
                target_name=target_name,
                metrics=asdict(result.metrics) if result.metrics else {},
            )
            yield AuditTargetCompleteEvent(
                type="target_complete",
                target_name=target_name,
                result=asdict(result),
                index=0,
                total=1,
            )

        yield AuditCompleteEvent(
            type="complete",
            success=result.error is None,
            summary=asdict(result),
        )

    async def audit_fleet(
        self,
        targets: List[str],
    ) -> AsyncGenerator[AuditEvent, None]:
        """Run audit across multiple targets sequentially."""
        cfg = self._get_config()
        total = len(targets)

        yield AuditStatusEvent(
            type="status",
            phase="start",
            message=f"Auditing {total} targets...",
        )

        results: List[AuditResult] = []

        for i, target_name in enumerate(targets):
            yield AuditTargetStartEvent(
                type="target_start", target_name=target_name, index=i, total=total
            )

            tc = cfg.get(target_name)
            if tc is None:
                yield AuditTargetErrorEvent(
                    type="target_error",
                    target_name=target_name,
                    error="Target config not found",
                    index=i,
                    total=total,
                )
                results.append(
                    AuditResult(
                        target_name=target_name,
                        engine="unknown",
                        host="unknown",
                        error="Target config not found",
                    )
                )
                continue

            result = self.audit_target(target_name, tc)
            results.append(result)

            if result.error:
                yield AuditTargetErrorEvent(
                    type="target_error",
                    target_name=target_name,
                    error=result.error,
                    index=i,
                    total=total,
                )
            else:
                yield AuditTargetCompleteEvent(
                    type="target_complete",
                    target_name=target_name,
                    result=asdict(result),
                    index=i,
                    total=total,
                )

        successes = sum(1 for r in results if r.error is None)
        failures = total - successes

        yield AuditCompleteEvent(
            type="complete",
            success=failures == 0,
            summary={
                "targets_audited": total,
                "successes": successes,
                "failures": failures,
            },
        )

    def audit_target(self, target_name: str, tc: Dict[str, Any]) -> AuditResult:
        """Audit a single target. Never raises."""
        from lib.fleet.audit_metrics import collect_metrics
        from lib.fleet.audit_scoring import compute_cache_opportunity, compute_sizing_verdict

        engine = tc.get("engine", "unknown")
        host = tc.get("host", "unknown")
        region = tc.get("region")
        instance_class = tc.get("instance_class")
        group = tc.get("group")
        tags = tc.get("tags", [])

        try:
            metrics = collect_metrics(tc)
        except Exception as e:
            return AuditResult(
                target_name=target_name,
                engine=engine,
                host=host,
                error=str(e),
            )

        # Auto-detect region from hostname if not set
        if not region:
            from lib.fleet.csv_importer import detect_region_from_hostname
            region = detect_region_from_hostname(host)

        # Estimate instance class from shared_buffers if not set
        if not instance_class and metrics.shared_buffers_mb > 0:
            from lib.fleet.pricing_data import estimate_class_from_shared_buffers
            instance_class = estimate_class_from_shared_buffers(metrics.shared_buffers_mb)

        sizing = compute_sizing_verdict(metrics, instance_class=instance_class)
        cache_opp = compute_cache_opportunity(metrics)

        # Collect top queries from pg_stat_statements / performance_schema
        top_queries = self._collect_top_queries(
            tc, engine, limit=20,
            stats_window_seconds=metrics.stats_window_seconds,
        )

        # Enrich with AWS storage info if this looks like an RDS instance
        self._enrich_aws_storage(metrics, host, region)

        return AuditResult(
            target_name=target_name,
            engine=engine,
            host=host,
            region=region,
            instance_class=instance_class,
            group=group,
            tags=tags,
            metrics=metrics,
            sizing=sizing,
            cache_opportunity=cache_opp,
            top_queries=top_queries,
            audited_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        )

    def _collect_top_queries(
        self, tc: Dict[str, Any], engine: str, limit: int = 20,
        stats_window_seconds: float = 0.0,
    ) -> List[Dict[str, Any]]:
        """Collect top queries from pg_stat_statements / performance_schema.

        Returns list of query dicts. Never raises — returns empty on failure.
        """
        import hashlib
        import re

        try:
            from lib.db_connection import create_direct_connection
            from lib.functions.query_stats import (
                collect_mysql_digest_stats,
                collect_pg_stat_statements,
            )

            conn = create_direct_connection(tc)
            try:
                if engine == "postgresql":
                    raw = collect_pg_stat_statements(conn)
                else:
                    raw = collect_mysql_digest_stats(conn)
            finally:
                conn.close()

            if not raw:
                return []

            # Normalize and convert to query dicts
            literal_re = re.compile(r"'(?:[^']|'')*'|\b\d+\.?\d*\b")
            total_time = sum(
                float(r.get("total_exec_time") or r.get("total_time_ms") or 0)
                for r in raw
            )

            queries = []
            for r in raw[:limit]:
                if engine == "postgresql":
                    text = str(r.get("query", ""))
                    calls = int(r.get("calls", 0))
                    total_ms = float(r.get("total_exec_time", 0))
                    avg_ms = float(r.get("mean_exec_time", 0))
                else:
                    text = str(r.get("DIGEST_TEXT") or r.get("digest_text") or "")
                    calls = int(r.get("COUNT_STAR") or r.get("count_star") or 0)
                    total_ms = float(r.get("total_time_ms", 0))
                    avg_ms = float(r.get("avg_time_ms", 0))

                if not text.strip():
                    continue

                normalized = literal_re.sub("?", text).strip()
                qhash = hashlib.md5(normalized.encode()).hexdigest()[:12]
                pct = round((total_ms / total_time) * 100, 1) if total_time > 0 else 0

                queries.append({
                    "query_hash": qhash,
                    "query_text": text[:16384],
                    "normalized_query": normalized[:16384],
                    "calls": calls,
                    "total_time_ms": round(total_ms, 2),
                    "avg_time_ms": round(avg_ms, 2),
                    "pct_total_time": pct,
                })

            return queries
        except Exception:
            return []

    def _enrich_aws_storage(
        self, metrics, host: str, region: str
    ) -> None:
        """Try to pull storage info from AWS RDS API. Silently skips on any failure."""
        if not host or not host.endswith(".rds.amazonaws.com"):
            return
        if not region:
            # Try to extract region from hostname (e.g., xxx.cw8z0txtcjjr.us-east-2.rds.amazonaws.com)
            parts = host.split(".")
            for i, p in enumerate(parts):
                if p == "rds" and i >= 1:
                    region = parts[i - 1]
                    break
        if not region:
            return

        try:
            from lib.fleet.aws_auth import get_rds_client
            client = get_rds_client(region)

            # Extract instance identifier from hostname for targeted lookup
            db_identifier = host.split(".")[0]

            # Try regular RDS instances first
            try:
                resp = client.describe_db_instances(
                    Filters=[{"Name": "db-instance-id", "Values": [db_identifier]}]
                )
            except Exception:
                # Fallback to unfiltered if filter fails
                resp = client.describe_db_instances()
            for inst in resp.get("DBInstances", []):
                endpoint = inst.get("Endpoint") or {}
                if endpoint.get("Address") == host:
                    allocated = inst.get("AllocatedStorage")  # GB
                    if allocated:
                        metrics.storage_allocated_gb = float(allocated)
                        data_gb = metrics.database_size_mb / 1024.0
                        metrics.storage_used_pct = round(
                            (data_gb / allocated) * 100, 1
                        )
                    metrics.storage_type = inst.get("StorageType")
                    return

            # Try Aurora clusters (cluster endpoint, not instance endpoint)
            resp = client.describe_db_clusters()
            for cluster in resp.get("DBClusters", []):
                if cluster.get("Endpoint") == host or cluster.get("ReaderEndpoint") == host:
                    allocated = cluster.get("AllocatedStorage")
                    if allocated:
                        metrics.storage_allocated_gb = float(allocated)
                        data_gb = metrics.database_size_mb / 1024.0
                        metrics.storage_used_pct = round(
                            (data_gb / allocated) * 100, 1
                        )
                    return
        except ImportError:
            pass  # botocore not installed
        except Exception:
            pass  # AWS auth failed, expired session, no permissions, etc.
