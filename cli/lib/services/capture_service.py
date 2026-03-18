"""Capture service — async generator for workload capture and analysis."""

import asyncio
import datetime
import hashlib
import logging
import re
import signal
import time
from dataclasses import asdict
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from lib.cli.rdst_cli import TargetsConfig
from lib.fleet.models import WorkloadQuery, WorkloadRun
from lib.services.types import (
    WorkloadCaptureCompleteEvent,
    WorkloadCaptureProgressEvent,
    WorkloadCompleteEvent,
    WorkloadConnectedEvent,
    WorkloadErrorEvent,
    WorkloadEvent,
    WorkloadSnapshotEvent,
    WorkloadStatusEvent,
)

logger = logging.getLogger(__name__)

# Simple literal replacement for normalization
_LITERAL_RE = re.compile(r"'[^']*'|\b\d+\.?\d*\b")


def _normalize(sql: str) -> str:
    """Normalize SQL by replacing literals with ?."""
    return _LITERAL_RE.sub("?", sql).strip()


def _hash_sql(normalized: str) -> str:
    return hashlib.md5(normalized.encode()).hexdigest()[:12]


def _parse_duration(duration_str: str) -> int:
    """Parse duration string like '30m', '1h', '5m', '30s' to seconds."""
    s = duration_str.strip().lower()
    if s.endswith("h"):
        return int(float(s[:-1]) * 3600)
    if s.endswith("m"):
        return int(float(s[:-1]) * 60)
    if s.endswith("s"):
        return int(float(s[:-1]))
    return int(s)


class CaptureService:
    """Service layer for audit capture and analysis."""

    def __init__(self, config: Optional[TargetsConfig] = None):
        self._config = config
        self._stop_requested = False

    def _get_config(self) -> TargetsConfig:
        if self._config is None:
            self._config = TargetsConfig()
            self._config.load()
        return self._config

    def request_stop(self):
        """Request graceful stop (called from signal handler)."""
        self._stop_requested = True

    async def run_capture(
        self,
        target_name: str,
        duration_seconds: Optional[int] = None,
        snapshot_only: bool = False,
        source: str = "auto",
        limit: int = 50,
        run_analysis: bool = True,
        model: Optional[str] = None,
        save_top_queries: Optional[int] = None,
        queries_input: Optional[str] = None,
        concurrency: int = 4,
        delay: float = 0.1,
        cumulative_top_queries: Optional[list] = None,
        audit_result: Optional[dict] = None,
        save_capture: bool = True,
    ) -> AsyncGenerator[WorkloadEvent, None]:
        """Capture a workload from a database target."""
        from lib.db_connection import create_direct_connection
        from lib.functions.query_stats import (
            collect_database_snapshot,
            collect_intermediate_snapshot,
            collect_mysql_digest_stats,
            collect_pg_stat_statements,
            collect_table_stats,
            compute_snapshot_delta,
        )
        from lib.audit_storage import AuditStorage

        cfg = self._get_config()
        tc = cfg.get(target_name)
        if tc is None:
            yield WorkloadErrorEvent(
                type="error", message=f"Target '{target_name}' not found", phase="config"
            )
            return

        db_engine = tc.get("engine", "postgresql")
        storage = AuditStorage()
        run_id = storage.generate_run_id(target_name)

        yield WorkloadStatusEvent(type="status", phase="config", message=f"Connecting to {target_name}...")

        try:
            conn = create_direct_connection(tc)
        except Exception as e:
            yield WorkloadErrorEvent(type="error", message=str(e), phase="connect")
            return

        yield WorkloadConnectedEvent(
            type="connected", target_name=target_name, db_engine=db_engine,
            source="snapshot" if snapshot_only else "live",
        )

        replay_threads = []
        try:
            # Step 0: Check if query stats are available
            has_query_stats = False
            try:
                if db_engine == "postgresql":
                    test_cur = conn.cursor()
                    test_cur.execute("SELECT count(*) FROM pg_stat_statements LIMIT 1")
                    test_cur.fetchone()
                    test_cur.close()
                    has_query_stats = True
                else:
                    test_cur = conn.cursor()
                    test_cur.execute(
                        "SELECT count(*) FROM performance_schema.events_statements_summary_by_digest LIMIT 1"
                    )
                    row = test_cur.fetchone()
                    # DictCursor
                    val = list(row.values())[0] if isinstance(row, dict) else row[0]
                    has_query_stats = int(val) > 0
                    test_cur.close()
            except Exception:
                pass

            if not has_query_stats:
                ext = "pg_stat_statements" if db_engine == "postgresql" else "performance_schema"
                yield WorkloadStatusEvent(
                    type="status", phase="warning",
                    message=f"WARNING: {ext} is not enabled. Workload capture will have no query "
                    f"data — only database-level health metrics. Enable {ext} for meaningful results.",
                )

            # Step 1: Start snapshot
            yield WorkloadStatusEvent(type="status", phase="snapshot_start", message="Capturing start snapshot...")
            snapshot_start = collect_database_snapshot(conn, db_engine)
            table_stats_start = collect_table_stats(conn, db_engine)

            yield WorkloadSnapshotEvent(
                type="snapshot", when="start",
                cache_hit_ratio=snapshot_start.cache_hit_ratio,
                active_connections=snapshot_start.active_connections or 0,
            )

            # Step 1.5: Start replay workers if queries were provided
            replay_threads = []
            replay_queries = []
            if queries_input:
                replay_queries, load_error = self._load_replay_queries(queries_input, target_name)
                if load_error:
                    yield WorkloadErrorEvent(
                        type="error", message=load_error, phase="config"
                    )
                    conn.close()
                    return
                self._stop_requested = False
                replay_threads = self._start_replay_workers(tc, replay_queries, concurrency, delay)
                yield WorkloadStatusEvent(
                    type="status", phase="replay",
                    message=f"Replaying {len(replay_queries)} queries with {concurrency} threads",
                )

            # Step 2: Capture queries
            started_at = datetime.datetime.now(datetime.timezone.utc)
            intermediate_snapshots = []

            if snapshot_only:
                # Instant snapshot from pg_stat_statements / performance_schema
                yield WorkloadStatusEvent(type="status", phase="capture", message="Reading query statistics...")
                if db_engine == "postgresql":
                    raw_queries = collect_pg_stat_statements(conn)
                else:
                    raw_queries = collect_mysql_digest_stats(conn)

                queries = self._convert_raw_queries(raw_queries, db_engine, limit)
                actual_duration = 0

            else:
                # Live capture with periodic intermediate snapshots
                mode = "timed" if duration_seconds else "ctrl-c"
                if duration_seconds:
                    yield WorkloadStatusEvent(
                        type="status", phase="capture",
                        message=f"Capturing queries for {duration_seconds}s... (Ctrl+C to stop early)",
                    )
                else:
                    yield WorkloadStatusEvent(
                        type="status", phase="capture",
                        message="Capturing queries... (Ctrl+C to stop)",
                    )

                # Baseline query stats
                if db_engine == "postgresql":
                    baseline_queries = collect_pg_stat_statements(conn)
                else:
                    baseline_queries = collect_mysql_digest_stats(conn)

                capture_start = time.monotonic()
                last_intermediate = capture_start
                self._stop_requested = False

                while True:
                    elapsed = time.monotonic() - capture_start

                    # Check duration limit
                    if duration_seconds and elapsed >= duration_seconds:
                        break
                    if self._stop_requested:
                        break

                    # Intermediate snapshot every 30 seconds
                    if time.monotonic() - last_intermediate >= 30:
                        inter = collect_intermediate_snapshot(
                            conn, db_engine, elapsed, snapshot_start
                        )
                        intermediate_snapshots.append(inter)
                        last_intermediate = time.monotonic()

                        yield WorkloadCaptureProgressEvent(
                            type="capture_progress",
                            elapsed_seconds=round(elapsed, 1),
                            total_seconds=float(duration_seconds) if duration_seconds else None,
                            cache_hit_ratio=inter.cache_hit_ratio,
                            active_connections=inter.active_connections,
                            tps=inter.transactions_per_sec,
                        )

                    await asyncio.sleep(2)

                actual_duration = int(time.monotonic() - capture_start)

                # End query stats — compute delta
                if db_engine == "postgresql":
                    end_queries = collect_pg_stat_statements(conn)
                else:
                    end_queries = collect_mysql_digest_stats(conn)

                queries = self._compute_query_delta(baseline_queries, end_queries, db_engine, limit)

            ended_at = datetime.datetime.now(datetime.timezone.utc)
            total_query_time = sum(q.total_time_ms for q in queries)
            total_executions = sum(q.calls for q in queries)

            yield WorkloadCaptureCompleteEvent(
                type="capture_complete",
                unique_queries=len(queries),
                total_executions=total_executions,
                total_query_time_ms=total_query_time,
                duration_seconds=float(actual_duration),
            )

            # Step 2.5: Stop replay workers if running
            if replay_threads:
                self._stop_replay_workers(replay_threads)
                replay_threads = []

            # Step 3: End snapshot
            yield WorkloadStatusEvent(type="status", phase="snapshot_end", message="Capturing end snapshot...")
            snapshot_end = collect_database_snapshot(conn, db_engine)
            table_stats_end = collect_table_stats(conn, db_engine)

            yield WorkloadSnapshotEvent(
                type="snapshot", when="end",
                cache_hit_ratio=snapshot_end.cache_hit_ratio,
                active_connections=snapshot_end.active_connections or 0,
            )

            # Step 4: Compute deltas
            delta_stats = compute_snapshot_delta(snapshot_start, snapshot_end)

            # Step 5: Build and save the run
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

            # Step 6: LLM Analysis (if requested and queries exist)
            analysis_dict = None
            if run_analysis and queries:
                # Collect schema context for index recommendations
                # Use both captured queries AND cumulative top queries for table extraction
                schema_context = ""
                try:
                    yield WorkloadStatusEvent(type="status", phase="schema", message="Collecting schema context...")
                    from lib.functions.query_stats import (
                        _extract_table_names_from_queries,
                        collect_schema_for_tables,
                    )
                    all_query_dicts = [{"normalized_query": q.normalized_query} for q in queries]
                    if cumulative_top_queries:
                        all_query_dicts.extend(cumulative_top_queries)
                    table_names = _extract_table_names_from_queries(all_query_dicts)
                    if table_names:
                        all_query_texts = [q.normalized_query or "" for q in queries]
                        if cumulative_top_queries:
                            all_query_texts.extend(
                                q.get("normalized_query") or q.get("query") or ""
                                for q in cumulative_top_queries
                            )
                        schema_context = collect_schema_for_tables(
                            conn, table_names, db_engine, query_texts=all_query_texts,
                        )
                        if schema_context:
                            logger.debug("Collected schema for %d tables", len(table_names))
                except Exception as e:
                    logger.debug("Schema collection skipped: %s", e)

                yield WorkloadStatusEvent(type="status", phase="analysis", message="Running LLM analysis...")
                try:
                    from dataclasses import asdict
                    from lib.prompts.audit_prompts import build_audit_capture_prompt
                    import json as _json

                    run_dict = asdict(run)
                    prompt = build_audit_capture_prompt(
                        run_dict, limit=min(limit, 30),
                        cumulative_top_queries=cumulative_top_queries,
                        audit_result=audit_result,
                        schema_context=schema_context,
                    )

                    from lib.llm_manager.llm_manager import LLMManager

                    llm = LLMManager()
                    llm_result = llm.generate_response(prompt, max_tokens=6144, temperature=0.0)
                    raw_text = llm_result.get("response", "")
                    from lib.util.json_parse import parse_llm_json
                    analysis_dict = parse_llm_json(raw_text)
                    used_model = llm_result.get("model", "unknown")
                    analysis_dict["model_used"] = used_model
                    analysis_dict["raw_response"] = raw_text
                    if schema_context:
                        analysis_dict["schema_context"] = schema_context

                    # Store analysis in the run
                    from lib.fleet.models import WorkloadAnalysis
                    run.analysis = WorkloadAnalysis(
                        model_used=used_model,
                        workload_characterization=analysis_dict.get("workload_characterization", ""),
                        health_score=analysis_dict.get("health_score", 0),
                        read_write_ratio=analysis_dict.get("read_write_ratio", ""),
                        top_bottlenecks=analysis_dict.get("top_bottlenecks", []),
                        index_recommendations=analysis_dict.get("index_recommendations", []),
                        caching_candidates=analysis_dict.get("caching_candidates", []),
                        capacity_insights=analysis_dict.get("capacity_insights", []),
                        optimization_priorities=analysis_dict.get("optimization_priorities", []),
                        raw_response=raw_text,
                        schema_context=schema_context,
                    )

                    from lib.services.types import WorkloadAnalysisProgressEvent
                    yield WorkloadAnalysisProgressEvent(
                        type="analysis_progress", message="Analysis complete", percent=100
                    )
                except Exception as e:
                    logger.warning("LLM analysis failed: %s", e)
                    yield WorkloadStatusEvent(
                        type="status", phase="analysis",
                        message=f"LLM analysis failed: {e}",
                    )

            # Step 7: Auto-save queries to registry
            # Default: save all captured queries. Use save_top_queries=0 to disable.
            n_save = save_top_queries if save_top_queries is not None else len(queries)
            saved_hashes = []
            if n_save > 0 and queries:
                try:
                    from lib.query_registry.query_registry import QueryRegistry
                    registry = QueryRegistry()
                    registry.load()
                    for q in queries[:n_save]:
                        try:
                            registry.add_query(
                                sql=q.query_text,
                                tag=f"capture_{run_id[:8]}",
                                target=target_name,
                            )
                            saved_hashes.append(q.query_hash)
                        except Exception:
                            # Skip queries that fail validation (truncated, etc.)
                            continue
                    if saved_hashes:
                        registry.save()
                        from lib.services.types import WorkloadQueriesSavedEvent
                        yield WorkloadQueriesSavedEvent(
                            type="queries_saved", count=len(saved_hashes), hashes=saved_hashes
                        )
                except Exception as e:
                    logger.warning("Failed to save queries to registry: %s", e)

            # Step 8: Save the run (with analysis if available)
            path = ""
            if save_capture:
                yield WorkloadStatusEvent(type="status", phase="storage", message="Saving audit capture...")
                path = storage.save_run(run)

            # Build query list for embedding in snapshot
            from dataclasses import asdict as _asdict
            query_dicts = [_asdict(q) for q in queries] if queries else []

            yield WorkloadCompleteEvent(
                type="complete",
                success=True,
                run_id=run_id,
                summary={
                    "run_id": run_id,
                    "target": target_name,
                    "duration_seconds": actual_duration if not snapshot_only else 0,
                    "unique_queries": len(queries),
                    "total_executions": total_executions,
                    "total_query_time_ms": round(total_query_time, 1),
                    "path": path,
                    "has_analysis": analysis_dict is not None,
                    "queries": query_dicts,
                },
                analysis=analysis_dict,
            )

        except Exception as e:
            # Stop replay workers on error
            if replay_threads:
                self._stop_replay_workers(replay_threads)
            yield WorkloadErrorEvent(type="error", message=str(e), phase="capture")
        finally:
            # Ensure replay workers are stopped
            if replay_threads:
                self._stop_replay_workers(replay_threads)
            try:
                conn.close()
            except Exception:
                pass

    def _load_replay_queries(self, queries_input: str, target_name: str) -> Tuple[List[str], Optional[str]]:
        """Load queries from a CSV file or comma-separated registry hashes.

        Returns (queries, error_message). error_message is None on success.
        """
        import csv
        from pathlib import Path

        queries: List[str] = []

        # Check if it looks like a file path
        path = Path(queries_input)
        if path.suffix in (".csv", ".txt", ".sql") or "/" in queries_input or "\\" in queries_input:
            if not path.exists():
                return [], f"Queries file not found: {queries_input}"
            if not path.is_file():
                return [], f"Not a file: {queries_input}"
            if path.stat().st_size == 0:
                return [], f"Queries file is empty: {queries_input}"

            with open(path, "r", newline="", encoding="utf-8-sig") as f:
                # Try CSV with header
                reader = csv.DictReader(f)
                if reader.fieldnames and "query" in [h.lower().strip() for h in reader.fieldnames]:
                    for row in reader:
                        sql = (row.get("query") or row.get("Query") or row.get("sql") or row.get("SQL") or "").strip()
                        if sql:
                            queries.append(sql)
                else:
                    # Plain text, one query per line
                    f.seek(0)
                    for line in f:
                        sql = line.strip()
                        if sql and not sql.startswith("#"):
                            queries.append(sql)

            if not queries:
                return [], (
                    f"No valid queries found in {queries_input}. "
                    f"Expected CSV with 'query' column header, or one SQL query per line."
                )

            # Basic validation — only allow read-only queries for replay
            sql_keywords = {"select", "with", "explain"}
            valid = [q for q in queries if q.split()[0].lower() in sql_keywords]
            if not valid:
                return [], (
                    f"No valid SQL queries found in {queries_input}. "
                    f"Queries must start with SELECT, WITH, or EXPLAIN (read-only)."
                )

            return valid, None

        # Otherwise treat as comma-separated registry hashes
        hashes = [h.strip() for h in queries_input.split(",") if h.strip()]
        if hashes:
            try:
                from lib.query_registry.query_registry import QueryRegistry
                registry = QueryRegistry()
                registry.load()
                not_found = []
                for h in hashes:
                    entry = registry.get_query(h)
                    if entry:
                        sql = entry.get("sql") or entry.get("query", "")
                        if sql:
                            queries.append(sql)
                    else:
                        not_found.append(h)
                if not_found:
                    logger.warning("Query hashes not found in registry: %s", ", ".join(not_found))
                if not queries:
                    return [], f"No queries found for hashes: {', '.join(hashes)}. Check rdst query list."
            except Exception as e:
                return [], f"Failed to load queries from registry: {e}"

        if not queries:
            return [], f"Could not load queries from: {queries_input}"

        return queries, None

    def _start_replay_workers(
        self,
        target_config: Dict[str, Any],
        queries: List[str],
        concurrency: int,
        delay: float,
    ) -> List[Any]:
        """Start replay worker threads. Returns list of threads."""
        import threading
        from lib.db_connection import create_direct_connection

        threads = []

        def _worker(worker_id: int):
            try:
                conn = create_direct_connection(target_config)
                cur = conn.cursor()
                idx = 0
                while not self._stop_requested:
                    sql = queries[idx % len(queries)]
                    try:
                        cur.execute(sql)
                        cur.fetchall()
                    except Exception:
                        pass
                    idx += 1
                    import time as _time
                    _time.sleep(delay + (hash(worker_id + idx) % 100) / 1000.0)
                cur.close()
                conn.close()
            except Exception:
                pass

        for i in range(concurrency):
            t = threading.Thread(target=_worker, args=(i,), daemon=True)
            t.start()
            threads.append(t)

        return threads

    def _stop_replay_workers(self, threads: List[Any]) -> None:
        """Stop replay worker threads."""
        self._stop_requested = True
        for t in threads:
            t.join(timeout=5)

    def _convert_raw_queries(
        self, raw: List[Dict], db_engine: str, limit: int
    ) -> List[WorkloadQuery]:
        """Convert raw pg_stat_statements / digest rows to WorkloadQuery objects."""
        queries = []
        total_time = sum(float(r.get("total_exec_time") or r.get("total_time_ms") or 0) for r in raw)

        for r in raw[:limit]:
            if db_engine == "postgresql":
                text = str(r.get("query", ""))
                calls = int(r.get("calls", 0))
                total_ms = float(r.get("total_exec_time", 0))
                avg_ms = float(r.get("mean_exec_time", 0))
                max_ms = float(r.get("max_exec_time", 0))
                blks_hit = int(r.get("shared_blks_hit", 0))
                blks_read = int(r.get("shared_blks_read", 0))
                rows = int(r.get("rows", 0))
            else:
                text = str(r.get("DIGEST_TEXT") or r.get("digest_text") or "")
                calls = int(r.get("COUNT_STAR") or r.get("count_star") or 0)
                total_ms = float(r.get("total_time_ms", 0))
                avg_ms = float(r.get("avg_time_ms", 0))
                max_ms = float(r.get("max_time_ms", 0))
                blks_hit = None
                blks_read = None
                rows = int(r.get("rows_sent") or r.get("SUM_ROWS_SENT") or 0)

            normalized = _normalize(text)
            qhash = _hash_sql(normalized)
            pct = round((total_ms / total_time) * 100, 1) if total_time > 0 else 0

            queries.append(WorkloadQuery(
                query_hash=qhash,
                query_text=text[:16384],
                normalized_query=normalized[:16384],
                calls=calls,
                total_time_ms=round(total_ms, 2),
                avg_time_ms=round(avg_ms, 2),
                max_time_ms=round(max_ms, 2),
                rows_returned=rows,
                shared_blks_hit=blks_hit,
                shared_blks_read=blks_read,
                pct_total_time=pct,
                source="pg_stat" if db_engine == "postgresql" else "perf_schema",
            ))

        return queries

    def _compute_query_delta(
        self, baseline: List[Dict], end: List[Dict], db_engine: str, limit: int
    ) -> List[WorkloadQuery]:
        """Compute query deltas between baseline and end stat snapshots."""
        # Index baseline by query id
        if db_engine == "postgresql":
            key_field = "queryid"
        else:
            key_field = "DIGEST"

        base_map = {}
        for r in baseline:
            k = r.get(key_field) or r.get(key_field.lower())
            if k:
                base_map[str(k)] = r

        # Compute deltas
        deltas = []
        for r in end:
            k = r.get(key_field) or r.get(key_field.lower())
            if not k:
                continue
            k = str(k)
            base = base_map.get(k, {})

            if db_engine == "postgresql":
                calls_delta = int(r.get("calls", 0)) - int(base.get("calls", 0))
                time_delta = float(r.get("total_exec_time", 0)) - float(base.get("total_exec_time", 0))
            else:
                calls_delta = int(r.get("COUNT_STAR") or r.get("count_star") or 0) - int(base.get("COUNT_STAR") or base.get("count_star") or 0)
                time_delta = float(r.get("total_time_ms", 0)) - float(base.get("total_time_ms", 0))

            if calls_delta <= 0:
                continue

            r_copy = dict(r)
            if db_engine == "postgresql":
                r_copy["calls"] = calls_delta
                r_copy["total_exec_time"] = time_delta
                r_copy["mean_exec_time"] = time_delta / calls_delta if calls_delta > 0 else 0
            else:
                r_copy["count_star"] = calls_delta
                r_copy["COUNT_STAR"] = calls_delta
                r_copy["total_time_ms"] = time_delta
                r_copy["avg_time_ms"] = time_delta / calls_delta if calls_delta > 0 else 0

            deltas.append(r_copy)

        # Sort by total time descending
        if db_engine == "postgresql":
            deltas.sort(key=lambda r: float(r.get("total_exec_time", 0)), reverse=True)
        else:
            deltas.sort(key=lambda r: float(r.get("total_time_ms", 0)), reverse=True)

        return self._convert_raw_queries(deltas, db_engine, limit)
