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

        # Progress messages from audit_target — collected here, yielded as status events below.
        _progress_msgs: list[str] = []
        result = self.audit_target(target_name, target_config, on_progress=lambda m: _progress_msgs.append(m))
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

    def audit_target(self, target_name: str, target_config: dict[str, Any], on_progress=None) -> AuditResult:
        """Audit a single target. Never raises.

        on_progress: optional callable(message: str) — called at each phase
        boundary so the CLI can show a spinner with elapsed time.
        """
        def _progress(msg: str):
            if on_progress:
                on_progress(msg)

        engine = target_config.get("engine", "unknown")
        host = target_config.get("host", "unknown")
        region = target_config.get("region")
        instance_class = target_config.get("instance_class")
        group = target_config.get("group")
        tags = target_config.get("tags", [])

        _progress("Collecting database metrics...")
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

        _progress("Checking cloud metrics...")
        # Pull CloudWatch CPU data if this looks like an RDS instance
        cloudwatch_cpu: dict[str, Any] | None = None
        if region and "rds.amazonaws.com" in (host or ""):
            try:
                from features.fleet.cloudwatch_metrics import (
                    collect_cloudwatch_cpu,
                    derive_rds_identifier,
                )

                rds_id = derive_rds_identifier(target_name, host)
                if not rds_id:
                    cluster_name = host.split(".")[0]
                    for suffix in ("-writer", ""):
                        candidate = f"{cluster_name}{suffix}"
                        test = collect_cloudwatch_cpu(candidate, region=region, hours=24)
                        if test:
                            rds_id = candidate
                            cloudwatch_cpu = test
                            break
                if rds_id and not cloudwatch_cpu:
                    cloudwatch_cpu = collect_cloudwatch_cpu(rds_id, region=region, hours=24)
            except Exception:
                pass

        _progress("Collecting health data...")
        # Collect §3-§6, §8 health data (Gautam v3 sections). Best-effort.
        health_report: dict[str, Any] | None = None
        try:
            from features.audit.health import collect_health_report
            from features.fleet.pricing import get_instance_info

            ram_gb: float | None = None
            vcpus: int | None = None
            if instance_class:
                info = get_instance_info(instance_class)
                if info:
                    ram_gb = float(info.get("memory_gb") or 0) or None
                    vcpus = int(info.get("vcpu") or 0) or None

            hr = collect_health_report(
                target_config, engine, instance_ram_gb=ram_gb, instance_vcpus=vcpus,
            )
            health_report = asdict(hr)
        except Exception as exc:
            health_report = {"collection_error": str(exc)}

        # Health LLM deferred — CLI runs it in parallel with duration capture.
        health_analysis: dict[str, Any] | None = None

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
            cloudwatch_cpu=cloudwatch_cpu,
            health_report=health_report,
            health_analysis=health_analysis,
        )

    @staticmethod
    def run_health_llm(audit_result_dict: dict[str, Any]) -> dict[str, Any] | None:
        """Run the health LLM analysis. Thread-safe — no shared state.

        Takes a dict (asdict of AuditResult) and returns health_analysis dict.
        Designed to run in a background thread while duration capture is in
        progress, since it only needs metrics/health data (no captured queries).
        """
        try:
            from features.audit.health_prompt import build_health_analysis_prompt
            from shared.llm_manager.llm_manager import LLMManager
            from shared.json_parse import parse_llm_json

            prompt = build_health_analysis_prompt(
                target_name=audit_result_dict.get("target_name", ""),
                engine=audit_result_dict.get("engine", ""),
                instance_class=audit_result_dict.get("instance_class"),
                health_report=audit_result_dict.get("health_report") or {},
                metrics=audit_result_dict.get("metrics") or {},
                sizing=audit_result_dict.get("sizing") or {},
                cloudwatch_cpu=audit_result_dict.get("cloudwatch_cpu"),
                top_queries=audit_result_dict.get("top_queries") or [],
            )
            llm = LLMManager()
            llm_result = llm.generate_response(prompt, max_tokens=4096, temperature=0.0)
            raw_text = llm_result.get("response", "")
            ha = parse_llm_json(raw_text) or {}
            ha["model_used"] = llm_result.get("model", "unknown")
            ha["raw_response"] = raw_text
            return ha
        except Exception as exc:
            return {"error": str(exc)}

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

                # Filter system/internal queries — these are RDST's own queries
                # or database internals, not user application queries.
                lower = text.lower()
                if any(s in lower for s in [
                    "pg_stat_", "pg_settings", "pg_indexes", "pg_catalog",
                    "pg_database_size", "pg_backend_pid", "pg_stat_statements",
                    "information_schema", "pg_replication",
                    "performance_schema", "@@",
                    "mysql.", "sys.",
                ]):
                    continue

                # Only SELECT queries belong in reports — filter SHOW, SET,
                # EXPLAIN, INSERT, UPDATE, DELETE, and other non-SELECT commands.
                first_word = lower.lstrip().split()[0] if lower.strip() else ""
                if first_word not in ("select", "with"):
                    continue

                normalized = literal_re.sub("?", text).strip()
                try:
                    from shared.query_registry import hash_sql
                    query_hash = hash_sql(text)[:12]
                except Exception:
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
