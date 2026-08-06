"""Audit service — async generator for single-target and fleet-wide audit."""

from __future__ import annotations

import datetime
import logging
from collections.abc import Awaitable, Callable
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

logger = logging.getLogger(__name__)


class AuditService:
    """Service layer for audit operations."""

    def __init__(self, config: TargetsConfig | None = None):
        self._config = config

    def _get_config(self) -> TargetsConfig:
        if self._config is None:
            self._config = TargetsConfig()
            self._config.load()
        return self._config

    @staticmethod
    def _save_report_artifact(snapshot_id: str, result: dict[str, Any]) -> None:
        """Render and persist the run's HTML report. Best-effort: a failure
        here costs the emailable artifact, never the audit."""
        try:
            from .report.artifact import save_report_locally
            from .report.report import render_v3_single_html

            save_report_locally(snapshot_id, render_v3_single_html(result))
        except Exception as exc:
            logger.warning("Could not render report for %s: %s", snapshot_id, exc)

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
            # Persist the same HTML artifact the CLI emails, so "email me this
            # report" from the web ships identical output.
            await asyncio.to_thread(self._save_report_artifact, snapshot_id, final_result)
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
        duration_seconds: int | None = None,
    ):
        """Run audits across multiple targets concurrently.

        Collection runs concurrently; per-target events stream while each
        audit and capture progresses. Unreachable targets surface as
        target_error events with the connection failure. When `insights` is
        set, fleet-level LLM insights over the successful results land in
        the complete event's summary. When `save_name` is set, successful
        results are saved as individual snapshots plus a combined
        FleetAuditSnapshot under that name. When `duration_seconds` is set,
        each successful audit runs a live workload capture (same path as
        the CLI's `fleet audit --duration`); the capture summary lands in
        the target_complete result under "workload", and the captured
        queries are benchmarked through the managed Readyset sandbox.
        All targets capture in parallel first, then Readyset benchmarks run
        sequentially through its capacity-one lease.
        """
        import asyncio

        config = self._get_config()
        total = len(targets)

        message = f"Auditing {total} targets..."
        if duration_seconds:
            message = f"Auditing {total} targets with {duration_seconds}s capture per target..."
        yield AuditStatusEvent(type="status", phase="start", message=message)

        docker_ok = False
        if duration_seconds:
            from .readyset_benchmark import docker_available
            docker_ok = docker_available()

        # A duration audit intentionally overlaps every target's independent
        # capture window. The caller's collection limit still applies to the
        # metrics-only path.
        capture_concurrency = total if duration_seconds else min(total, concurrency)
        semaphore = asyncio.Semaphore(max(1, capture_concurrency))

        async def _audit_one(
            name: str,
            emit: Callable[[AuditStatusEvent], Awaitable[None]] | None = None,
        ) -> dict[str, Any]:
            async with semaphore:
                target_config = config.get(name)
                if target_config is None:
                    return asdict(AuditResult(
                        target_name=name,
                        engine="unknown",
                        host="unknown",
                        error="Target config not found",
                    ))
                if emit:
                    await emit(AuditStatusEvent(
                        type="status",
                        phase="collect",
                        message=f"{name}: Collecting database metrics...",
                        target_name=name,
                        step="collecting",
                    ))
                result = await asyncio.to_thread(self.audit_target, name, target_config)
                result_dict = asdict(result)
                if duration_seconds and not result.error:
                    if emit:
                        await emit(AuditStatusEvent(
                            type="status",
                            phase="capture",
                            message=f"{name}: Capturing live workload for {duration_seconds}s...",
                            target_name=name,
                            elapsed_seconds=0,
                            total_seconds=float(duration_seconds),
                            step="capturing",
                        ))
                    workload = await self._capture_workload(
                        name, duration_seconds, insights=insights, audit_result=result_dict,
                        on_progress=emit,
                    )
                    if workload:
                        result_dict["workload"] = workload
                    if emit:
                        await emit(AuditStatusEvent(
                            type="status",
                            phase="capture_complete",
                            message=f"{name}: Capture complete; queued for Readyset",
                            target_name=name,
                            elapsed_seconds=float(duration_seconds),
                            total_seconds=float(duration_seconds),
                            step="queued",
                        ))
                return result_dict

        index_by_target = {name: index for index, name in enumerate(targets)}
        for index, target_name in enumerate(targets):
            yield AuditTargetStartEvent(
                type="target_start", target_name=target_name, index=index, total=total
            )

        results: list[dict[str, Any]] = []

        def _target_error_event(result: dict[str, Any]) -> AuditTargetErrorEvent:
            target_name = result.get("target_name", "")
            index = index_by_target.get(target_name, 0)
            return AuditTargetErrorEvent(
                type="target_error",
                target_name=target_name,
                error=result["error"],
                index=index,
                total=total,
            )

        def _target_complete_event(result: dict[str, Any]) -> AuditTargetCompleteEvent:
            target_name = result.get("target_name", "")
            return AuditTargetCompleteEvent(
                type="target_complete",
                target_name=target_name,
                result=result,
                index=index_by_target.get(target_name, 0),
                total=total,
            )

        async def _benchmark_events(result: dict[str, Any]):
            """Run only the best-effort Readyset phase for one captured target."""
            target_name = result.get("target_name", "")
            workload = result.get("workload") or {}
            captured = workload.get("queries") or []
            reason: str | None = None

            if not docker_ok:
                reason = "Docker is not available"
            elif workload.get("error"):
                reason = f"capture failed: {workload['error']}"
            elif not captured:
                yield AuditStatusEvent(
                    type="status",
                    phase="readyset",
                    message=(
                        f"{target_name}: Readyset benchmark skipped; "
                        "no live queries were captured"
                    ),
                    target_name=target_name,
                    step="skipped",
                )
            else:
                yield AuditStatusEvent(
                    type="status",
                    phase="readyset",
                    message=(
                        f"{target_name}: Starting Readyset to benchmark "
                        f"{len(captured)} captured queries..."
                    ),
                    target_name=target_name,
                    step="deploying",
                )
                try:
                    outcome = await self.run_readyset_comparison(
                        target_name, captured,
                    )
                except Exception as exc:
                    outcome = {"status": "error", "detail": str(exc)}
                if outcome.get("status") == "ok":
                    result["readyset_comparison"] = outcome["comparison"]
                    yield AuditStatusEvent(
                        type="status",
                        phase="readyset",
                        message=f"{target_name}: Readyset benchmark complete",
                        target_name=target_name,
                        step="done",
                    )
                else:
                    reason = outcome.get("detail") or "benchmark produced no comparison"

            if reason:
                yield AuditStatusEvent(
                    type="status",
                    phase="readyset",
                    message=f"{target_name}: Readyset benchmark could not run: {reason}",
                    target_name=target_name,
                    step="failed",
                )

            yield _target_complete_event(result)

        if duration_seconds:
            # Phase 1: every target collects and captures concurrently. A queue
            # lets lifecycle/progress events escape the worker tasks immediately
            # instead of waiting for their capture window to finish.
            event_queue: asyncio.Queue[
                tuple[str, str, AuditStatusEvent | dict[str, Any]]
            ] = asyncio.Queue()

            async def _emit_capture(event: AuditStatusEvent) -> None:
                await event_queue.put(("event", event.target_name or "", event))

            async def _capture_one(name: str) -> None:
                try:
                    result = await _audit_one(name, _emit_capture)
                except Exception as exc:
                    result = asdict(AuditResult(
                        target_name=name,
                        engine="unknown",
                        host="unknown",
                        error=str(exc),
                    ))
                await event_queue.put(("result", name, result))

            tasks = [asyncio.create_task(_capture_one(name)) for name in targets]
            results_by_target: dict[str, dict[str, Any]] = {}
            completed = 0
            try:
                while completed < total:
                    kind, name, payload = await event_queue.get()
                    if kind == "event":
                        yield payload
                        continue
                    result = payload
                    assert isinstance(result, dict)
                    results_by_target[name] = result
                    completed += 1
                    if result.get("error"):
                        yield _target_error_event(result)
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

            # Phase 2: benchmark captured targets strictly in requested order.
            # Each benchmark releases its sandbox lease before this loop advances.
            results = [results_by_target[name] for name in targets]
            for result in results:
                if result.get("error"):
                    continue
                async for event in _benchmark_events(result):
                    yield event
        else:
            tasks = [asyncio.ensure_future(_audit_one(name)) for name in targets]
            for future in asyncio.as_completed(tasks):
                result = await future
                results.append(result)
                if result.get("error"):
                    yield _target_error_event(result)
                else:
                    yield _target_complete_event(result)

        all_results = results
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

    async def _capture_workload(
        self,
        target_name: str,
        duration_seconds: int,
        *,
        insights: bool,
        audit_result: dict[str, Any],
        on_progress: Callable[[AuditStatusEvent], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        """Run a duration capture for one fleet target.

        Mirrors the CLI's per-target capture path (analysis when insights is
        on, capture not saved separately). Capture failures land in
        {"error": ...} instead of failing the audit.
        """
        from .capture_service import CaptureService

        workload: dict[str, Any] = {}
        try:
            capture = CaptureService(config=self._config)
            async for event in capture.run_capture(
                target_name=target_name,
                duration_seconds=duration_seconds,
                source="auto",
                limit=50,
                run_analysis=insights,
                cumulative_top_queries=audit_result.get("top_queries") or [],
                audit_result=audit_result,
                save_capture=False,
            ):
                if on_progress and event.type == "capture_progress":
                    await on_progress(AuditStatusEvent(
                        type="status",
                        phase="capture",
                        message=f"{target_name}: Capturing live workload...",
                        target_name=target_name,
                        elapsed_seconds=event.elapsed_seconds,
                        total_seconds=event.total_seconds,
                        step="capturing",
                    ))
                elif on_progress and (
                    event.type == "analysis_progress"
                    or (event.type == "status" and event.phase == "analysis")
                ):
                    await on_progress(AuditStatusEvent(
                        type="status",
                        phase="analysis",
                        message=f"{target_name}: {event.message}",
                        target_name=target_name,
                        step="analyzing",
                    ))
                if event.type == "error":
                    workload.setdefault("error", event.message)
                elif event.type == "complete":
                    workload = event.summary or {}
                    if event.analysis:
                        workload["analysis"] = event.analysis
        except Exception as exc:
            workload = {"error": str(exc)}
        return workload

    @staticmethod
    async def run_readyset_comparison(
        target_name: str, queries: list[dict[str, Any]], max_queries: int = 20,
    ) -> dict[str, Any]:
        """Benchmark captured queries through the managed Readyset sandbox."""
        from .readyset_benchmark import build_comparison, run_managed_benchmark

        outcome = await run_managed_benchmark(
            target_name, queries, max_queries=max_queries
        )
        if outcome.get("status") != "ok":
            return {
                "status": "error",
                "detail": outcome.get("detail") or "benchmark produced no results",
            }
        return {"status": "ok", "comparison": build_comparison(outcome["results"])}

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
        successful = [result for result in all_results if not result.get("error")]
        monthly_costs = [
            sizing["current_monthly_cost_usd"]
            for result in successful
            if (sizing := result.get("sizing"))
            and sizing.get("current_monthly_cost_usd") is not None
        ]
        potential_savings = [
            sizing["potential_savings_usd"]
            for result in successful
            if (sizing := result.get("sizing"))
            and sizing.get("potential_savings_usd") is not None
        ]
        cache_scores = [
            cache["score"]
            for result in successful
            if (cache := result.get("cache_opportunity"))
            and cache.get("score") is not None
        ]
        snapshot.total_monthly_cost_usd = sum(monthly_costs) if monthly_costs else None
        snapshot.potential_savings_usd = (
            sum(potential_savings) if potential_savings else None
        )
        snapshot.avg_cache_opportunity = (
            sum(cache_scores) / len(cache_scores) if cache_scores else None
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
            metrics = collect_metrics(target_config, target=target_name)
        except Exception as exc:
            return AuditResult(
                target_name=target_name,
                engine=engine,
                host=host,
                error=str(exc),
            )

        if not region:
            region = detect_region_from_hostname(host)
        # Only infer an instance class from shared_buffers for real RDS
        # endpoints — the heuristic mislabels local/self-hosted databases.
        instance_class_source = "aws" if instance_class else None
        if (
            not instance_class
            and metrics.shared_buffers_mb > 0
            and (host or "").endswith(".rds.amazonaws.com")
        ):
            instance_class = estimate_class_from_shared_buffers(metrics.shared_buffers_mb)
            if instance_class:
                instance_class_source = "estimated"

        sizing = compute_sizing_verdict(metrics, instance_class=instance_class)
        cache_opportunity = compute_cache_opportunity(metrics)
        top_queries = self._collect_top_queries(
            target_name,
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
            instance_class_source=instance_class_source,
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
        target_name: str,
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

            connection = create_direct_connection(
                target_config,
                target=target_name,
            )
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
                    query_hash = hashlib.md5(normalized.encode(), usedforsecurity=False).hexdigest()[:12]
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
