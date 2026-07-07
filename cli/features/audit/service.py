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
    AuditSnapshotSavedEvent,
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

    async def audit_single(
        self, target_name: str, *, insights: bool = False, save: bool = True
    ):
        """Run a deep audit on a single target without blocking the event loop.

        The blocking collection work runs in a thread; its progress messages
        stream out as status events while it runs. When `insights` is set,
        the health LLM analysis runs after collection and is merged into the
        result. When `save` is set, the merged result is persisted to the
        snapshot store so `audit list` / `audit show` (and the API run
        history) can find it.
        """
        import asyncio

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

        # audit_target appends progress messages from its thread; list append
        # and len are atomic under the GIL, so polling from here is safe.
        progress_msgs: list[str] = []
        task = asyncio.ensure_future(
            asyncio.to_thread(
                self.audit_target, target_name, target_config,
                on_progress=progress_msgs.append,
            )
        )
        seen = 0
        done = False
        while not done:
            # Check done before draining so messages appended between the
            # drain and the check still get flushed on the final pass.
            done = task.done()
            while seen < len(progress_msgs):
                yield AuditStatusEvent(type="status", phase="collect", message=progress_msgs[seen])
                seen += 1
            if not done:
                await asyncio.sleep(0.2)
        result = await task

        if result.error:
            yield AuditTargetErrorEvent(
                type="target_error",
                target_name=target_name,
                error=result.error,
                index=0,
                total=1,
            )
            yield AuditCompleteEvent(type="complete", success=False)
            return

        yield AuditMetricsCollectedEvent(
            type="metrics_collected",
            target_name=target_name,
            metrics=asdict(result.metrics) if result.metrics else {},
        )

        final_result = asdict(result)
        if insights:
            yield AuditStatusEvent(type="status", phase="insights", message="Analyzing health data...")
            health_analysis = await asyncio.to_thread(self.run_health_llm, final_result)
            if health_analysis:
                final_result["health_analysis"] = health_analysis

        snapshot_id: str | None = None
        if save:
            from features.fleet import SnapshotStore

            snapshot_id = f"audit_{target_name}_{datetime.datetime.now():%Y%m%d_%H%M%S}"
            path = await asyncio.to_thread(SnapshotStore().save_raw, snapshot_id, final_result)
            yield AuditSnapshotSavedEvent(
                type="snapshot_saved", snapshot_id=snapshot_id, path=path,
            )

        yield AuditTargetCompleteEvent(
            type="target_complete",
            target_name=target_name,
            result=final_result,
            index=0,
            total=1,
        )
        yield AuditCompleteEvent(type="complete", success=True, snapshot_id=snapshot_id)

    async def audit_fleet(
        self,
        targets: list[str],
        *,
        concurrency: int = 10,
        insights: bool = False,
        save_name: str | None = None,
    ):
        """Run audits across multiple targets concurrently.

        Collection runs in a bounded thread pool; per-target events stream
        out as each audit finishes. Unreachable targets surface as
        target_error events with the connection failure. When `insights` is
        set, fleet-level LLM insights over the successful results land in
        the complete event's summary. When `save_name` is set, successful
        results are saved as individual snapshots plus a combined
        FleetAuditSnapshot under that name.
        """
        import asyncio

        config = self._get_config()
        total = len(targets)

        yield AuditStatusEvent(type="status", phase="start", message=f"Auditing {total} targets...")

        semaphore = asyncio.Semaphore(max(1, min(total, concurrency)))

        async def _audit_one(name: str) -> AuditResult:
            async with semaphore:
                target_config = config.get(name)
                if target_config is None:
                    return AuditResult(
                        target_name=name,
                        engine="unknown",
                        host="unknown",
                        error="Target config not found",
                    )
                return await asyncio.to_thread(self.audit_target, name, target_config)

        index_by_target = {name: index for index, name in enumerate(targets)}
        for index, target_name in enumerate(targets):
            yield AuditTargetStartEvent(
                type="target_start", target_name=target_name, index=index, total=total
            )

        tasks = [asyncio.ensure_future(_audit_one(name)) for name in targets]
        results: list[AuditResult] = []
        for future in asyncio.as_completed(tasks):
            result = await future
            results.append(result)
            # Events stream in completion order; index matches the target's
            # position from its target_start event so clients can correlate.
            index = index_by_target.get(result.target_name, 0)
            if result.error:
                yield AuditTargetErrorEvent(
                    type="target_error",
                    target_name=result.target_name,
                    error=result.error,
                    index=index,
                    total=total,
                )
            else:
                yield AuditTargetCompleteEvent(
                    type="target_complete",
                    target_name=result.target_name,
                    result=asdict(result),
                    index=index,
                    total=total,
                )

        all_results = [asdict(result) for result in results]
        successful = [r for r in all_results if not r.get("error")]
        failures = total - len(successful)

        fleet_insights: dict[str, Any] | None = None
        if insights and successful:
            yield AuditStatusEvent(
                type="status", phase="insights", message="Generating fleet insights...",
            )
            fleet_insights = await asyncio.to_thread(self.run_fleet_insights, successful)

        snapshot_id: str | None = None
        if save_name:
            path = await asyncio.to_thread(
                self._save_fleet_snapshot, save_name, all_results, failures, fleet_insights,
            )
            snapshot_id = save_name
            yield AuditSnapshotSavedEvent(
                type="snapshot_saved", snapshot_id=save_name, path=path,
            )

        summary: dict[str, Any] = {
            "targets_audited": total,
            "successes": len(successful),
            "failures": failures,
        }
        if fleet_insights:
            summary["fleet_insights"] = fleet_insights
        yield AuditCompleteEvent(
            type="complete",
            success=len(successful) > 0,
            snapshot_id=snapshot_id,
            summary=summary,
        )

    @staticmethod
    def run_fleet_insights(successful_results: list[dict[str, Any]]) -> dict[str, Any]:
        """Run the fleet-level insights LLM over successful audit results.

        Returns the parsed insights dict, {"raw": text} when the response
        is not valid JSON, or {"error": ...} when the call fails.
        """
        try:
            from features.fleet import build_fleet_insights_prompt
            from shared.llm_manager import LLMManager
            from shared.json_parse import parse_llm_json

            prompt = build_fleet_insights_prompt(successful_results)
            llm = LLMManager()
            result = llm.generate_response(prompt, max_tokens=4096, temperature=0.0)
            raw = result.get("response", "")
            try:
                return parse_llm_json(raw) or {"raw": raw}
            except Exception:
                return {"raw": raw}
        except Exception as exc:
            return {"error": str(exc)}

    @staticmethod
    def _save_fleet_snapshot(
        save_name: str,
        all_results: list[dict[str, Any]],
        failures: int,
        fleet_insights: dict[str, Any] | None,
    ) -> str:
        """Save per-target snapshots for successful results plus the
        combined fleet snapshot. Returns the combined snapshot's path."""
        from features.fleet import FleetAuditSnapshot, SnapshotStore

        store = SnapshotStore()
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        for result in all_results:
            if result.get("error"):
                continue
            target_name = result.get("target_name", "unknown")
            store.save_raw(f"audit_{target_name}_{timestamp}", result)

        snapshot = FleetAuditSnapshot(
            snapshot_id=save_name,
            name=save_name,
            created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            targets_audited=len(all_results),
            targets_failed=failures,
            results=all_results,
        )
        if fleet_insights:
            snapshot.fleet_insights = fleet_insights
        return store.save_raw(save_name, asdict(snapshot))

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
