"""Audit service — async generator for single-target and fleet-wide audit."""

from __future__ import annotations

import datetime
from dataclasses import asdict
from typing import Any

from shared.config.targets import TargetsConfig

from features.fleet.csv_importer import detect_region_from_hostname
from features.fleet.pricing import estimate_class_from_shared_buffers

from .events import (
    AuditCompleteEvent,
    AuditErrorEvent,
    AuditEvent,
    AuditMetricsCollectedEvent,
    AuditStatusEvent,
    AuditTargetCompleteEvent,
    AuditTargetErrorEvent,
    AuditTargetStartEvent,
)
from .metrics import collect_metrics
from .models import AuditResult
from .query_stats import collect_mysql_digest_stats, collect_pg_stat_statements
from .scoring import compute_cache_opportunity, compute_sizing_verdict


class AuditService:
    """Service layer for audit operations."""

    def __init__(self, config: TargetsConfig | None = None):
        self._config = config

    def _get_config(self) -> TargetsConfig:
        if self._config is None:
            self._config = TargetsConfig()
            self._config.load()
        return self._config

    async def audit_single(self, target_name: str):
        """Run a deep audit on a single target."""
        config = self._get_config()
        target_config = config.get(target_name)
        if target_config is None:
            yield AuditErrorEvent(
                type="error",
                message=f"Target '{target_name}' not found",
                phase="config",
            )
            return

        yield AuditStatusEvent(type="status", phase="connect", message=f"Auditing {target_name}...")
        yield AuditTargetStartEvent(type="target_start", target_name=target_name, index=0, total=1)

        result = self.audit_target(target_name, target_config)
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

    async def audit_fleet(self, targets: list[str]):
        """Run audit across multiple targets sequentially."""
        config = self._get_config()
        total = len(targets)

        yield AuditStatusEvent(type="status", phase="start", message=f"Auditing {total} targets...")
        results: list[AuditResult] = []

        for index, target_name in enumerate(targets):
            yield AuditTargetStartEvent(
                type="target_start", target_name=target_name, index=index, total=total
            )
            target_config = config.get(target_name)
            if target_config is None:
                yield AuditTargetErrorEvent(
                    type="target_error",
                    target_name=target_name,
                    error="Target config not found",
                    index=index,
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

            result = self.audit_target(target_name, target_config)
            results.append(result)
            if result.error:
                yield AuditTargetErrorEvent(
                    type="target_error",
                    target_name=target_name,
                    error=result.error,
                    index=index,
                    total=total,
                )
            else:
                yield AuditTargetCompleteEvent(
                    type="target_complete",
                    target_name=target_name,
                    result=asdict(result),
                    index=index,
                    total=total,
                )

        failures = sum(1 for result in results if result.error is not None)
        yield AuditCompleteEvent(
            type="complete",
            success=failures == 0,
            summary={
                "targets_audited": total,
                "successes": total - failures,
                "failures": failures,
            },
        )

    def audit_target(self, target_name: str, target_config: dict[str, Any]) -> AuditResult:
        """Audit a single target. Never raises."""
        engine = target_config.get("engine", "unknown")
        host = target_config.get("host", "unknown")
        region = target_config.get("region")
        instance_class = target_config.get("instance_class")
        group = target_config.get("group")
        tags = target_config.get("tags", [])

        try:
            metrics = collect_metrics(target_config)
        except Exception as exc:
            return AuditResult(
                target_name=target_name,
                engine=engine,
                host=host,
                error=str(exc),
            )

        if not region:
            region = detect_region_from_hostname(host)
        if not instance_class and metrics.shared_buffers_mb > 0:
            instance_class = estimate_class_from_shared_buffers(metrics.shared_buffers_mb)

        sizing = compute_sizing_verdict(metrics, instance_class=instance_class)
        cache_opportunity = compute_cache_opportunity(metrics)
        top_queries = self._collect_top_queries(
            target_config,
            engine,
            limit=20,
            stats_window_seconds=metrics.stats_window_seconds,
        )
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
            cache_opportunity=cache_opportunity,
            top_queries=top_queries,
            audited_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        )

    def _collect_top_queries(
        self,
        target_config: dict[str, Any],
        engine: str,
        limit: int = 20,
        stats_window_seconds: float = 0.0,
    ) -> list[dict[str, Any]]:
        """Collect top queries from pg_stat_statements / performance_schema."""
        import hashlib
        import re

        try:
            from shared.db_connection import create_direct_connection

            connection = create_direct_connection(target_config)
            try:
                if engine == "postgresql":
                    raw = collect_pg_stat_statements(connection)
                else:
                    raw = collect_mysql_digest_stats(connection)
            finally:
                connection.close()

            if not raw:
                return []

            literal_re = re.compile(r"'(?:[^']|'')*'|\b\d+\.?\d*\b")
            total_time = sum(
                float(row.get("total_exec_time") or row.get("total_time_ms") or 0)
                for row in raw
            )
            queries: list[dict[str, Any]] = []
            for row in raw[:limit]:
                if engine == "postgresql":
                    text = str(row.get("query", ""))
                    calls = int(row.get("calls", 0))
                    total_ms = float(row.get("total_exec_time", 0))
                    avg_ms = float(row.get("mean_exec_time", 0))
                else:
                    text = str(row.get("DIGEST_TEXT") or row.get("digest_text") or "")
                    calls = int(row.get("COUNT_STAR") or row.get("count_star") or 0)
                    total_ms = float(row.get("total_time_ms", 0))
                    avg_ms = float(row.get("avg_time_ms", 0))

                if not text.strip():
                    continue

                normalized = literal_re.sub("?", text).strip()
                query_hash = hashlib.md5(normalized.encode()).hexdigest()[:12]
                pct = round((total_ms / total_time) * 100, 1) if total_time > 0 else 0
                queries.append(
                    {
                        "query_hash": query_hash,
                        "query_text": text[:16384],
                        "normalized_query": normalized[:16384],
                        "calls": calls,
                        "total_time_ms": round(total_ms, 2),
                        "avg_time_ms": round(avg_ms, 2),
                        "pct_total_time": pct,
                    }
                )
            return queries
        except Exception:
            return []

    def _enrich_aws_storage(self, metrics, host: str, region: str | None) -> None:
        """Try to pull storage info from AWS RDS API."""
        if not host or not host.endswith(".rds.amazonaws.com"):
            return
        if not region:
            parts = host.split(".")
            for index, part in enumerate(parts):
                if part == "rds" and index >= 1:
                    region = parts[index - 1]
                    break
        if not region:
            return

        try:
            client = get_rds_client(region)
            db_identifier = host.split(".")[0]
            try:
                response = client.describe_db_instances(
                    Filters=[{"Name": "db-instance-id", "Values": [db_identifier]}]
                )
            except Exception:
                response = client.describe_db_instances()

            for instance in response.get("DBInstances", []):
                endpoint = instance.get("Endpoint") or {}
                if endpoint.get("Address") == host:
                    allocated = instance.get("AllocatedStorage")
                    if allocated:
                        metrics.storage_allocated_gb = float(allocated)
                        data_gb = metrics.database_size_mb / 1024.0
                        metrics.storage_used_pct = round((data_gb / allocated) * 100, 1)
                    metrics.storage_type = instance.get("StorageType")
                    return

            response = client.describe_db_clusters()
            for cluster in response.get("DBClusters", []):
                if cluster.get("Endpoint") == host or cluster.get("ReaderEndpoint") == host:
                    allocated = cluster.get("AllocatedStorage")
                    if allocated:
                        metrics.storage_allocated_gb = float(allocated)
                        data_gb = metrics.database_size_mb / 1024.0
                        metrics.storage_used_pct = round((data_gb / allocated) * 100, 1)
                    return
        except ImportError:
            pass
        except Exception:
            pass
