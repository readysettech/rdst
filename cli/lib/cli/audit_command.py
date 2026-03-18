"""Audit command — single-target deep health audit."""

import argparse
import asyncio
import json
import logging
import os
import signal
import time

# Suppress noisy sqlglot warnings during query normalization
logging.getLogger("sqlglot").setLevel(logging.ERROR)

from lib.ui import Status, ElapsedMessage
from lib.cli.rdst_cli import RdstResult
from lib.ui import get_console


class AuditCommand:
    """Orchestrates single-target audit: metrics collection, sizing, cache opportunity."""

    def execute(self, args: argparse.Namespace) -> RdstResult:
        """Run a deep audit on a single target."""
        target = getattr(args, "target", None)
        if not target:
            return RdstResult(False, "Target required: rdst audit --target <name>")

        output_json = getattr(args, "output_json", False)
        save_name = getattr(args, "save_name", None)
        insights = not getattr(args, "no_insights", False)
        no_save = getattr(args, "no_save", False)

        from lib.services.audit_service import AuditService

        service = AuditService()
        console = get_console()

        if insights:
            from lib.services.anthropic_env import has_anthropic_api_key
            if not has_anthropic_api_key():
                insights = False
                if not output_json:
                    console.print(
                        "[yellow]Warning: No LLM API key found. Skipping insights.\n"
                        "Set with: export ANTHROPIC_API_KEY='your-key' or run: rdst init[/yellow]\n"
                    )
        final_result = {}

        async def _run():
            nonlocal final_result
            async for event in service.audit_single(target):
                if event.type == "status":
                    if not output_json:
                        console.print(f"[dim]{event.message}[/dim]")
                elif event.type == "target_complete":
                    final_result = event.result
                elif event.type == "target_error":
                    if not output_json:
                        console.print(f"[red]Error: {event.error}[/red]")
                elif event.type == "error":
                    if not output_json:
                        console.print(f"[red]{event.message}[/red]")
                    return

        asyncio.run(_run())

        if not final_result:
            return RdstResult(False, "Audit failed")

        # Live workload capture if --duration was provided (run before rendering/JSON)
        duration_str = getattr(args, "duration", None)
        workload_result = None
        if duration_str and final_result:
            workload_result = self._run_workload_capture(
                console=console,
                target=target,
                duration_str=duration_str,
                source=getattr(args, "source", "auto"),
                limit=getattr(args, "limit", 50),
                no_save=getattr(args, "no_save", False),
                output_json=output_json,
                run_analysis=insights,
                cumulative_top_queries=final_result.get("top_queries"),
                audit_result=final_result,
            )
            if workload_result:
                final_result["workload"] = workload_result

                # Merge full audit data (metrics, sizing, etc.) into the saved capture file
                # so that 'audit show' renders the complete report
                if not getattr(args, "no_save", False):
                    self._merge_audit_into_capture(
                        target, workload_result.get("run_id", ""), final_result
                    )

        # Save discovered queries to registry
        top_queries = final_result.get("top_queries", [])
        if top_queries and not no_save:
            try:
                from lib.query_registry.query_registry import QueryRegistry
                registry = QueryRegistry()
                registry.load()
                saved = 0
                for q in top_queries:
                    sql = q.get("query_text", "")
                    if not sql or not sql.strip().upper().startswith("SELECT"):
                        continue
                    try:
                        registry.add_query(
                            sql=sql,
                            tag=f"audit_{q.get('query_hash', '')[:8]}",
                            target=target,
                            source="audit",
                        )
                        saved += 1
                    except Exception:
                        continue
                if saved:
                    registry.save()
            except Exception:
                pass

        if output_json:
            print(json.dumps(final_result, indent=2, default=str))
            return RdstResult(True, message="")

        # Render the audit result (metrics, sizing, top queries)
        self._render_result(console, final_result)

        # Render workload capture data below audit metrics (if --duration was used)
        if workload_result:
            run_id = workload_result.get("run_id", "")
            if run_id:
                self._render_workload_queries(console, run_id, target)
            workload_analysis = workload_result.get("analysis")
            if workload_analysis:
                self._render_workload_analysis(console, workload_analysis, target_name=target)
            if run_id:
                console.print(f"\n[dim]View again: rdst audit show {run_id}[/dim]")

        # Always save audit result (unless --no-save)
        if not no_save and final_result:
            import datetime as _dt
            from lib.fleet.snapshot_store import SnapshotStore
            auto_name = save_name or f"audit_{target}_{_dt.datetime.now():%Y%m%d_%H%M%S}"
            store = SnapshotStore()
            path = store.save_raw(auto_name, final_result)
            if not output_json:
                console.print(f"\n[dim]Audit saved. View past audits: rdst audit list[/dim]")

        # LLM insights for this single target
        # If workload analysis already ran (--duration), skip — already rendered above.
        # If no duration, make one LLM call with metrics + top queries.
        has_workload_analysis = workload_result and workload_result.get("analysis")
        if insights and final_result and not has_workload_analysis:
            spinner = Status(
                ElapsedMessage(f"Generating insights for {target}...", time.monotonic()),
                spinner="dots",
                console=console,
            )
            spinner.start()
            try:
                from lib.fleet.fleet_llm import build_single_target_insights_prompt
                from lib.llm_manager.llm_manager import LLMManager

                prompt = build_single_target_insights_prompt(final_result)
                llm = LLMManager()
                result = llm.generate_response(prompt, max_tokens=4096, temperature=0.0)
                spinner.stop()
                insights_text = result.get("response", "")
                self._render_audit_insights(console, insights_text)
            except Exception as e:
                spinner.stop()
                console.print(f"[red]LLM insights failed: {e}[/red]")

        return RdstResult(True, message="")

    def _render_audit_insights(self, console, raw_text: str) -> None:
        """Parse structured JSON insights and render with Rich."""
        import json as _json
        from lib.ui import StyledPanel

        try:
            from lib.util.json_parse import parse_llm_json
            data = parse_llm_json(raw_text)
        except Exception as e:
            console.print(f"\n[red]Failed to parse LLM response as JSON: {e}[/red]")
            console.print(f"[dim]Raw response (first 200 chars): {raw_text[:200]}[/dim]")
            return

        # Health summary
        console.print()
        console.print(StyledPanel(data.get("health_summary", ""), title="Health Assessment"))

        # Readyset verdict
        verdict = data.get("readyset_verdict", "?")
        summary = data.get("readyset_summary", "")
        cacheable = data.get("estimated_cacheable_pct")
        verdict_colors = {
            "strong_candidate": "green",
            "good_candidate": "green",
            "marginal": "yellow",
            "not_recommended": "red",
        }
        vc = verdict_colors.get(verdict, "dim")
        readyset_text = f"[{vc}][bold]{verdict.replace('_', ' ').title()}[/bold][/{vc}]"
        if cacheable is not None:
            readyset_text += f" — {cacheable}% of queries cacheable"
        readyset_text += f"\n{summary}"
        console.print(StyledPanel(readyset_text, title="Readyset Recommendation"))

        # Sizing
        sizing = data.get("sizing_recommendation", "")
        if sizing and sizing != "No change needed":
            console.print(StyledPanel(sizing, title="Sizing"))

        # Key findings
        findings = data.get("key_findings", [])
        if findings:
            findings_text = "\n".join(f"  {i+1}. {f}" for i, f in enumerate(findings))
            console.print(StyledPanel(findings_text, title="Key Findings"))

        # Concerns
        concerns = data.get("top_concerns", [])
        if concerns and concerns != ["None"]:
            concerns_text = "\n".join(f"  {c}" for c in concerns)
            console.print(StyledPanel(concerns_text, title="Concerns"))

    def execute_subcommand(self, subcommand: str, args: argparse.Namespace) -> RdstResult:
        """Route to audit subcommands (list, show)."""
        handler = getattr(self, f"_handle_{subcommand}", None)
        if handler is None:
            return RdstResult(False, f"Unknown audit subcommand: {subcommand}")
        return handler(args)

    def _handle_list(self, args: argparse.Namespace) -> RdstResult:
        """List past audit runs (both quick audits and duration captures)."""
        target = getattr(args, "target", None)
        limit = getattr(args, "limit", 20)
        output_json = getattr(args, "output_json", False)

        from lib.audit_storage import AuditStorage

        storage = AuditStorage()
        runs = storage.list_runs(target=target, limit=limit)

        # Also include saved audit snapshots (quick audits without duration)
        from lib.fleet.snapshot_store import SnapshotStore
        store = SnapshotStore()
        snapshots_dir = store.base_dir
        if snapshots_dir.exists():
            import json as _json
            for path in sorted(snapshots_dir.glob("audit_*.json"), reverse=True):
                try:
                    with open(path) as f:
                        data = _json.load(f)
                    snap_target = data.get("target_name", "")
                    if target and snap_target != target:
                        continue
                    # Don't duplicate — skip if this audit also has a workload run
                    if data.get("workload", {}).get("run_id"):
                        continue
                    runs.append({
                        "run_id": path.stem,
                        "target_name": snap_target,
                        "started_at": data.get("audited_at", "")[:19],
                        "duration_seconds": 0,
                        "total_queries": len(data.get("top_queries", [])),
                        "source": "quick",
                        "has_analysis": False,
                        "path": str(path),
                    })
                except Exception:
                    continue
            # Re-sort by started_at descending and apply limit
            runs.sort(key=lambda r: r.get("started_at", ""), reverse=True)
            runs = runs[:limit]

        if output_json:
            print(json.dumps(runs, indent=2))
            return RdstResult(True, message="")

        console = get_console()
        if not runs:
            console.print("[dim]No audit runs found.[/dim]")
            console.print("[dim]Run an audit: rdst audit --target <name>[/dim]")
            return RdstResult(True, message="No runs")

        from lib.ui import DataTable

        rows = []
        for r in runs:
            rows.append((
                r["run_id"],
                r["target_name"],
                r["started_at"][:19],
            ))

        table = DataTable(
            title=f"Audit Runs ({len(runs)})",
            columns=["Run ID", "Target", "Date"],
            rows=rows,
        )
        console.print(table)
        console.print(f"\n[dim]View details: rdst audit show <run_id>[/dim]")
        return RdstResult(True, message=f"{len(runs)} runs")

    def _handle_show(self, args: argparse.Namespace) -> RdstResult:
        """View a past audit run. Finds the file, renders it."""
        run_id = getattr(args, "run_id", None)
        output_json = getattr(args, "output_json", False)
        export_queries = getattr(args, "export_queries", False)
        export_top = getattr(args, "export_top_queries", False)
        export_captured = getattr(args, "export_captured_queries", False)

        if not run_id:
            return RdstResult(False, "Run ID required")

        import json as _json
        from lib.fleet.snapshot_store import SnapshotStore
        from lib.audit_storage import AuditStorage

        data = None
        target = getattr(args, "target", None)

        # Search snapshots first (full merged reports)
        store = SnapshotStore()
        if store.base_dir.exists():
            exact = store.base_dir / f"{run_id}.json"
            if exact.exists():
                with open(exact) as f:
                    data = _json.load(f)
            else:
                for p in sorted(store.base_dir.glob("*.json"), reverse=True):
                    if run_id in p.stem:
                        with open(p) as f:
                            data = _json.load(f)
                        break

        # Fall back to capture storage (duration captures from single-target audit)
        if data is None:
            storage = AuditStorage()
            if not target:
                for r in storage.list_runs(limit=200):
                    if r["run_id"] == run_id or r["run_id"].startswith(run_id):
                        target = r["target_name"]
                        run_id = r["run_id"]
                        break
            if target:
                data = storage.load_run(target, run_id)

        if data is None:
            return RdstResult(False, f"Run '{run_id}' not found. Try: rdst audit list")

        console = get_console()

        # Export queries mode (check before general JSON output)
        if export_queries or export_top or export_captured:
            # Individual audit saves: data["queries"] at top level
            # Fleet snapshots: data["workload"]["queries"]
            wl = data.get("workload") or {}
            captured = data.get("queries") or wl.get("queries", [])
            top = data.get("top_queries", [])

            if export_top:
                queries = top
                source = "top"
            elif export_captured:
                queries = captured
                source = "captured"
                if not queries:
                    return RdstResult(False, "No captured queries. Run audit with --duration first.")
            else:
                # --export-queries: prefer captured, fall back to top
                queries = captured if captured else top
                source = "captured" if captured else "top"

            if output_json:
                export_data = {
                    "target": data.get("target_name"),
                    "source": source,
                    "query_count": len(queries),
                    "queries": queries,
                }
                print(json.dumps(export_data, indent=2, default=str))
                return RdstResult(True, message="")
            return self._export_queries(console, data, queries=queries, source=source)

        if output_json:
            print(json.dumps(data, indent=2, default=str))
            return RdstResult(True, message="")

        # Render the audit — metrics, sizing, top queries
        self._render_result(console, data)

        # Render captured queries if present
        # Individual audit: data["queries"], Fleet: data["workload"]["queries"]
        wl = data.get("workload") or {}
        wl_queries = data.get("queries") or wl.get("queries", [])
        if wl_queries:
            from lib.ui import DataTable
            duration = wl.get("duration_seconds", 0) or 0
            rows = []
            for q in wl_queries:
                sql = (q.get("normalized_query") or "")[:60]
                row = [q.get("query_hash", "")[:8], sql, str(q.get("calls", 0))]
                if duration > 0:
                    qps = q.get("calls", 0) / duration
                    row.append(f"{qps:.1f}" if qps < 1000 else f"{qps/1000:.1f}K")
                row.extend([
                    f"{q.get('total_time_ms', 0):.0f}ms",
                    f"{q.get('avg_time_ms', 0):.1f}ms",
                    f"{q.get('pct_total_time', 0):.1f}%",
                ])
                rows.append(tuple(row))
            columns = ["Hash", "Query", "Calls"]
            if duration > 0:
                columns.append("QPS")
            columns.extend(["Total", "Avg", "% Load"])
            console.print(DataTable(
                title=f"Captured Queries ({len(wl_queries)} unique, {duration}s window)",
                columns=columns,
                rows=rows,
            ))

        # Render analysis if present
        # Individual audit: data["analysis"], Fleet: data["workload"]["analysis"]
        analysis = data.get("analysis") or wl.get("analysis")
        if analysis:
            show_target = data.get("target_name", "")
            self._render_workload_analysis(console, analysis, target_name=show_target)

        return RdstResult(True, message="")

        # Top queries
        queries = data.get("queries", [])
        if queries:
            from lib.ui import DataTable

            rows = []
            for q in queries[:15]:
                sql = q.get("normalized_query", "")[:60]
                rows.append((
                    q.get("query_hash", "")[:8],
                    sql,
                    str(q.get("calls", 0)),
                    f"{q.get('total_time_ms', 0):.0f}ms",
                    f"{q.get('avg_time_ms', 0):.1f}ms",
                    f"{q.get('pct_total_time', 0):.1f}%",
                ))

            table = DataTable(
                title="Top Queries by Total Time",
                columns=["Hash", "Query", "Calls", "Total", "Avg", "% Load"],
                rows=rows,
            )
            console.print(table)

        # Analysis if present
        analysis = data.get("analysis")
        if analysis:
            self._render_workload_analysis(console, analysis, target_name=data.get("target_name", ""))

        return RdstResult(True, message="Run displayed")

    def _export_queries(self, console, data: dict, queries: list = None, source: str = None) -> RdstResult:
        """Export full query text from an audit run."""
        from lib.ui import DataTable

        wl = data.get("workload") or {}

        if queries is None:
            captured = wl.get("queries", [])
            top = data.get("top_queries", [])
            if captured:
                source = "captured"
                queries = captured
            elif top:
                source = "top"
                queries = top
            else:
                return RdstResult(False, "No queries found in this audit run")

        duration = wl.get("duration_seconds", 0) if source == "captured" else 0

        target = data.get("target_name", "unknown")
        engine = data.get("engine", "")
        console.print(f"\n[bold]Exported Queries[/bold] — {target} ({source}, {len(queries)} queries)")
        if duration:
            console.print(f"[dim]Capture window: {duration}s[/dim]")

        # Check for actually truncated queries (cut off mid-statement)
        truncated_count = 0
        for q in queries:
            sql = (q.get("query_text") or q.get("normalized_query") or "").rstrip()
            if len(sql) < 1020:
                continue
            last_char = sql[-1] if sql else ""
            if last_char in ("?", ")", "*", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9"):
                continue
            last_token = sql.split()[-1] if sql.split() else ""
            if last_token.upper() in ("DESC", "ASC", "LIMIT", "NULL", "TRUE", "FALSE", "ALL"):
                continue
            truncated_count += 1
        if truncated_count > 0 and "mysql" in engine.lower():
            console.print(
                f"\n[yellow]{truncated_count} queries are truncated. "
                f"To get full text, set performance_schema_max_digest_length = 16384 and "
                f"max_digest_length = 16384 in the DB parameter group, restart the instance, "
                f"then CALL sys.ps_truncate_all_tables(FALSE) to clear old entries.[/yellow]"
            )
        elif truncated_count > 0:
            console.print(
                f"\n[yellow]{truncated_count} queries are truncated. "
                f"Check track_activity_query_size in postgresql.conf.[/yellow]"
            )

        console.print()

        for i, q in enumerate(queries):
            sql = q.get("query_text") or q.get("normalized_query") or ""
            qhash = q.get("query_hash", "?")[:12]
            calls = q.get("calls", 0)
            avg = q.get("avg_time_ms", 0)
            pct = q.get("pct_total_time", 0)

            console.print(f"[bold]Query {i+1}[/bold] [{qhash}]  calls={calls}  avg={avg:.1f}ms  load={pct:.1f}%")
            if duration and calls:
                qps = calls / duration
                console.print(f"  QPS: {qps:.1f}")
            console.print(f"[dim]{sql}[/dim]")
            console.print()

        return RdstResult(True, message=f"{len(queries)} queries exported")

    def _run_workload_capture(
        self,
        console,
        target: str,
        duration_str: str,
        source: str = "auto",
        limit: int = 50,
        no_save: bool = False,
        output_json: bool = False,
        run_analysis: bool = True,
        cumulative_top_queries: list = None,
        audit_result: dict = None,
    ) -> dict:
        """Run live workload capture after the standard audit completes."""
        from lib.services.capture_service import CaptureService, _parse_duration

        duration_seconds = _parse_duration(duration_str)
        service = CaptureService()
        final_summary = {}
        final_analysis = None
        save_top_queries_n = 0 if no_save else None

        # Set up Ctrl+C handler for graceful stop
        _sigint_count = 0

        def _sigint_handler(sig, frame):
            nonlocal _sigint_count
            _sigint_count += 1
            service.request_stop()
            if _sigint_count >= 2:
                if not output_json:
                    console.print("\n[red]Force stopping...[/red]")
                raise KeyboardInterrupt
            if not output_json:
                console.print("\n[yellow]Stopping capture (finishing up)... Press Ctrl+C again to force quit.[/yellow]")

        old_handler = signal.signal(signal.SIGINT, _sigint_handler)

        try:
            async def _run():
                nonlocal final_summary, final_analysis
                spinner = None
                capture_start = None

                def _stop_spinner():
                    nonlocal spinner
                    if spinner is not None:
                        spinner.stop()
                        spinner = None

                async for event in service.run_capture(
                    target_name=target,
                    duration_seconds=duration_seconds,
                    source=source,
                    limit=limit,
                    run_analysis=run_analysis,
                    save_top_queries=save_top_queries_n,
                    cumulative_top_queries=cumulative_top_queries,
                    audit_result=audit_result,
                ):
                    if output_json:
                        if event.type == "complete":
                            final_summary = event.summary or {}
                            final_analysis = event.analysis
                        continue

                    if event.type == "status":
                        _stop_spinner()
                        if event.phase == "warning":
                            console.print(f"[yellow bold]{event.message}[/yellow bold]")
                        elif event.phase == "capture":
                            capture_start = time.monotonic()
                            spinner = Status(
                                ElapsedMessage(event.message, capture_start),
                                spinner="dots",
                                console=console,
                            )
                            spinner.start()
                        elif event.phase == "analysis":
                            spinner = Status(
                                ElapsedMessage(event.message, time.monotonic()),
                                spinner="dots",
                                console=console,
                            )
                            spinner.start()
                        else:
                            console.print(f"[dim]{event.message}[/dim]")
                    elif event.type == "connected":
                        console.print(f"\n[bold]Live Workload Capture[/bold] ({duration_str})")
                        console.print(f"[dim]Connected to {event.target_name} ({event.db_engine})[/dim]")
                    elif event.type == "snapshot":
                        _stop_spinner()
                        hit = event.cache_hit_ratio or 0
                        console.print(
                            f"  {event.when.capitalize()} snapshot: "
                            f"cache hit {hit:.1f}%, "
                            f"{event.active_connections} active connections"
                        )
                    elif event.type == "capture_progress":
                        if spinner and capture_start:
                            elapsed = int(event.elapsed_seconds or 0)
                            total = int(event.total_seconds or 0)
                            time_str = f" {elapsed}s/{total}s" if total else f" {elapsed}s"
                            hit = event.cache_hit_ratio or 0
                            stats_msg = (
                                f"Capturing...{time_str} | "
                                f"cache: {hit:.1f}%, conns: {event.active_connections}, "
                                f"tps: {event.tps:.1f}"
                            )
                            spinner.update(stats_msg)
                    elif event.type == "capture_complete":
                        _stop_spinner()
                        console.print(
                            f"\n[bold]Capture complete:[/bold] {event.unique_queries} unique queries, "
                            f"{event.total_executions} total executions, "
                            f"{event.total_query_time_ms:.0f}ms total time"
                        )
                    elif event.type == "queries_saved":
                        console.print(f"  [green]Saved {event.count} queries to registry[/green]")
                    elif event.type == "complete":
                        _stop_spinner()
                        final_summary = event.summary or {}
                        final_analysis = event.analysis
                        if event.success:
                            console.print(f"\n[green]Workload captured.[/green]")
                        else:
                            console.print("[red]Workload capture failed[/red]")
                    elif event.type == "error":
                        _stop_spinner()
                        console.print(f"[red]Error ({event.phase}): {event.message}[/red]")

            asyncio.run(_run())

        finally:
            signal.signal(signal.SIGINT, old_handler)

        result = final_summary
        if final_analysis:
            result["analysis"] = final_analysis
        return result

    def _render_workload_queries(self, console, run_id: str, target: str) -> None:
        """Render top queries table from a saved workload run."""
        from lib.audit_storage import AuditStorage
        from lib.ui import DataTable

        storage = AuditStorage()
        data = storage.load_run(target, run_id)
        if not data:
            return

        queries = data.get("queries", [])
        if not queries:
            console.print("\n[dim]No queries captured.[/dim]")
            return

        duration = data.get("duration_seconds", 0) or 0
        rows = []
        for q in queries[:15]:
            sql = (q.get("normalized_query") or "")[:60]
            calls = q.get("calls", 0)
            row = [
                q.get("query_hash", "")[:8],
                sql,
                str(calls),
            ]
            if duration > 0:
                qps = calls / duration
                if qps >= 1000:
                    row.append(f"{qps/1000:.1f}K")
                else:
                    row.append(f"{qps:.1f}")
            row.extend([
                f"{q.get('total_time_ms', 0):.0f}ms",
                f"{q.get('avg_time_ms', 0):.1f}ms",
                f"{q.get('pct_total_time', 0):.1f}%",
            ])
            rows.append(tuple(row))

        columns = ["Hash", "Query", "Calls"]
        if duration > 0:
            columns.append("QPS")
        columns.extend(["Total", "Avg", "% Load"])
        table = DataTable(
            title=f"Top Queries ({len(queries)} unique, {duration:.0f}s window)",
            columns=columns,
            rows=rows,
        )
        console.print(table)

    @staticmethod
    def _filter_existing_indexes(index_recs: list, analysis: dict) -> list:
        """Remove index recommendations where the column set already exists as an index.

        Uses the schema_context string that was passed to the LLM prompt (stored in
        analysis dict) to deterministically identify existing indexes. This is more
        reliable than trusting the LLM to avoid duplicates.
        """
        schema_text = analysis.get("schema_context", "")
        if not schema_text:
            return index_recs

        # Parse existing indexes from schema context format:
        #   table_name:
        #     Indexes: idx_name(col1, col2), other_idx(col3)
        existing_indexes: dict[str, set[tuple[str, ...]]] = {}
        current_table = None
        for line in schema_text.split("\n"):
            stripped = line.strip()
            # Table header line: "orders:" (not indented field labels)
            if (stripped.endswith(":")
                    and not stripped.startswith("Columns")
                    and not stripped.startswith("Indexes")
                    and not stripped.startswith("Foreign")
                    and not stripped.startswith("NOTE")
                    and not stripped.startswith("==")):
                current_table = stripped[:-1].strip().lower()
            elif stripped.startswith("Indexes:") and current_table:
                idx_text = stripped[len("Indexes:"):].strip()
                # Split on "), " to separate indexes like "idx_a(col1, col2), idx_b(col3)"
                for part in idx_text.split("),"):
                    part = part.strip()
                    paren_start = part.find("(")
                    if paren_start == -1:
                        continue
                    cols_str = part[paren_start + 1:].rstrip(")")
                    cols = tuple(c.strip().lower() for c in cols_str.split(",") if c.strip())
                    if cols:
                        existing_indexes.setdefault(current_table, set()).add(cols)
                        # Composite index prefixes: (a,b,c) covers (a) and (a,b)
                        for prefix_len in range(1, len(cols)):
                            existing_indexes[current_table].add(cols[:prefix_len])

        if not existing_indexes:
            return index_recs

        filtered = []
        for rec in index_recs:
            table = rec.get("table", "").lower()
            rec_cols = tuple(c.strip().lower() for c in rec.get("columns", []))
            if not rec_cols:
                filtered.append(rec)
                continue
            table_indexes = existing_indexes.get(table, set())
            # Skip if exact column set (or reversed) already indexed
            if rec_cols in table_indexes:
                continue
            if tuple(reversed(rec_cols)) in table_indexes:
                continue
            filtered.append(rec)
        return filtered

    @staticmethod
    def _merge_audit_into_capture(target: str, run_id: str, audit_result: dict) -> None:
        """Merge full audit data into the saved capture file so audit show is complete."""
        if not run_id:
            return
        try:
            from lib.audit_storage import AuditStorage
            import json as _json

            storage = AuditStorage()
            path = storage.base_dir / target / f"{run_id}.json"
            if not path.exists():
                return

            with open(path) as f:
                data = _json.load(f)

            # Merge audit-level fields that the capture doesn't have
            for key in ("target_name", "engine", "host", "region", "instance_class",
                        "group", "tags", "metrics", "sizing", "cache_opportunity",
                        "top_queries"):
                if key in audit_result and key not in data:
                    data[key] = audit_result[key]

            with open(path, "w") as f:
                _json.dump(data, f, indent=2, default=str)
        except Exception:
            pass  # Non-critical — don't break the audit flow

    def _render_workload_analysis(self, console, analysis: dict, target_name: str = "") -> None:
        """Render LLM workload analysis to the console."""
        from lib.ui import DataTable

        char = analysis.get("workload_characterization", "")
        score = analysis.get("health_score", 0)
        rw = analysis.get("read_write_ratio", "")

        score_color = "green" if score >= 70 else "yellow" if score >= 40 else "red"

        console.print()
        console.print(f"[bold]Workload Analysis[/bold]")
        console.print(f"  Characterization: [bold]{char}[/bold]")
        console.print(f"  Health Score: [{score_color}]{score}/100[/{score_color}]")
        if rw:
            console.print(f"  Read/Write: {rw}")

        # Readyset recommendation
        rs_rec = analysis.get("readyset_recommendation")
        if rs_rec:
            verdict = rs_rec.get("verdict", "unknown")
            verdict_colors = {
                "strong_candidate": "green bold",
                "good_candidate": "green",
                "marginal": "yellow",
                "not_recommended": "red",
            }
            vc = verdict_colors.get(verdict, "dim")
            console.print(
                f"\n[bold]Readyset Assessment:[/bold] [{vc}]{verdict.upper().replace('_', ' ')}[/{vc}]"
            )
            summary = rs_rec.get("summary", "")
            if summary:
                console.print(f"  {summary}")
            cacheable_pct = rs_rec.get("estimated_cacheable_pct")
            if cacheable_pct is not None:
                console.print(f"  Estimated cacheable queries: {cacheable_pct}%")
            reasons = rs_rec.get("key_reasons", [])
            if reasons:
                for r in reasons:
                    console.print(f"  - {r}")

        # Bottlenecks
        bottlenecks = analysis.get("top_bottlenecks", [])
        if bottlenecks:
            from lib.ui import StyledPanel
            lines = []
            for b in bottlenecks:
                rank = b.get("rank", "?")
                cat = b.get("category", "")
                impact = b.get("impact", "?")
                desc = b.get("description", "")
                rec = b.get("recommendation", "")
                lines.append(f"[bold]{rank}. {cat}[/bold] (impact: {impact})")
                lines.append(f"  {desc}")
                lines.append(f"  [dim]Recommendation: {rec}[/dim]")
                lines.append("")
            # Add analyze breadcrumbs for affected queries
            if target_name:
                seen_hashes = []
                for b in bottlenecks:
                    for h in b.get("affected_queries", []):
                        if h not in seen_hashes:
                            seen_hashes.append(h)
                if seen_hashes:
                    lines.append("[dim]Deep dive into these queries:[/dim]")
                    for h in seen_hashes[:3]:
                        lines.append(f"  [dim]rdst analyze --target {target_name} --hash {h[:8]}[/dim]")
            console.print(StyledPanel("\n".join(lines), title="Top Bottlenecks"))

        # Index recommendations (with post-processing to filter already-existing indexes)
        indexes = analysis.get("index_recommendations", [])
        if indexes:
            # Filter out recommendations for indexes that already exist
            indexes = self._filter_existing_indexes(indexes, analysis)

        if indexes:
            from lib.ui import StyledPanel
            lines = []
            for i, idx in enumerate(indexes, 1):
                table_name = idx.get("table", "?")
                cols = ", ".join(idx.get("columns", []))
                idx_type = idx.get("type", "btree")
                reason = idx.get("reason", "")
                impact = idx.get("estimated_impact", "")
                create_sql = idx.get("create_index_sql", "")
                if not create_sql:
                    # Generate CREATE INDEX if LLM didn't provide it
                    safe_cols = ", ".join(c.strip() for c in idx.get("columns", []))
                    idx_name = "idx_" + "_".join(c.strip().replace(" ", "_") for c in idx.get("columns", []))
                    create_sql = f"CREATE INDEX {idx_name} ON {table_name}({safe_cols})"
                lines.append(f"[bold]{i}. {table_name}[/bold] ({cols}) [{idx_type}]")
                lines.append(f"  {reason}")
                if impact:
                    lines.append(f"  [dim]Impact: {impact}[/dim]")
                lines.append(f"  [cyan]{create_sql}[/cyan]")
                lines.append("")
            console.print(StyledPanel("\n".join(lines), title="Index Recommendations"))

        # Caching candidates
        caching = analysis.get("caching_candidates", [])
        if caching:
            from lib.ui import StyledPanel
            lines = []
            for c in caching:
                qhash = c.get("query_hash", "?")[:12]
                calls = c.get("calls", "")
                reason = c.get("reason", "")
                benefit = c.get("estimated_benefit", "")
                lines.append(f"[bold]{qhash}[/bold] — {calls} calls")
                lines.append(f"  {reason}")
                if benefit:
                    lines.append(f"  [dim]Benefit: {benefit}[/dim]")
                lines.append("")
            console.print(StyledPanel("\n".join(lines), title="Readyset Caching Candidates"))

        # Not cacheable
        not_cacheable = analysis.get("not_cacheable", [])
        if not_cacheable:
            rows = []
            for nc in not_cacheable:
                rows.append((
                    nc.get("query_hash", "?"),
                    nc.get("reason", ""),
                ))
            table = DataTable(
                title="Not Cacheable by Readyset",
                columns=["Query Hash", "Reason"],
                rows=rows,
            )
            console.print(table)

        # Optimization priorities
        priorities = analysis.get("optimization_priorities", [])
        if priorities:
            from lib.ui import StyledPanel
            lines = []
            for p in priorities:
                pri = p.get("priority", "?")
                cat = p.get("category", "")
                action = p.get("action", "")
                effort = p.get("effort", "?")
                impact = p.get("impact", "?")
                details = p.get("details", "")
                lines.append(f"[bold]{pri}. {action}[/bold] [{cat}]")
                if details:
                    lines.append(f"  {details}")
                lines.append(f"  [dim]Effort: {effort} | Impact: {impact}[/dim]")
                lines.append("")
            console.print(StyledPanel("\n".join(lines), title="Optimization Priorities"))

        # Analyze breadcrumb at the end
        if target_name:
            # Collect top query hashes from caching candidates (most impactful)
            top_hashes = []
            for c in analysis.get("caching_candidates", []):
                h = c.get("query_hash", "")
                if h and h not in top_hashes:
                    top_hashes.append(h)
            if top_hashes:
                console.print(f"\n[dim]Analyze top queries in depth:[/dim]")
                for h in top_hashes[:3]:
                    console.print(f"  [dim]rdst analyze --target {target_name} --hash {h[:8]}[/dim]")

    def _render_result(self, console, result: dict) -> None:
        """Render a single audit result to the console."""
        from lib.ui import DataTable

        target = result.get("target_name", "unknown")
        engine = result.get("engine", "")
        host = result.get("host", "")
        metrics = result.get("metrics") or {}
        sizing = result.get("sizing") or {}
        cache_opp = result.get("cache_opportunity") or {}

        # Header
        console.print(f"\n[bold]Audit: {target}[/bold] ({engine}, {host})")

        # Metrics table
        metric_rows = []
        if metrics.get("server_version"):
            metric_rows.append(("Server Version", metrics["server_version"]))
        if metrics.get("database_size_mb"):
            size_str = f"{metrics['database_size_mb']:.1f} MB"
            if metrics.get("storage_allocated_gb"):
                size_str += f" / {metrics['storage_allocated_gb']:.0f} GB allocated"
                if metrics.get("storage_used_pct") is not None:
                    size_str += f" ({metrics['storage_used_pct']:.1f}% used)"
                if metrics.get("storage_type"):
                    size_str += f" [{metrics['storage_type']}]"
            metric_rows.append(("Database Size", size_str))
        metric_rows.append((
            "Connections",
            f"{metrics.get('active_connections', 0)} active / "
            f"{metrics.get('max_connections', 0)} max "
            f"({metrics.get('connection_utilization_pct', 0):.1f}%)",
        ))
        if metrics.get("cache_hit_rate"):
            metric_rows.append(("Buffer Cache Hit Rate", f"{metrics['cache_hit_rate']:.2f}%"))
        if metrics.get("shared_buffers_mb"):
            metric_rows.append(("Shared Buffers", f"{metrics['shared_buffers_mb']:.0f} MB"))
        if metrics.get("read_pct") or metrics.get("write_pct"):
            metric_rows.append((
                "Read/Write",
                f"{metrics.get('read_pct', 0):.1f}% read / {metrics.get('write_pct', 0):.1f}% write",
            ))
        if metrics.get("is_replica"):
            lag = metrics.get("replication_lag_seconds")
            lag_str = f"{lag:.1f}s" if lag is not None else "unknown"
            metric_rows.append(("Replica", f"Yes (lag: {lag_str})"))
        if metrics.get("tracked_query_count"):
            metric_rows.append(("Tracked Queries", str(metrics["tracked_query_count"])))
        else:
            engine = result.get("engine", "")
            if engine == "postgresql":
                metric_rows.append(("Tracked Queries", "[yellow]0 — pg_stat_statements not enabled or no activity[/yellow]"))
            else:
                metric_rows.append(("Tracked Queries", "[yellow]0 — performance_schema may not be tracking queries[/yellow]"))

        if metric_rows:
            table = DataTable(
                title="Metrics",
                columns=["Metric", "Value"],
                rows=metric_rows,
            )
            console.print(table)

        # Sizing verdict
        verdict = sizing.get("verdict", "unknown")
        verdict_colors = {
            "under_provisioned": "red",
            "oversized": "yellow",
            "right_sized": "green",
            "unknown": "dim",
        }
        color = verdict_colors.get(verdict, "dim")
        console.print(f"\n[bold]Sizing:[/bold] [{color}]{verdict.upper().replace('_', ' ')}[/{color}]")
        if sizing.get("explanation"):
            console.print(f"  [dim]{sizing['explanation']}[/dim]")
        if sizing.get("current_monthly_cost_usd"):
            console.print(f"  [dim]Estimated cost: ${sizing['current_monthly_cost_usd']:.2f}/month[/dim]")
        if sizing.get("potential_savings_usd"):
            console.print(
                f"  [green]Potential savings: ${sizing['potential_savings_usd']:.2f}/month "
                f"(downsize to {sizing.get('suggested_instance_class')})[/green]"
            )

        # Cache opportunity
        score = cache_opp.get("score", 0)
        level = cache_opp.get("level", "low")
        level_colors = {"high": "green", "medium": "yellow", "low": "dim"}
        lcolor = level_colors.get(level, "dim")
        console.print(f"\n[bold]Cache Opportunity:[/bold] [{lcolor}]{score}/100 ({level})[/{lcolor}]")
        if cache_opp.get("explanation"):
            console.print(f"  [dim]{cache_opp['explanation']}[/dim]")

        # Top queries table
        top_queries = result.get("top_queries", [])
        if top_queries:
            has_qps = top_queries[0].get("qps") is not None
            rows = []
            for q in top_queries[:15]:
                sql = (q.get("normalized_query") or "")[:60]
                row = [
                    q.get("query_hash", "")[:8],
                    sql,
                    str(q.get("calls", 0)),
                ]
                if has_qps:
                    qps = q.get("qps", 0) or 0
                    if qps >= 1000:
                        row.append(f"{qps/1000:.1f}K")
                    else:
                        row.append(f"{qps:.1f}")
                row.extend([
                    f"{q.get('total_time_ms', 0):.0f}ms",
                    f"{q.get('avg_time_ms', 0):.1f}ms",
                    f"{q.get('pct_total_time', 0):.1f}%",
                ])
                rows.append(tuple(row))
            columns = ["Hash", "Query", "Calls"]
            if has_qps:
                columns.append("QPS")
            columns.extend(["Total", "Avg", "% Load"])
            table = DataTable(
                title=f"Top Queries ({len(top_queries)} tracked)",
                columns=columns,
                rows=rows,
            )
            console.print(table)

            # Truncation warning — detect queries actually cut off mid-statement
            truncated = 0
            for q in top_queries:
                sql = (q.get("query_text") or q.get("normalized_query") or "").rstrip()
                if len(sql) < 1020:
                    continue
                # Legitimate endings: ?, ), *, number, keyword, identifier
                last_char = sql[-1] if sql else ""
                if last_char in ("?", ")", "*", "`", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9"):
                    continue
                # Ends with a complete word/keyword
                last_token = sql.split()[-1] if sql.split() else ""
                bare_token = last_token.strip("`\"'")
                if bare_token.upper() in ("DESC", "ASC", "LIMIT", "NULL", "TRUE", "FALSE", "ALL", "ID"):
                    continue
                # Ends mid-word or with comma/dot — likely truncated
                truncated += 1
            if truncated > 0:
                engine = result.get("engine", "")
                if "mysql" in engine.lower():
                    console.print(
                        f"[yellow]{truncated} queries are truncated. "
                        f"To get full text, set performance_schema_max_digest_length = 16384 and "
                        f"max_digest_length = 16384 in the DB parameter group, restart the instance, "
                        f"then CALL sys.ps_truncate_all_tables(FALSE) to clear old entries.[/yellow]"
                    )
                else:
                    console.print(
                        f"[yellow]{truncated} queries are truncated. "
                        f"Check track_activity_query_size in postgresql.conf.[/yellow]"
                    )

        elif not metrics.get("tracked_query_count"):
            # Warning if no query stats
            engine = result.get("engine", "")
            if engine == "postgresql":
                console.print(
                    f"\n[yellow bold]Warning:[/yellow bold] [yellow]pg_stat_statements is not enabled. "
                    f"Without it, we cannot see which queries are running or how they perform. "
                    f"Enable it by adding 'pg_stat_statements' to shared_preload_libraries "
                    f"and restarting PostgreSQL.[/yellow]"
                )
            else:
                console.print(
                    f"\n[yellow bold]Warning:[/yellow bold] [yellow]No query statistics found in "
                    f"performance_schema. Ensure performance_schema is enabled and queries "
                    f"are being tracked. Without this, we have limited visibility into "
                    f"database activity.[/yellow]"
                )

        # Next steps
        if score >= 70 and top_queries:
            console.print(
                f"\n[bold green]This database is a strong Readyset candidate.[/bold green]"
            )
            console.print(
                f"[dim]For deeper analysis: rdst audit --target {target} --duration 5m[/dim]"
            )
        else:
            console.print(f"\n[dim]Next: rdst top --target {target}  (find slow queries)[/dim]")
