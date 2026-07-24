"""Capture service — async generator for workload capture and analysis."""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import logging
import re
import time
from dataclasses import asdict
from typing import Any

from shared.config.targets import TargetsConfig

from .events import (
    WorkloadAnalysisProgressEvent,
    WorkloadCaptureCompleteEvent,
    WorkloadCaptureProgressEvent,
    WorkloadCompleteEvent,
    WorkloadConnectedEvent,
    WorkloadErrorEvent,
    WorkloadEvent,
    WorkloadQueriesSavedEvent,
    WorkloadSnapshotEvent,
    WorkloadStatusEvent,
)
from .index_dedupe import filter_existing_indexes, normalize_index_rec_sql
from .models import WorkloadAnalysis, WorkloadQuery, WorkloadRun
from .prompts import build_audit_capture_prompt
from .storage import AuditStorage
from . import query_stats as query_stats_module

logger = logging.getLogger(__name__)
_LITERAL_RE = re.compile(r"'[^']*'|\b\d+\.?\d*\b")

# Keep capture reports aligned with the full metrics audit produced by the
# CLI. These fields are added to both the persisted WorkloadRun JSON and the
# complete event summary. Workload-only fields remain owned by WorkloadRun.
_AUDIT_CAPTURE_FIELDS = (
    "target_name",
    "engine",
    "host",
    "region",
    "instance_class",
    "instance_class_source",
    "group",
    "tags",
    "metrics",
    "sizing",
    "cache_opportunity",
    "top_queries",
    "audited_at",
    "cloudwatch_cpu",
    "health_report",
    "health_analysis",
)


def _normalize(sql: str) -> str:
    """Normalize for display/comparison. For hashing, use the registry's
    normalize_sql + hash_sql so hashes match the query registry."""
    return _LITERAL_RE.sub("?", sql).strip()


def _hash_sql(sql_text: str) -> str:
    """Hash using the registry's normalizer so the hash matches what
    add_query() produces. This means `rdst analyze --hash <X>` works
    directly without tag fallbacks."""
    try:
        from shared.query_registry import hash_sql
        return hash_sql(sql_text)[:12]
    except Exception:
        # Fallback if registry import fails
        normalized = _LITERAL_RE.sub("?", sql_text).strip()
        return hashlib.md5(normalized.encode()).hexdigest()[:12]


def _parse_duration(duration_str: str) -> int:
    duration = duration_str.strip().lower()
    if duration.endswith("h"):
        return int(float(duration[:-1]) * 3600)
    if duration.endswith("m"):
        return int(float(duration[:-1]) * 60)
    if duration.endswith("s"):
        return int(float(duration[:-1]))
    return int(duration)


class CaptureService:
    """Service layer for audit capture and analysis."""

    def __init__(self, config: TargetsConfig | None = None):
        self._config = config
        self._stop_requested = False

    def _get_config(self) -> TargetsConfig:
        if self._config is None:
            self._config = TargetsConfig()
            self._config.load()
        return self._config

    def request_stop(self) -> None:
        self._stop_requested = True

    async def _collect_metrics_audit(
        self,
        target_name: str,
        target_config: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Collect the full audit payload for API captures, best-effort.

        CLI and fleet captures pass an already-collected ``audit_result`` to
        ``run_capture``. The web capture endpoint does not, so it reaches this
        fallback. A metrics failure must never prevent workload capture.
        """
        try:
            from .service import AuditService

            result = await asyncio.to_thread(
                AuditService(config=self._get_config()).audit_target,
                target_name,
                target_config,
            )
            if result.error:
                logger.warning("Metrics audit failed during capture: %s", result.error)
                return None
            return asdict(result)
        except Exception as exc:
            logger.warning("Metrics audit failed during capture: %s", exc)
            return None

    @staticmethod
    def _audit_capture_payload(audit_result: dict[str, Any] | None) -> dict[str, Any]:
        if not audit_result or audit_result.get("error"):
            return {}
        return {
            key: audit_result[key]
            for key in _AUDIT_CAPTURE_FIELDS
            if key in audit_result
        }

    @staticmethod
    def check_query_stats(connection, db_engine: str) -> dict[str, Any]:
        """Check that query tracking is available on an open connection.

        pg_stat_statements (PG) or performance_schema (MySQL) must be ON for
        capture to produce query data. Returns
        {"status": "ok"|"missing"|"error", "detail": str, "remediation": str|None}.
        """
        cursor = None
        try:
            cursor = connection.cursor()
            if db_engine == "postgresql":
                cursor.execute("SELECT 1 FROM pg_stat_statements LIMIT 1")
            elif db_engine == "mysql":
                cursor.execute("SHOW VARIABLES LIKE 'performance_schema'")
                row = cursor.fetchone()
                # pymysql returns tuple or dict depending on cursor type
                value = row.get("Value", "") if isinstance(row, dict) else (row[1] if row else "")
                if str(value).upper() != "ON":
                    return {
                        "status": "missing",
                        "detail": "MySQL performance_schema is OFF. Query capture requires it to be enabled.",
                        "remediation": (
                            "Fix: create a custom RDS parameter group with performance_schema=ON, "
                            "attach it to the instance, and reboot.\n"
                            "For non-RDS: add performance_schema=ON to my.cnf and restart MySQL."
                        ),
                    }
            return {
                "status": "ok",
                "detail": (
                    "pg_stat_statements is available"
                    if db_engine == "postgresql"
                    else "performance_schema is ON"
                ),
                "remediation": None,
            }
        except Exception as exc:
            err = str(exc)
            if db_engine == "postgresql" and "pg_stat_statements" in err.lower():
                return {
                    "status": "missing",
                    "detail": "pg_stat_statements extension is not installed. Query capture requires it.",
                    "remediation": (
                        "Fix: CREATE EXTENSION pg_stat_statements;\n"
                        "For RDS: add pg_stat_statements to shared_preload_libraries in the parameter group."
                    ),
                }
            return {"status": "error", "detail": err, "remediation": None}
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:
                    pass

    async def run_capture(
        self,
        target_name: str,
        duration_seconds: int | None = None,
        snapshot_only: bool = False,
        source: str = "auto",
        limit: int = 50,
        run_analysis: bool = True,
        model: str | None = None,
        save_top_queries: int | None = None,
        queries_input: str | None = None,
        concurrency: int = 4,
        delay: float = 0.1,
        cumulative_top_queries: list | None = None,
        audit_result: dict | None = None,
        collect_metrics_audit: bool = False,
        save_capture: bool = True,
        readyset_testing: bool = False,
    ):
        """Capture a workload from a database target.

        When `readyset_testing` is set, captured queries are benchmarked
        through the managed Readyset sandbox after analysis; the comparison
        lands in the complete event's summary under "readyset_comparison".
        """
        from shared.db_connection import create_direct_connection

        config = self._get_config()
        target_config = config.get(target_name)
        if target_config is None:
            yield WorkloadErrorEvent(
                type="error", message=f"Target '{target_name}' not found", phase="config"
            )
            return

        db_engine = target_config.get("engine", "postgresql")
        storage = AuditStorage()
        run_id = storage.generate_run_id(target_name)

        if audit_result is None and collect_metrics_audit:
            yield WorkloadStatusEvent(
                type="status",
                phase="audit",
                message="Collecting health and sizing metrics...",
            )
            audit_result = await self._collect_metrics_audit(target_name, target_config)
        audit_payload = self._audit_capture_payload(audit_result)

        yield WorkloadStatusEvent(
            type="status", phase="config", message=f"Connecting to {target_name}..."
        )
        try:
            connection = create_direct_connection(target_config)
        except Exception as exc:
            yield WorkloadErrorEvent(type="error", message=str(exc), phase="connect")
            return

        # Preflight: verify query tracking is enabled before spending time on
        # capture. "error" results (unrelated failures) let the capture proceed
        # and get handled downstream; only a definitive "missing" aborts.
        if duration_seconds and not snapshot_only:
            preflight = self.check_query_stats(connection, db_engine)
            if preflight["status"] == "missing":
                yield WorkloadErrorEvent(
                    type="error",
                    message=f"{preflight['detail']}\n{preflight['remediation']}",
                    phase="preflight",
                )
                connection.close()
                return

        yield WorkloadConnectedEvent(
            type="connected",
            target_name=target_name,
            db_engine=db_engine,
            source="snapshot" if snapshot_only else "live",
        )

        replay_threads: list[Any] = []
        try:
            has_query_stats = False
            try:
                test_cursor = connection.cursor()
                if db_engine == "postgresql":
                    test_cursor.execute("SELECT count(*) FROM pg_stat_statements LIMIT 1")
                    test_cursor.fetchone()
                    has_query_stats = True
                else:
                    test_cursor.execute(
                        "SELECT count(*) FROM performance_schema.events_statements_summary_by_digest LIMIT 1"
                    )
                    row = test_cursor.fetchone()
                    value = list(row.values())[0] if isinstance(row, dict) else row[0]
                    has_query_stats = int(value) > 0
                test_cursor.close()
            except Exception:
                pass

            if not has_query_stats:
                extension = "pg_stat_statements" if db_engine == "postgresql" else "performance_schema"
                yield WorkloadStatusEvent(
                    type="status",
                    phase="warning",
                    message=(
                        f"WARNING: {extension} is not enabled. Workload capture will have no query "
                        f"data — only database-level health metrics. Enable {extension} for meaningful results."
                    ),
                )

            yield WorkloadStatusEvent(
                type="status", phase="snapshot_start", message="Capturing start snapshot..."
            )
            snapshot_start = query_stats_module.collect_database_snapshot(connection, db_engine)
            table_stats_start = query_stats_module.collect_table_stats(connection, db_engine)

            yield WorkloadSnapshotEvent(
                type="snapshot",
                when="start",
                cache_hit_ratio=snapshot_start.cache_hit_ratio,
                active_connections=snapshot_start.active_connections or 0,
            )

            if queries_input:
                replay_queries, load_error = self._load_replay_queries(queries_input, target_name)
                if load_error:
                    yield WorkloadErrorEvent(type="error", message=load_error, phase="config")
                    connection.close()
                    return
                self._stop_requested = False
                replay_threads = self._start_replay_workers(
                    target_config, replay_queries, concurrency, delay
                )
                yield WorkloadStatusEvent(
                    type="status",
                    phase="replay",
                    message=f"Replaying {len(replay_queries)} queries with {concurrency} threads",
                )

            started_at = datetime.datetime.now(datetime.timezone.utc)
            intermediate_snapshots = []

            if snapshot_only:
                yield WorkloadStatusEvent(
                    type="status", phase="capture", message="Reading query statistics..."
                )
                raw_queries = (
                    query_stats_module.collect_pg_stat_statements(connection)
                    if db_engine == "postgresql"
                    else query_stats_module.collect_mysql_digest_stats(connection)
                )
                queries = self._convert_raw_queries(raw_queries, db_engine, limit)
                actual_duration = 0
            else:
                if duration_seconds:
                    yield WorkloadStatusEvent(
                        type="status",
                        phase="capture",
                        message=f"Capturing queries for {duration_seconds}s... (Ctrl+C to stop early)",
                    )
                else:
                    yield WorkloadStatusEvent(
                        type="status",
                        phase="capture",
                        message="Capturing queries... (Ctrl+C to stop)",
                    )

                baseline_queries = (
                    query_stats_module.collect_pg_stat_statements(connection)
                    if db_engine == "postgresql"
                    else query_stats_module.collect_mysql_digest_stats(connection)
                )

                capture_start = time.monotonic()
                last_intermediate = capture_start
                self._stop_requested = False

                while True:
                    elapsed = time.monotonic() - capture_start
                    if duration_seconds and elapsed >= duration_seconds:
                        break
                    if self._stop_requested:
                        break
                    if time.monotonic() - last_intermediate >= 30:
                        intermediate = query_stats_module.collect_intermediate_snapshot(
                            connection, db_engine, elapsed, snapshot_start
                        )
                        intermediate_snapshots.append(intermediate)
                        last_intermediate = time.monotonic()
                        yield WorkloadCaptureProgressEvent(
                            type="capture_progress",
                            elapsed_seconds=round(elapsed, 1),
                            total_seconds=float(duration_seconds) if duration_seconds else None,
                            cache_hit_ratio=intermediate.cache_hit_ratio,
                            active_connections=intermediate.active_connections,
                            tps=intermediate.transactions_per_sec,
                        )
                    await asyncio.sleep(2)

                actual_duration = int(time.monotonic() - capture_start)
                end_queries = (
                    query_stats_module.collect_pg_stat_statements(connection)
                    if db_engine == "postgresql"
                    else query_stats_module.collect_mysql_digest_stats(connection)
                )
                queries = self._compute_query_delta(
                    baseline_queries, end_queries, db_engine, limit
                )

            ended_at = datetime.datetime.now(datetime.timezone.utc)
            total_query_time = sum(query.total_time_ms for query in queries)
            total_executions = sum(query.calls for query in queries)

            yield WorkloadCaptureCompleteEvent(
                type="capture_complete",
                unique_queries=len(queries),
                total_executions=total_executions,
                total_query_time_ms=total_query_time,
                duration_seconds=float(actual_duration),
            )

            if replay_threads:
                self._stop_replay_workers(replay_threads)
                replay_threads = []

            yield WorkloadStatusEvent(
                type="status", phase="snapshot_end", message="Capturing end snapshot..."
            )
            snapshot_end = query_stats_module.collect_database_snapshot(connection, db_engine)
            table_stats_end = query_stats_module.collect_table_stats(connection, db_engine)

            yield WorkloadSnapshotEvent(
                type="snapshot",
                when="end",
                cache_hit_ratio=snapshot_end.cache_hit_ratio,
                active_connections=snapshot_end.active_connections or 0,
            )

            delta_stats = query_stats_module.compute_snapshot_delta(snapshot_start, snapshot_end)
            run = WorkloadRun(
                run_id=run_id,
                target_name=target_name,
                db_engine=db_engine,
                started_at=started_at.isoformat(),
                ended_at=ended_at.isoformat(),
                duration_seconds=actual_duration if not snapshot_only else 0,
                source="snapshot" if snapshot_only else ("replay" if queries_input else "live"),
                snapshot_start=snapshot_start,
                snapshot_end=snapshot_end,
                intermediate_snapshots=intermediate_snapshots,
                delta_stats=delta_stats,
                table_stats_start=table_stats_start,
                table_stats_end=table_stats_end,
                queries=queries,
                total_queries=total_executions,
                total_query_time_ms=total_query_time,
            )

            analysis_dict = None
            if run_analysis and queries:
                schema_context = ""
                try:
                    yield WorkloadStatusEvent(type="status", phase="schema", message="Collecting schema context...")
                    all_query_dicts = [{"normalized_query": q.normalized_query} for q in queries]
                    if cumulative_top_queries:
                        all_query_dicts.extend(cumulative_top_queries)
                    table_names = query_stats_module._extract_table_names_from_queries(all_query_dicts)
                    if table_names:
                        all_query_texts = [q.normalized_query or "" for q in queries]
                        if cumulative_top_queries:
                            all_query_texts.extend(
                                q.get("normalized_query") or q.get("query") or ""
                                for q in cumulative_top_queries
                            )
                        schema_context = await asyncio.to_thread(
                            query_stats_module.collect_schema_for_tables,
                            connection, table_names, db_engine, query_texts=all_query_texts,
                        )
                        if schema_context:
                            logger.debug("Collected schema for %d tables", len(table_names))
                except Exception as exc:
                    logger.debug("Schema collection skipped: %s", exc)

                yield WorkloadStatusEvent(type="status", phase="analysis", message="Running final analysis...")
                try:
                    from shared.llm_manager import LLMManager
                    from shared.json_parse import parse_llm_json

                    run_dict = asdict(run)
                    prompt = build_audit_capture_prompt(
                        run_dict,
                        limit=min(limit, 30),
                        cumulative_top_queries=cumulative_top_queries,
                        audit_result=audit_result,
                        schema_context=schema_context,
                    )
                    llm = LLMManager()
                    llm_result = await asyncio.to_thread(
                        llm.generate_response, prompt, max_tokens=6144, temperature=0.0,
                    )
                    raw_text = llm_result.get("response", "")
                    analysis_dict = parse_llm_json(raw_text)
                    used_model = llm_result.get("model", "unknown")
                    analysis_dict["model_used"] = used_model
                    analysis_dict["raw_response"] = raw_text
                    if schema_context:
                        analysis_dict["schema_context"] = schema_context
                    deduped_index_recs = normalize_index_rec_sql(
                        filter_existing_indexes(
                            analysis_dict.get("index_recommendations") or [],
                            schema_context or "",
                        )
                    )
                    analysis_dict["index_recommendations"] = deduped_index_recs
                    run.analysis = WorkloadAnalysis(
                        model_used=used_model,
                        workload_characterization=analysis_dict.get("workload_characterization", ""),
                        health_score=analysis_dict.get("health_score", 0),
                        read_write_ratio=analysis_dict.get("read_write_ratio", ""),
                        top_bottlenecks=analysis_dict.get("top_bottlenecks", []),
                        index_recommendations=deduped_index_recs,
                        caching_candidates=analysis_dict.get("caching_candidates", []),
                        capacity_insights=analysis_dict.get("capacity_insights", []),
                        optimization_priorities=analysis_dict.get("optimization_priorities", []),
                        raw_response=raw_text,
                        schema_context=schema_context,
                    )
                    yield WorkloadAnalysisProgressEvent(
                        type="analysis_progress", message="Analysis complete", percent=100
                    )
                except Exception as exc:
                    logger.warning("Analysis failed: %s", exc)
                    yield WorkloadStatusEvent(
                        type="status",
                        phase="analysis",
                        message=f"Analysis failed: {exc}",
                    )

            readyset_comparison = None
            if readyset_testing and queries and not snapshot_only:
                from .readyset_benchmark import (
                    build_comparison,
                    docker_available,
                    run_managed_benchmark,
                )

                if not docker_available():
                    yield WorkloadStatusEvent(
                        type="status",
                        phase="readyset",
                        message="Docker is not available — skipped Readyset cache benchmark",
                    )
                else:
                    yield WorkloadStatusEvent(
                        type="status",
                        phase="readyset",
                        message=f"Benchmarking {len(queries)} captured queries against Readyset...",
                    )
                    outcome = await run_managed_benchmark(
                        target_name,
                        [asdict(query) for query in queries],
                    )
                    if outcome.get("status") == "ok":
                        readyset_comparison = build_comparison(outcome["results"])
                        run.readyset_comparison = readyset_comparison
                    else:
                        yield WorkloadStatusEvent(
                            type="status",
                            phase="readyset",
                            message=f"Readyset benchmark skipped: {outcome.get('detail')}",
                        )

            number_to_save = save_top_queries if save_top_queries is not None else len(queries)
            saved_hashes: list[str] = []
            if number_to_save > 0 and queries:
                try:
                    from shared.query_registry import QueryRegistry

                    registry = QueryRegistry()
                    registry.load()
                    for query in queries[:number_to_save]:
                        try:
                            # Normalize MySQL DIGEST_TEXT spacing before saving.
                            save_sql = query.query_text
                            if db_engine == "mysql":
                                save_sql = re.sub(r'(\w)\s+\(', r'\1(', save_sql)
                                save_sql = re.sub(r'`\s+\.\s+`', '`.`', save_sql)

                            # Save and get the REGISTRY hash back — this is
                            # the single source of truth. Update the query's
                            # hash to match so breadcrumbs resolve directly.
                            reg_hash, _ = registry.add_query(
                                sql=save_sql,
                                target=target_name,
                                skip_param_extraction=True,
                            )
                            query.query_hash = reg_hash
                            saved_hashes.append(reg_hash)
                        except Exception:
                            continue
                    if saved_hashes:
                        registry.save()
                        yield WorkloadQueriesSavedEvent(
                            type="queries_saved", count=len(saved_hashes), hashes=saved_hashes
                        )
                except Exception as exc:
                    logger.warning("Failed to save queries to registry: %s", exc)

            path = ""
            if save_capture:
                yield WorkloadStatusEvent(type="status", phase="storage", message="Saving audit capture...")
                path = storage.save_run(run, extra=audit_payload)

            query_dicts = [asdict(query) for query in queries] if queries else []
            summary = {
                "run_id": run_id,
                "target": target_name,
                "duration_seconds": actual_duration if not snapshot_only else 0,
                "unique_queries": len(queries),
                "total_executions": total_executions,
                "total_query_time_ms": round(total_query_time, 1),
                "path": path,
                "has_analysis": analysis_dict is not None,
                "queries": query_dicts,
            }
            if readyset_comparison:
                summary["readyset_comparison"] = readyset_comparison
            summary.update(audit_payload)
            yield WorkloadCompleteEvent(
                type="complete",
                success=True,
                run_id=run_id,
                summary=summary,
                analysis=analysis_dict,
            )
        except Exception as exc:
            if replay_threads:
                self._stop_replay_workers(replay_threads)
            yield WorkloadErrorEvent(type="error", message=str(exc), phase="capture")
        finally:
            if replay_threads:
                self._stop_replay_workers(replay_threads)
            try:
                connection.close()
            except Exception:
                pass

    def _load_replay_queries(self, queries_input: str, target_name: str):
        """Load queries from a file path or comma-separated registry hashes."""
        import csv
        from pathlib import Path

        queries: list[str] = []
        path = Path(queries_input)
        if path.suffix in (".csv", ".txt", ".sql") or "/" in queries_input or "\\" in queries_input:
            if not path.exists():
                return [], f"Queries file not found: {queries_input}"
            if not path.is_file():
                return [], f"Not a file: {queries_input}"
            if path.stat().st_size == 0:
                return [], f"Queries file is empty: {queries_input}"

            with open(path, "r", newline="", encoding="utf-8-sig") as file_obj:
                reader = csv.DictReader(file_obj)
                if reader.fieldnames and "query" in [header.lower().strip() for header in reader.fieldnames]:
                    for row in reader:
                        sql = (
                            row.get("query")
                            or row.get("Query")
                            or row.get("sql")
                            or row.get("SQL")
                            or ""
                        ).strip()
                        if sql:
                            queries.append(sql)
                else:
                    file_obj.seek(0)
                    for line in file_obj:
                        sql = line.strip()
                        if sql and not sql.startswith("#"):
                            queries.append(sql)

            if not queries:
                return [], (
                    f"No valid queries found in {queries_input}. Expected CSV with 'query' column "
                    f"header, or one SQL query per line."
                )

            valid = [query for query in queries if query.split()[0].lower() in {"select", "with", "explain"}]
            if not valid:
                return [], (
                    f"No valid SQL queries found in {queries_input}. Queries must start with SELECT, "
                    f"WITH, or EXPLAIN (read-only)."
                )
            return valid, None

        hashes = [value.strip() for value in queries_input.split(",") if value.strip()]
        if hashes:
            try:
                from shared.query_registry import QueryRegistry

                registry = QueryRegistry()
                registry.load()
                not_found = []
                for query_hash in hashes:
                    entry = registry.get_query(query_hash)
                    if entry:
                        sql = entry.get("sql") or entry.get("query", "")
                        if sql:
                            queries.append(sql)
                    else:
                        not_found.append(query_hash)
                if not_found:
                    logger.warning("Query hashes not found in registry: %s", ", ".join(not_found))
                if not queries:
                    return [], f"No queries found for hashes: {', '.join(hashes)}. Check rdst query list."
            except Exception as exc:
                return [], f"Failed to load queries from registry: {exc}"

        if not queries:
            return [], f"Could not load queries from: {queries_input}"
        return queries, None

    def _start_replay_workers(
        self,
        target_config: dict[str, Any],
        queries: list[str],
        concurrency: int,
        delay: float,
    ) -> list[Any]:
        """Start replay worker threads."""
        import threading
        from shared.db_connection import create_direct_connection

        threads: list[Any] = []

        def _worker(worker_id: int) -> None:
            try:
                connection = create_direct_connection(target_config)
                cursor = connection.cursor()
                index = 0
                while not self._stop_requested:
                    sql = queries[index % len(queries)]
                    try:
                        cursor.execute(sql)
                        cursor.fetchall()
                    except Exception:
                        pass
                    index += 1
                    time.sleep(delay + (hash(worker_id + index) % 100) / 1000.0)
                cursor.close()
                connection.close()
            except Exception:
                pass

        for index in range(concurrency):
            thread = threading.Thread(target=_worker, args=(index,), daemon=True)
            thread.start()
            threads.append(thread)
        return threads

    def _stop_replay_workers(self, threads: list[Any]) -> None:
        self._stop_requested = True
        for thread in threads:
            thread.join(timeout=5)

    def _convert_raw_queries(self, raw: list[dict], db_engine: str, limit: int) -> list[WorkloadQuery]:
        """Convert raw pg_stat_statements / digest rows to WorkloadQuery objects."""
        queries = []
        total_time = sum(
            float(row.get("total_exec_time") or row.get("total_time_ms") or 0)
            for row in raw
        )

        for row in raw[:limit]:
            if db_engine == "postgresql":
                text = str(row.get("query", ""))
                calls = int(row.get("calls", 0))
                total_ms = float(row.get("total_exec_time", 0))
                avg_ms = float(row.get("mean_exec_time", 0))
                max_ms = float(row.get("max_exec_time", 0))
                blocks_hit = int(row.get("shared_blks_hit", 0))
                blocks_read = int(row.get("shared_blks_read", 0))
                rows = int(row.get("rows", 0))
            else:
                text = str(row.get("DIGEST_TEXT") or row.get("digest_text") or "")
                calls = int(row.get("COUNT_STAR") or row.get("count_star") or 0)
                total_ms = float(row.get("total_time_ms", 0))
                avg_ms = float(row.get("avg_time_ms", 0))
                max_ms = float(row.get("max_time_ms", 0))
                blocks_hit = None
                blocks_read = None
                rows = int(row.get("rows_sent") or row.get("SUM_ROWS_SENT") or 0)

            # Filter system/internal queries at the source — these are RDST's
            # own monitoring queries or database internals, not user queries.
            lower = text.lower()
            if any(s in lower for s in [
                "pg_stat_", "pg_settings", "pg_indexes", "pg_catalog",
                "pg_database_size", "pg_backend_pid", "pg_stat_statements",
                "information_schema", "pg_replication",
                "performance_schema", "@@",
                "mysql.", "sys.",
            ]):
                continue

            # Only SELECT/WITH queries belong in reports
            first_word = lower.lstrip().split()[0] if lower.strip() else ""
            if first_word not in ("select", "with"):
                continue

            normalized = _normalize(text)
            query_hash = _hash_sql(text)  # hash from raw text, matches registry
            pct = round((total_ms / total_time) * 100, 1) if total_time > 0 else 0

            queries.append(
                WorkloadQuery(
                    query_hash=query_hash,
                    query_text=text[:16384],
                    normalized_query=normalized[:16384],
                    calls=calls,
                    total_time_ms=round(total_ms, 2),
                    avg_time_ms=round(avg_ms, 2),
                    max_time_ms=round(max_ms, 2),
                    rows_returned=rows,
                    shared_blks_hit=blocks_hit,
                    shared_blks_read=blocks_read,
                    pct_total_time=pct,
                    source="pg_stat" if db_engine == "postgresql" else "perf_schema",
                )
            )
        return queries

    def _compute_query_delta(
        self, baseline: list[dict], end: list[dict], db_engine: str, limit: int
    ) -> list[WorkloadQuery]:
        """Compute query deltas between baseline and end stat snapshots."""
        key_field = "queryid" if db_engine == "postgresql" else "DIGEST"
        baseline_map: dict[str, dict] = {}
        for row in baseline:
            key = row.get(key_field) or row.get(key_field.lower())
            if key:
                baseline_map[str(key)] = row

        deltas = []
        for row in end:
            key = row.get(key_field) or row.get(key_field.lower())
            if not key:
                continue
            key = str(key)
            base = baseline_map.get(key, {})

            if db_engine == "postgresql":
                calls_delta = int(row.get("calls", 0)) - int(base.get("calls", 0))
                time_delta = float(row.get("total_exec_time", 0)) - float(base.get("total_exec_time", 0))
            else:
                calls_delta = int(row.get("COUNT_STAR") or row.get("count_star") or 0) - int(
                    base.get("COUNT_STAR") or base.get("count_star") or 0
                )
                time_delta = float(row.get("total_time_ms", 0)) - float(base.get("total_time_ms", 0))

            if calls_delta <= 0:
                continue

            # Filter system queries at delta level too
            text = str(
                row.get("query") or row.get("DIGEST_TEXT") or row.get("digest_text") or ""
            ).lower()
            if any(s in text for s in [
                "pg_stat_", "pg_settings", "pg_catalog", "pg_database_size",
                "pg_backend_pid", "pg_stat_statements", "information_schema",
                "pg_replication", "performance_schema", "@@",
                "mysql.", "sys.",
            ]):
                continue

            # Only SELECT/WITH queries
            first_word = text.lstrip().split()[0] if text.strip() else ""
            if first_word not in ("select", "with"):
                continue

            row_copy = dict(row)
            if db_engine == "postgresql":
                row_copy["calls"] = calls_delta
                row_copy["total_exec_time"] = time_delta
                row_copy["mean_exec_time"] = time_delta / calls_delta if calls_delta > 0 else 0
            else:
                row_copy["count_star"] = calls_delta
                row_copy["COUNT_STAR"] = calls_delta
                row_copy["total_time_ms"] = time_delta
                row_copy["avg_time_ms"] = time_delta / calls_delta if calls_delta > 0 else 0
            deltas.append(row_copy)

        # Sort by slowest single execution. For Postgres pg_stat_statements
        # max_exec_time is captured directly; for MySQL the row carries
        # max_time_ms (mapped from MAX_TIMER_WAIT in collect_mysql_digest_stats).
        sort_key = "max_exec_time" if db_engine == "postgresql" else "max_time_ms"
        deltas.sort(key=lambda row: float(row.get(sort_key, 0)), reverse=True)
        return self._convert_raw_queries(deltas, db_engine, limit)
