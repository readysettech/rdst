"""Audit command — single-target deep health audit."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import time

# Suppress noisy sqlglot warnings during query normalization
logging.getLogger("sqlglot").setLevel(logging.ERROR)

from shared.anthropic_env import has_anthropic_api_key
from shared.cli.types import RdstResult
from shared.json_parse import parse_llm_json
from shared.query_registry import QueryRegistry
from shared.ui import ElapsedMessage, Status, get_console

from features.audit.capture_service import CaptureService, _parse_duration
from features.audit.service import AuditService
from features.audit.storage import AuditStorage
from features.fleet import SnapshotStore, build_single_target_insights_prompt


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
        verbose = getattr(args, "verbose", False)
        auto_yes = getattr(args, "auto_yes", False)
        # --verbose is the only way to opt out of email — terminal-only mode.
        # No standalone --no-email flag exists.
        no_email = verbose

        service = AuditService()
        console = get_console()

        # Validate target exists before expensive LLM checks so
        # "not found" errors surface immediately.
        from shared.config.targets import TargetsConfig as _TC
        _cfg = _TC(); _cfg.load()
        target_config = _cfg.get(target)
        if not target_config:
            return RdstResult(False, f"Target '{target}' not found")

        if insights:
            if not has_anthropic_api_key():
                console.print(
                    "[yellow]No LLM API key configured. The audit report requires AI analysis.[/yellow]\n"
                    "[dim]Set up your LLM provider now:[/dim]\n"
                )
                try:
                    from features.configure.cli.wizard import ConfigurationWizard
                    wizard = ConfigurationWizard()
                    from shared.config.targets import TargetsConfig as _TC
                    _cfg = _TC(); _cfg.load()
                    wizard.configure_llm(_cfg, {})
                    _cfg.save()
                    # Re-check after configure
                    if not has_anthropic_api_key():
                        return RdstResult(False, "LLM key still not set. Run: rdst configure llm")
                except (EOFError, KeyboardInterrupt):
                    return RdstResult(False, "LLM setup cancelled. Run: rdst configure llm")
                except Exception as e:
                    return RdstResult(
                        False,
                        f"Could not launch LLM setup: {e}\n"
                        "Run manually: rdst configure llm",
                    )
            try:
                from shared.llm_manager import LLMManager
                llm = LLMManager()
                llm.generate_response("Say OK", max_tokens=1, temperature=0.0)
            except Exception as e:
                return RdstResult(
                    False,
                    f"ANTHROPIC_API_KEY is invalid or the API is unreachable: {e}\n"
                    "Fix the key and re-run.",
                )
        # Run audit_target directly with a spinner callback for live progress.
        from dataclasses import asdict as _asdict

        if not output_json:
            console.print(f"[dim]Auditing {target}...[/dim]")

        _audit_spinner = [None]  # mutable so callback can replace it

        def _on_progress(msg):
            if output_json:
                return
            if _audit_spinner[0]:
                _audit_spinner[0].stop()
            _audit_spinner[0] = Status(
                ElapsedMessage(msg, time.monotonic()),
                spinner="dots",
                console=console,
            )
            _audit_spinner[0].start()

        result = service.audit_target(target, target_config, on_progress=_on_progress)
        if _audit_spinner[0]:
            _audit_spinner[0].stop()

        if result.error:
            if not output_json:
                console.print(f"[red]Error: {result.error}[/red]")
            return RdstResult(False, result.error)

        final_result = _asdict(result)

        if not final_result:
            return RdstResult(False, "Audit failed")

        # Live workload capture if --duration was provided
        duration_str = getattr(args, "duration", None)
        workload_result = None
        if duration_str and final_result:
            # Two parallel flows:
            #   Thread 1 (background): health LLM — uses metrics/health data already collected
            #   Main thread: duration capture + workload LLM — runs the 30s window
            # Health LLM overlaps with the capture window for free.
            import threading
            health_llm_result: list = [None]  # mutable container for thread result

            def _run_health_llm():
                health_llm_result[0] = service.run_health_llm(final_result)

            health_thread = threading.Thread(target=_run_health_llm, daemon=True)
            health_thread.start()

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

            # Wait for health LLM to finish (should be done by now — capture takes 30s)
            health_thread.join(timeout=30)
            if health_llm_result[0]:
                final_result["health_analysis"] = health_llm_result[0]

            if workload_result:
                final_result["workload"] = workload_result

                if not getattr(args, "no_save", False):
                    self._merge_audit_into_capture(
                        target, workload_result.get("run_id", ""), final_result
                    )
        else:
            # No duration — run health LLM synchronously
            if not output_json:
                _on_progress("Analyzing health data...")
            ha = service.run_health_llm(final_result)
            if ha:
                final_result["health_analysis"] = ha
            if _audit_spinner[0]:
                _audit_spinner[0].stop()
                _audit_spinner[0] = None

        # Save discovered queries to registry
        top_queries = final_result.get("top_queries", [])
        if top_queries and not no_save:
            try:
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

        # Run Readyset cache testing on captured queries (PLG Advanced Flow)
        rs_results = None
        if workload_result and workload_result.get("queries"):
            try:
                rs_results = self._run_readyset_testing(
                    console, target, workload_result.get("queries") or [],
                    auto_yes=auto_yes,
                )
                if rs_results:
                    final_result["readyset_results"] = rs_results
            except Exception:
                pass

        # Re-compute sizing with workload intensity data (if capture was run)
        if workload_result:
            try:
                from features.audit.scoring import compute_sizing_verdict
                from features.audit.models import AuditMetrics
                metrics_obj = final_result.get("metrics") or {}
                if metrics_obj:
                    # Rebuild AuditMetrics from dict
                    am = AuditMetrics(**{
                        k: v for k, v in metrics_obj.items()
                        if k in AuditMetrics.__dataclass_fields__
                    })
                    new_sizing = compute_sizing_verdict(
                        am,
                        instance_class=final_result.get("instance_class"),
                        workload_total_time_ms=workload_result.get("total_query_time_ms"),
                        workload_duration_seconds=workload_result.get("duration_seconds"),
                    )
                    from dataclasses import asdict as _asdict
                    final_result["sizing"] = _asdict(new_sizing)
            except Exception:
                pass

        if output_json:
            print(json.dumps(final_result, indent=2, default=str))
            return RdstResult(True, message="")

        # Verbose: print full terminal report. Default: print compact summary
        # (the full report goes via email).
        if verbose:
            self._render_terminal_report(console, target, final_result, workload_result, rs_results)
        else:
            self._render_summary(
                console, target, final_result,
                workload_result,
                (workload_result or {}).get("analysis"),
            )

        # Verbose mode already printed everything via _render_terminal_report.
        # Always save audit result (unless --no-save)
        if not no_save and final_result:
            import datetime as _dt
            auto_name = save_name or f"audit_{target}_{_dt.datetime.now():%Y%m%d_%H%M%S}"
            store = SnapshotStore()
            path = store.save_raw(auto_name, final_result)
            if not output_json:
                console.print(f"\n[dim]View locally: rdst audit show {auto_name}[/dim]")
                console.print(f"[dim]View past audits: rdst audit list[/dim]")

                # Note: ephemeral_lifecycle in _run_readyset_testing has already
                # torn down any container we deployed for this audit. Containers
                # that pre-existed the audit are left running. So we no longer
                # tell the user "container is still running" — the answer
                # depends on whether they had one before, which is fine.
                if rs_results:
                    import subprocess
                    container_name = f"rdst-readyset-{target}"
                    try:
                        r = subprocess.run(
                            ["docker", "inspect", "--format", "{{.State.Running}}", container_name],
                            capture_output=True, text=True, timeout=3,
                        )
                        still_running = r.returncode == 0 and r.stdout.strip().lower() == "true"
                    except Exception:
                        still_running = False
                    if still_running:
                        # Container survived audit — it was pre-existing.
                        console.print(
                            f"\n[dim]Readyset cache for [bold]{target}[/bold] was already deployed and is still running.\n"
                            f"  Stop it with:  rdst cache stop --target {target}\n"
                            f"  Remove fully:  rdst cache remove --target {target}[/dim]"
                        )

        # LLM insights for non-verbose, no-duration case (legacy single-target prompt).
        # Verbose mode already showed everything via _render_terminal_report.
        has_workload_analysis = workload_result and workload_result.get("analysis")
        if insights and final_result and not has_workload_analysis and not verbose:
            spinner = Status(
                ElapsedMessage(f"Generating insights for {target}...", time.monotonic()),
                spinner="dots",
                console=console,
            )
            spinner.start()
            try:
                from shared.llm_manager import LLMManager

                prompt = build_single_target_insights_prompt(final_result)
                llm = LLMManager()
                result = llm.generate_response(prompt, max_tokens=4096, temperature=0.0)
                spinner.stop()
                insights_text = result.get("response", "")
                self._render_audit_insights(console, insights_text)
            except Exception as e:
                spinner.stop()
                console.print(f"[red]LLM insights failed: {e}[/red]")

        # Default flow: generate HTML, save locally, email. Skipped under --verbose.
        # Also skipped when the per-target health LLM errored — the report would
        # be missing its core narrative, score, and findings, so we don't ship it.
        ha = final_result.get("health_analysis") or {}
        llm_failed = bool(ha.get("error")) or (insights and not ha.get("health_score"))
        if llm_failed and not output_json:
            console.print(
                "[yellow]Skipping report generation — health analysis LLM did not return usable output.\n"
                "Audit data still saved; re-run with a working LLM key to generate the report.[/yellow]"
            )
        if not verbose and not llm_failed:
            insights_data = (workload_result or {}).get("analysis")
            html_content = self._generate_html_report(
                final_result,
                insights_data=insights_data,
                workload=workload_result,
                rs_results=rs_results,
            )
            if html_content and final_result:
                import datetime as _dt
                snapshot_id = save_name or f"audit_{target}_{_dt.datetime.now():%Y%m%d_%H%M%S}"
                self._save_report_locally(snapshot_id, html_content)
                try:
                    self._email_report(console, html_content, target, snapshot_id=snapshot_id)
                except Exception as e:
                    console.print(f"  [yellow]Email delivery failed: {e}[/yellow]")
                    console.print(f"  [dim]View locally: rdst audit show {snapshot_id}[/dim]")
                try:
                    self._track_audit_report(target, final_result, insights_data, workload_result)
                except Exception:
                    pass
        else:
            try:
                insights_data = (workload_result or {}).get("analysis")
                self._track_audit_report(target, final_result, insights_data, workload_result)
            except Exception:
                pass

        return RdstResult(True, message="")

    def _render_terminal_report(
        self,
        console,
        target: str,
        result: dict,
        workload_result: dict | None,
        rs_results: list | None,
    ) -> None:
        """Verbose terminal report — full details, no email needed.

        Mirrors the 3-section HTML layout but uses Rich panels and inline
        command hints (rdst analyze --hash X / rdst query show X / rdst cache
        deploy --target X) since users see them right here.
        """
        from shared.ui import StyledPanel, DataTable

        m = result.get("metrics") or {}
        sizing = result.get("sizing") or {}
        cache_opp = result.get("cache_opportunity") or {}
        ha = result.get("health_analysis") or {}
        wl = workload_result or {}
        captured = wl.get("queries") or []

        # ── Hero block ─────────────────────────────────────────────────
        console.print()
        console.print(f"[bold]RDST Audit Report[/bold]  ·  [bold cyan]{target}[/bold cyan]")
        engine = result.get("engine", "unknown")
        version = (m.get("server_version") or "").split(",")[0]
        cls = result.get("instance_class") or "—"
        region = result.get("region") or "—"
        console.print(f"[dim]{engine} · {version} · {cls} · {region}[/dim]")

        score = ha.get("health_score")
        label = (ha.get("health_label") or "").upper()
        if score is not None:
            try:
                s = int(score)
                col = "green" if s >= 75 else ("cyan" if s >= 60 else ("yellow" if s >= 40 else "red"))
                console.print(f"\n[bold]Health Score:[/bold] [{col}]{s}/100 {label}[/{col}]")
                if ha.get("health_score_rationale"):
                    console.print(f"  [dim]{ha.get('health_score_rationale')}[/dim]")
            except (TypeError, ValueError):
                pass

        # Key stats row
        size_mb = m.get("database_size_mb") or 0
        size_str = f"{size_mb/1024:.1f} GB" if size_mb >= 1024 else f"{int(size_mb)} MB"
        cache_hit = m.get("cache_hit_rate") or 0
        rw = f"{int(m.get('read_pct') or 0)}% reads / {int(m.get('write_pct') or 0)}% writes"
        captured_calls = sum(int(q.get("calls") or 0) for q in captured)
        dur = wl.get("duration_seconds") or 0
        qps = (captured_calls / dur) if dur else 0
        console.print(
            f"\n[dim]Size:[/dim] {size_str}  |  "
            f"[dim]Buffer Cache Hit:[/dim] {cache_hit:.1f}%  |  "
            f"[dim]R/W:[/dim] {rw}"
        )
        if dur:
            qps_str = f"{qps:.1f}" if qps < 10 else f"{qps:.0f}"
            console.print(
                f"[dim]Captured:[/dim] {captured_calls:,} executions across "
                f"{len(captured)} unique queries  |  [dim]QPS:[/dim] {qps_str}"
            )

        # Sizing + savings
        verdict = (sizing.get("verdict") or "unknown").replace("_", " ").upper()
        cost = sizing.get("current_monthly_cost_usd")
        rs_cost = sizing.get("readyset_projected_cost_usd")
        rs_save = sizing.get("readyset_projected_savings_usd")
        if cost:
            console.print(f"[dim]Sizing:[/dim] [bold]{verdict}[/bold]  ·  ${int(cost):,}/mo current")
            if rs_save and rs_save > 0 and rs_cost:
                pct = int(rs_save / cost * 100) if cost else 0
                console.print(
                    f"[dim]Estimated Savings:[/dim] [green]${int(rs_save):,}/mo[/green] "
                    f"({pct}% reduction with caching → {sizing.get('readyset_projected_class','—')})"
                )

        # ── Top findings ───────────────────────────────────────────────
        sev_color = {"crit": "red", "warn": "yellow", "info": "cyan", "ok": "green"}
        top_findings = ha.get("top_findings") or []
        if top_findings:
            console.print("\n[bold]Top Findings[/bold]")
            for f in top_findings[:3]:
                sev = (f.get("severity") or "info").lower()
                col = sev_color.get(sev, "dim")
                console.print(f"  [{col}]●[/{col}] [bold]{f.get('title','')}[/bold]")
                if f.get("body"):
                    console.print(f"    [dim]{f.get('body')}[/dim]")

        # ── Detailed findings ──────────────────────────────────────────
        findings = ha.get("findings") or []
        if findings:
            console.print("\n[bold]Findings[/bold]")
            for f in findings:
                sev = (f.get("severity") or "info").lower()
                col = sev_color.get(sev, "dim")
                console.print(f"  [{col}]●[/{col}] [bold]{f.get('title','')}.[/bold] {f.get('body','')}")

        # ── Captured query table ───────────────────────────────────────
        if captured:
            rs_by_hash = {}
            for r in (rs_results or []):
                h = r.get("query_hash") or r.get("hash")
                if h:
                    rs_by_hash[h[:12]] = r
            has_rs = any(rs_by_hash.values())

            cols = ["#", "Hash", "Calls", "Avg", "Load %"]
            if has_rs:
                cols += ["Cached", "Speedup"]
            rows = []
            for i, q in enumerate(captured[:15], 1):
                qhash = (q.get("query_hash") or "")[:12]
                avg = q.get("avg_time_ms") or 0
                pct = q.get("pct_total_time") or 0
                rs = rs_by_hash.get(qhash) or {}
                row = [
                    str(i),
                    qhash[:8],
                    f"{int(q.get('calls') or 0):,}",
                    f"{avg:.1f}ms",
                    f"{pct:.1f}%",
                ]
                if has_rs:
                    rs_avg = rs.get("readyset_ms") or rs.get("avg_rs_ms")
                    speedup = rs.get("speedup") or rs.get("speedup_x") or 0
                    row.append(f"{rs_avg:.1f}ms" if rs_avg else "—")
                    row.append(f"{float(speedup):.1f}x" if speedup else "—")
                rows.append(tuple(row))
            console.print()
            console.print(DataTable(title="Captured Queries", columns=cols, rows=rows))

            console.print("\n[dim]Inspect any query:[/dim]")
            for q in captured[:5]:
                qhash = (q.get("query_hash") or "")[:12]
                console.print(f"  [dim]rdst query show {qhash}[/dim]")
                console.print(f"  [dim]rdst analyze --target {target} --hash {qhash[:8]}[/dim]")

        # ── Index recommendations ──────────────────────────────────────
        wa = wl.get("analysis") or {}
        idx_recs = (wa.get("index_recommendations") or []) or (ha.get("index_suggestions") or [])
        if idx_recs:
            console.print("\n[bold]Index Recommendations[/bold]")
            for rec in idx_recs[:6]:
                table = rec.get("table") or ""
                cols_list = rec.get("columns") or []
                title = f"{table} ({', '.join(cols_list)})" if table and cols_list else (rec.get("sql") or "")
                console.print(f"  [bold]{title}[/bold]")
                if rec.get("reason"):
                    console.print(f"    [dim]{rec.get('reason')}[/dim]")
                sql = rec.get("create_index_sql") or rec.get("sql") or ""
                if sql:
                    console.print(f"    [cyan]{sql}[/cyan]")

        # ── Next steps ─────────────────────────────────────────────────
        next_steps = ha.get("recommended_actions") or []
        if next_steps:
            console.print("\n[bold]Next Steps[/bold]")
            for i, step in enumerate(next_steps, 1):
                rank = step.get("rank") or i
                console.print(f"  [bold cyan]{rank}.[/bold cyan] [bold]{step.get('title','')}.[/bold] {step.get('body','')}")
                for cmd in (step.get("commands") or [])[:4]:
                    console.print(f"     [white]{cmd}[/white]")

        # Cache deploy hint if a strong candidate
        opp_score = int(cache_opp.get("score") or 0)
        if opp_score >= 70:
            console.print()
            console.print(f"[bold green]Strong candidate for Readyset caching.[/bold green]")
            console.print(f"  [dim]rdst cache deploy --target {target}[/dim]")

    def _render_audit_insights(self, console, raw_text: str) -> None:
        """Parse structured JSON insights and render with Rich."""
        import json as _json
        from shared.ui import StyledPanel

        try:
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

    @staticmethod
    def _substitute_params(sql):
        """Replace $1, $2, etc. with sample values so parameterized queries can be executed.

        Returns the substituted SQL, or the original if no params found.
        Returns None if the query is a system/internal query that shouldn't be benchmarked.
        """
        import re

        # Skip system/internal queries (PG + MySQL)
        lower = sql.lower()
        if any(s in lower for s in [
            # PostgreSQL system
            "pg_stat_", "pg_settings", "pg_indexes", "information_schema",
            "pg_catalog", "pg_database_size", "pg_backend_pid",
            # MySQL system
            "performance_schema", "@@",
            "mysql.", "sys.",
        ]):
            return None

        # MySQL DIGEST_TEXT adds spaces around function parens and dot notation
        # that break execution: "SUM ( x )" → "SUM(x)", "`u` . `name`" → "`u`.`name`"
        # Fix these before param substitution.
        result_sql = sql
        result_sql = re.sub(r'(\w)\s+\(', r'\1(', result_sql)      # SUM ( → SUM(
        result_sql = re.sub(r'`\s+\.\s+`', '`.`', result_sql)       # ` . ` → `.`

        if "$" not in result_sql and "?" not in result_sql:
            return result_sql

        # Replace $N parameters with sample values based on context
        def _replace_param(match):
            # Look at surrounding context to guess a reasonable value
            pos = match.start()
            before = sql[max(0, pos - 40):pos].lower()
            if any(k in before for k in ["limit", "offset"]):
                return "10"
            if any(k in before for k in [">", "<", ">=", "<="]):
                return "100"
            if any(k in before for k in ["= '", "like", "ilike"]):
                return "'sample'"
            if "in (" in before:
                return "1"
            if "between" in before:
                return "'2026-01-01'"
            return "1"

        result = re.sub(r'\$\d+', _replace_param, result_sql)
        result = result.replace("?", "1")
        # Replace :p1, :p2 style params too
        result = re.sub(r':p\d+', '1', result)
        return result

    def _run_readyset_testing(self, console, target, queries, max_queries=20, auto_yes=False):
        """Test audit queries against a local Readyset cache.

        Uses ephemeral_lifecycle: if no cache is deployed (or one is stopped),
        prompts the user; on yes, deploys/starts an ephemeral container, runs
        benchmarks, and tears down to original state. Pre-existing running
        caches are reused and left untouched.

        Returns list of {query_hash, query_text, cacheable, not_cacheable_reason,
        baseline_ms, readyset_ms, speedup} or None if RS not available.
        """
        try:
            from features.cache.service import CacheService
            from features.cache.models import CacheInput, CacheOptions
            from shared.deploy.lifecycle import probe, ProbeState, ephemeral_lifecycle
        except ImportError:
            return None

        import asyncio
        import shutil
        import sys

        if not shutil.which("docker"):
            console.print("[dim]  Skipping Readyset cache testing (Docker not installed)[/dim]")
            return None

        # Probe first: only prompt the user if we'll actually need to deploy/start.
        pre = probe(target, mode="docker")

        if pre.state == ProbeState.NOT_DEPLOYED:
            if not auto_yes and not sys.stdin.isatty():
                return None
            if auto_yes:
                console.print("[dim]  Auto-deploying ephemeral Readyset cache (-y)[/dim]")
                consent = True
            else:
                from shared.ui import Confirm
                console.print()
                console.print(
                    "[dim]  Readyset can benchmark your captured queries to show how much"
                    "\n  faster they'd run with caching. We'll deploy a local Docker"
                    "\n  container, run the benchmark, and tear it down when done.[/dim]"
                )
                try:
                    consent = Confirm.ask(
                        "  Deploy Readyset (ephemeral) to test query performance?",
                        default=True,
                    )
                except (EOFError, KeyboardInterrupt):
                    consent = False
            if not consent:
                return None
        elif pre.state == ProbeState.DEPLOYED_STOPPED:
            if not auto_yes and not sys.stdin.isatty():
                return None
            if auto_yes:
                console.print("[dim]  Auto-starting cache for ephemeral benchmark (-y)[/dim]")
                consent = True
            else:
                from shared.ui import Confirm
                console.print()
                console.print(
                    "[dim]  Readyset can benchmark your captured queries to show how much"
                    "\n  faster they'd run with caching. The local cache container is"
                    "\n  stopped — we'll start it for the benchmark and stop it again."
                    "[/dim]"
                )
                try:
                    consent = Confirm.ask(
                        "  Start Readyset cache to test query performance?",
                        default=True,
                    )
                except (EOFError, KeyboardInterrupt):
                    consent = False
            if not consent:
                return None
        elif pre.state == ProbeState.DEPLOYED_RUNNING:
            # No prompt needed — reuse existing container, leave it alone after.
            pass
        else:
            console.print(f"[yellow]  Cache probe inconclusive ({pre.state.value}). Skipping benchmark.[/yellow]")
            return None

        # Single ephemeral_lifecycle wrapping the benchmark. Handles deploy/start
        # entry, wait-for-ready, and full teardown to original state on exit.
        with ephemeral_lifecycle(target, mode="docker") as outcome:
            if outcome.error:
                console.print(f"[yellow]  {outcome.error}[/yellow]")
                return None
            service = CacheService()
            results = self._run_readyset_testing_inner(
                console, target, queries, max_queries, service,
                CacheInput, CacheOptions, asyncio,
            )
            return results

    def _run_readyset_testing_inner(
        self, console, target, queries, max_queries, service,
        CacheInput, CacheOptions, asyncio,
    ):
        """Inner benchmark loop — caller manages container lifecycle."""
        results = []

        # Wait for Readyset to be ready and verify connection
        import time as _t
        _resolved = service._resolve_cache_target(target)
        if not _resolved:
            console.print("[yellow]  Cache target not found after deploy[/yellow]")
            return None
        _cache_name, _cache_cfg = _resolved
        console.print("[dim]  Waiting for Readyset to be ready...[/dim]")
        _ready = False
        _last_err = None
        for _attempt in range(20):
            _t.sleep(1)
            try:
                from shared.db_connection import create_direct_connection
                _test_conn = create_direct_connection(_cache_cfg)
                _test_conn.close()
                _ready = True
                break
            except Exception as e:
                _last_err = e
        if not _ready:
            err_str = str(_last_err)
            if "Access denied" in err_str or "password" in err_str.lower():
                _pw_env = _cache_cfg.get("password_env", "")
                console.print(
                    f"[yellow]  Cannot connect to Readyset cache: {_pw_env} is not set.[/yellow]\n"
                    f"[yellow]  Export it first: export {_pw_env}=\"your_password\"[/yellow]"
                )
            else:
                console.print(f"[yellow]  Readyset not ready after 20s: {_last_err}[/yellow]")
            return None

        # Step 2: Record pre-existing caches
        pre_existing_ids = set()
        try:
            async def _list_caches():
                async for event in service.list_caches(CacheInput(target=target)):
                    if event.type == "cache_list":
                        for c in (event.caches or []):
                            pre_existing_ids.add(c.get("query_id", c.get("id", "")))
            asyncio.run(_list_caches())
        except Exception:
            pass

        # Step 3: Create caches and benchmark
        created_ids = set()
        console.print(f"\n[dim]  Testing {min(len(queries), max_queries)} queries against Readyset...[/dim]")

        for q in queries[:max_queries]:
            sql = q.get("query_text") or q.get("normalized_query", "")
            q_hash = q.get("query_hash", q.get("hash", ""))[:12]
            baseline_ms = q.get("avg_time_ms", 0.0)

            if not sql.strip():
                continue

            # Check cacheability
            try:
                from features.cache.readyset_cacheability import check_readyset_cacheability
                check = check_readyset_cacheability(query=sql)
                if not check.get("cacheable", False):
                    results.append({
                        "query_hash": q_hash,
                        "query_text": sql[:200],
                        "cacheable": False,
                        "not_cacheable_reason": "; ".join(check.get("issues", ["Unknown"])),
                        "baseline_ms": baseline_ms,
                        "readyset_ms": 0,
                        "speedup": 0,
                    })
                    continue
            except Exception:
                pass

            cache_id = None
            cache_create_error = None
            try:
                async def _add_cache():
                    nonlocal cache_id, cache_create_error
                    async for event in service.add_cache(
                        CacheInput(target=target, query=sql),
                        CacheOptions(),
                    ):
                        if event.type == "cache_add":
                            if event.success:
                                cache_id = getattr(event, "cache_id", None) or q_hash
                                if cache_id not in pre_existing_ids:
                                    created_ids.add(cache_id)
                            else:
                                cache_create_error = (
                                    getattr(event, "error", None)
                                    or getattr(event, "message", None)
                                    or "CREATE CACHE failed"
                                )
                asyncio.run(_add_cache())
            except Exception as exc:
                cache_create_error = f"CREATE CACHE raised: {exc}"

            if cache_create_error and cache_id is None:
                results.append({
                    "query_hash": q_hash,
                    "query_text": sql[:200],
                    "cacheable": False,
                    "not_cacheable_reason": cache_create_error,
                    "baseline_ms": baseline_ms,
                    "readyset_ms": 0,
                    "speedup": 0,
                })
                continue

            # Benchmark against Readyset (3 runs: 1 warmup + 2 measured)
            # For parameterized queries, substitute sample values so we can execute them
            readyset_ms = 0.0
            bench_sql = self._substitute_params(sql)
            if not bench_sql:
                # Query was filtered (system query or truncated) — skip with reason
                reason = "system query" if any(s in sql.lower() for s in [
                    "pg_stat_", "performance_schema", "@@", "information_schema",
                ]) else "truncated query"
                results.append({
                    "query_hash": q_hash,
                    "query_text": sql[:200],
                    "cacheable": False,
                    "not_cacheable_reason": reason,
                    "baseline_ms": baseline_ms,
                    "readyset_ms": 0,
                    "speedup": 0,
                })
                continue
            if bench_sql:
                try:
                    cache_target = service._resolve_cache_target(target)
                    if cache_target:
                        cache_name, cache_config = cache_target
                        from shared.db_connection import create_direct_connection
                        conn = create_direct_connection(cache_config)
                        times = []
                        for i in range(3):
                            start = time.perf_counter()
                            try:
                                cursor = conn.cursor()
                                cursor.execute(bench_sql)
                                cursor.fetchall()
                                cursor.close()
                            except Exception:
                                break
                            elapsed_ms = (time.perf_counter() - start) * 1000
                            if i > 0:  # Skip warmup
                                times.append(elapsed_ms)
                        conn.close()
                        if times:
                            readyset_ms = sum(times) / len(times)
                except Exception as e:
                    if not getattr(self, '_rs_conn_error_shown', False):
                        console.print(f"[yellow]  Could not connect to Readyset cache: {e}[/yellow]")
                        self._rs_conn_error_shown = True
                    return None

            speedup = baseline_ms / readyset_ms if readyset_ms > 0 and baseline_ms > 0 else 0.0

            results.append({
                "query_hash": q_hash,
                "query_text": sql[:200],
                "cacheable": True,
                "not_cacheable_reason": "",
                "baseline_ms": baseline_ms,
                "readyset_ms": round(readyset_ms, 3),
                "speedup": round(speedup, 1),
            })

        # CLD-1754: created_ids holds RDST hashes, not Readyset's
        # q_<hash> cache names — DROP fails silently here. Tracked.
        if created_ids:
            for cid in created_ids:
                try:
                    async def _delete(cache_id=cid):
                        async for event in service.delete_cache(CacheInput(target=target, cache_id=cache_id)):
                            pass
                    asyncio.run(_delete())
                except Exception:
                    pass

        cached_count = sum(1 for r in results if r["cacheable"])
        if cached_count > 0:
            speedups = [r["speedup"] for r in results if r["cacheable"] and r["speedup"] > 0]
            avg = sum(speedups) / len(speedups) if speedups else 0
            console.print(f"  [green]{cached_count} queries cached, avg speedup {avg:.1f}x[/green]")
        else:
            console.print(f"  [dim]No queries cacheable[/dim]")

        return results if results else None

    # =========================================================================
    # Summary Mode (default UX)
    # =========================================================================

    def _render_summary(self, console, target, result, workload_result, insights_data):
        """Render slim CLI summary — the default audit output."""
        metrics = result.get("metrics") or {}
        sizing = result.get("sizing") or {}
        cache_opp = result.get("cache_opportunity") or {}
        top_queries = result.get("top_queries") or []

        engine = result.get("engine", "?")
        host = result.get("host", "?")
        version = metrics.get("server_version", "?")
        engine_display = f"{engine}, {version}" if version != "?" else engine

        console.print(f"\n  [bold]Audit: {target}[/bold] ({engine_display})")
        console.print(f"  {'─' * 60}")

        # Core metrics
        size = metrics.get("database_size_mb", 0)
        size_str = f"{size / 1024:.1f} GB" if size >= 1024 else f"{size:.0f} MB"
        conn_str = f"{metrics.get('active_connections', 0)}/{metrics.get('max_connections', 0)} ({metrics.get('connection_utilization_pct', 0):.0f}%)"
        console.print(f"  [dim]Size:[/dim] {size_str}  |  [dim]Connections:[/dim] {conn_str}  |  [dim]Buffer Cache Hit:[/dim] {metrics.get('cache_hit_rate', 0):.1f}%")

        # Sizing verdict
        verdict = sizing.get("verdict", "unknown").replace("_", " ").upper()
        cost = sizing.get("current_monthly_cost_usd")
        savings = sizing.get("potential_savings_usd")
        sizing_str = f"  [dim]Sizing:[/dim] [bold]{verdict}[/bold]"
        if cost and savings and savings > 0:
            pct = (savings / cost) * 100
            sizing_str += f" (caching can save up to {pct:.0f}%)"
        console.print(sizing_str)

        # Cache opportunity
        score = cache_opp.get("score", 0)
        level = cache_opp.get("level", "low")
        console.print(f"  [dim]Cache Opportunity:[/dim] {score}/100 ({level})")

        # R/W ratio
        console.print(f"  [dim]Read/Write:[/dim] {metrics.get('read_pct', 0):.0f}% reads / {metrics.get('write_pct', 0):.0f}% writes")

        # Query count
        console.print(f"  [dim]Top Queries:[/dim] {len(top_queries)} tracked")

        # Readyset verdict (from insights)
        if insights_data and isinstance(insights_data, dict):
            rs_verdict = insights_data.get("readyset_verdict", "")
            if rs_verdict:
                verdict_display = rs_verdict.replace("_", " ").upper()
                console.print(f"\n  [bold]Readyset Verdict:[/bold]   {verdict_display}")
            rs_summary = insights_data.get("readyset_summary", "")
            if rs_summary:
                console.print(f"  {rs_summary}")
            # CLI summary uses percentages per Tanmay's request ($ amounts in the full report only)
            if savings and cost and savings > 0:
                pct = (savings / cost) * 100
                console.print(f"  [bold]Potential Savings:[/bold]  up to {pct:.0f}% with Readyset + rightsizing")

        console.print()

    # =========================================================================
    # HTML Report Generation + Email + Local Save
    # =========================================================================

    def _generate_html_report(self, audit_result, insights_data=None, workload=None, rs_results=None):
        """Generate unified 3-section HTML report for a single target."""
        try:
            from features.audit.report.report import render_report_html

            if hasattr(audit_result, "__dataclass_fields__"):
                from dataclasses import asdict
                audit_result = asdict(audit_result)
            r = dict(audit_result)
            if workload is not None:
                r["workload"] = workload
            if rs_results is not None:
                r["readyset_results"] = rs_results
            return render_report_html([r], fleet_insights=None, title=r.get("target_name"))
        except Exception:
            return None

    def _save_report_locally(self, snapshot_id, html_content):
        """Save HTML report to ~/.rdst/reports/."""
        try:
            from pathlib import Path
            reports_dir = Path.home() / ".rdst" / "reports"
            reports_dir.mkdir(parents=True, exist_ok=True)
            report_path = reports_dir / f"{snapshot_id}.html"
            report_path.write_text(html_content)
        except Exception:
            pass

    def _email_report(self, console, html_content, target, snapshot_id=None):
        """Prompt for email and send the HTML report with verified delivery."""
        from shared.config.targets import TargetsConfig

        cfg = TargetsConfig()
        cfg.load()
        primary_email = cfg.get_email()

        import re
        from shared.ui import Prompt, Confirm
        _email_re = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

        recipient = None
        if primary_email and _email_re.match(primary_email):
            console.print(f"\n  Sending report to [bold]{primary_email}[/bold]")
            try:
                alt = (Prompt.ask(
                    "  Press Enter to continue or type a different email",
                    default="", show_default=False,
                ) or "").strip()
            except (EOFError, KeyboardInterrupt):
                show_cmd = f"rdst audit show {snapshot_id}" if snapshot_id else "rdst audit list"
                console.print(f"\n  [dim]Skipped. View locally: {show_cmd}[/dim]")
                return
            if alt:
                if _email_re.match(alt):
                    recipient = alt
                else:
                    console.print("  [yellow]Invalid email address. Sending to the original.[/yellow]")
                    recipient = primary_email
            else:
                recipient = primary_email
        else:
            console.print(
                "\n  Enter your email to receive the full performance report."
                "\n  Press Ctrl+C to skip.\n"
            )
            while True:
                try:
                    typed = (Prompt.ask("  Email", default="", show_default=False) or "").strip()
                except (EOFError, KeyboardInterrupt):
                    typed = ""
                    break
                if not typed:
                    break
                if _email_re.match(typed):
                    recipient = typed
                    break
                console.print("  [yellow]Invalid email address. Try again or press Ctrl+C to skip.[/yellow]")

            if not recipient:
                show_cmd = f"rdst audit show {snapshot_id}" if snapshot_id else "rdst audit list"
                console.print(f"  [dim]Skipped. View locally: {show_cmd}[/dim]")
                return

        report_token = cfg.get_token_for_email(recipient)

        if not cfg.get_email():
            cfg.set_email(recipient)
            cfg.save()

        try:
            from features.audit.email_service import EmailService
            svc = EmailService()
            subject = f"RDST Audit Report: {target}"

            spinner = None

            def _on_status(msg):
                nonlocal spinner
                spinner = Status(
                    ElapsedMessage(msg, time.monotonic()),
                    spinner="dots",
                    console=console,
                )
                spinner.start()

            send_spinner = Status(
                ElapsedMessage("Sending report...", time.monotonic()),
                spinner="dots",
                console=console,
            )
            send_spinner.start()

            result = svc.send_report_with_verification(
                email=recipient,
                html_body=html_content,
                subject=subject,
                report_token=report_token,
                on_status=_on_status,
                mode="link",
            )

            send_spinner.stop()
            if spinner:
                spinner.stop()

            if result.get("stale_token"):
                cfg.remove_email(recipient)
                cfg.save()

            if result.get("success"):
                new_token = result.get("report_token")
                if new_token:
                    cfg.add_verified_email(recipient, new_token)
                current_primary = cfg.get_email()
                if recipient and recipient != current_primary and current_primary:
                    try:
                        make_default = Confirm.ask(
                            f"  Make [bold]{recipient}[/bold] your default for future audits?",
                            default=False,
                        )
                    except (EOFError, KeyboardInterrupt):
                        make_default = False
                    if make_default:
                        cfg.set_primary_email(recipient)
                cfg.save()
                if result.get("queued"):
                    console.print(
                        f"  [green]Verification email sent to {recipient}[/green]\n"
                        f"  [dim]Click the link in your inbox — your report will arrive automatically.[/dim]"
                    )
                else:
                    console.print(f"  [green]Report link sent to {recipient}[/green]")
            else:
                error = result.get("error", "unknown error")
                console.print(f"  [yellow]{error}[/yellow]")
                show_cmd = f"rdst audit show {snapshot_id}" if snapshot_id else "rdst audit list"
                console.print(f"  [dim]View locally: {show_cmd}[/dim]")
        except Exception as e:
            console.print(f"  [yellow]Email delivery unavailable: {e}[/yellow]")
            show_cmd = f"rdst audit show {snapshot_id}" if snapshot_id else "rdst audit list"
            console.print(f"  [dim]View locally: {show_cmd}[/dim]")

    def _track_audit_report(self, target, result, insights_data, workload_result):
        """Send PostHog events for audit report completion."""
        try:
            from shared.telemetry import telemetry
            from shared.config.targets import TargetsConfig

            cfg = TargetsConfig()
            cfg.load()
            email = cfg.get_email() or "unknown"

            m = (result or {}).get("metrics") or {}
            sizing = (result or {}).get("sizing") or {}
            ha = (result or {}).get("health_analysis") or {}
            wl = workload_result or {}
            captured = len(wl.get("queries") or [])

            properties = {
                "target": target,
                "engine": (result or {}).get("engine", "unknown"),
                "instance_class": (result or {}).get("instance_class"),
                "region": (result or {}).get("region"),
                "email": email,
                "health_score": ha.get("health_score"),
                "health_label": ha.get("health_label"),
                "sizing_verdict": sizing.get("verdict"),
                "current_cost_usd": sizing.get("current_monthly_cost_usd"),
                "potential_savings_usd": sizing.get("readyset_projected_savings_usd") or sizing.get("potential_savings_usd"),
                "cache_opportunity_score": ((result or {}).get("cache_opportunity") or {}).get("score"),
                "captured_queries": captured,
                "has_duration": bool(wl.get("duration_seconds")),
                "has_readyset_testing": bool((result or {}).get("readyset_results")),
                "report_delivery": "link",
                "flow_stage": "advanced",
            }

            # Always fire to PostHog for analytics
            telemetry.track("audit_report_generated", properties)

            # Fire first_audit only once per device (triggers Slack).
            # Include display_name/auth_type/source/email_tier so the Slack
            # template renders consistently with trial events.
            telemetry.track_first_event_once(
                "first_audit",
                flag="first_audit_fired",
                properties={
                    **properties,
                    "display_name": f"First Audit: {target}",
                    "auth_type": "own_key",
                    "source": "audit",
                    "email_tier": "—",
                },
            )
        except Exception:
            pass

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

        from features.audit.storage import list_audit_runs

        runs = list_audit_runs(target=target, limit=limit)

        if output_json:
            print(json.dumps(runs, indent=2))
            return RdstResult(True, message="")

        console = get_console()
        if not runs:
            console.print("[dim]No audit runs found.[/dim]")
            console.print("[dim]Run an audit: rdst audit --target <name>[/dim]")
            return RdstResult(True, message="No runs")

        from shared.ui import DataTable

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
        if runs:
            example_id = runs[0]["run_id"]
            console.print(f"\n[dim]View details: rdst audit show {example_id}[/dim]")
        if len(runs) >= 50:
            console.print(f"[dim]Filter by target: rdst audit list --target <name>[/dim]")
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

        from features.audit.storage import find_audit_run

        target = getattr(args, "target", None)
        data = find_audit_run(run_id, target=target)

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
            from shared.ui import DataTable
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
            show_queries = data.get("queries") or wl.get("queries") or []
            self._render_workload_analysis(console, analysis, target_name=show_target, queries=show_queries)

        return RdstResult(True, message="")

        # Top queries
        queries = data.get("queries", [])
        if queries:
            from shared.ui import DataTable

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
        from shared.ui import DataTable

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
                            f"buffer cache hit {hit:.1f}%, "
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
                                f"buffer cache: {hit:.1f}%, conns: {event.active_connections}, "
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
                        if getattr(event, 'phase', '') == 'preflight':
                            return None  # Abort — no point continuing

            asyncio.run(_run())

        finally:
            signal.signal(signal.SIGINT, old_handler)

        result = final_summary
        if final_analysis:
            result["analysis"] = final_analysis
        return result

    def _render_workload_queries(self, console, run_id: str, target: str) -> None:
        """Render top queries table from a saved workload run."""
        from shared.ui import DataTable

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
        """Wrapper around features.audit.index_dedupe.filter_existing_indexes
        that pulls schema_context out of the analysis dict for back-compat
        with existing call sites.
        """
        from features.audit.index_dedupe import filter_existing_indexes
        return filter_existing_indexes(index_recs, analysis.get("schema_context", ""))

    @staticmethod
    def _merge_audit_into_capture(target: str, run_id: str, audit_result: dict) -> None:
        """Merge full audit data into the saved capture file so audit show is complete."""
        if not run_id:
            return
        try:
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

    def _render_workload_analysis(self, console, analysis: dict, target_name: str = "", queries: list = None) -> None:
        """Render LLM workload analysis to the console."""
        from shared.ui import DataTable

        char = analysis.get("workload_characterization", "")
        rw = analysis.get("read_write_ratio", "")

        console.print()
        console.print(f"[bold]Workload Analysis[/bold]")
        console.print(f"  Characterization: [bold]{char}[/bold]")
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
            from shared.ui import StyledPanel
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
            from shared.ui import StyledPanel
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
            from shared.ui import StyledPanel
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
            from shared.ui import StyledPanel
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

        # Analyze breadcrumb at the end — use hashes from the queries list
        # (registry hashes), NOT from LLM output (which may have stale hashes).
        if target_name:
            top_hashes = []
            if queries:
                # Use the actual query objects (registry hashes)
                for q in queries[:5]:
                    h = q.get("query_hash", "") if isinstance(q, dict) else getattr(q, "query_hash", "")
                    if h and h not in top_hashes:
                        top_hashes.append(h)
            if not top_hashes:
                # Fallback to LLM caching candidates if no queries passed
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
        from shared.ui import DataTable

        target = result.get("target_name", "unknown")
        engine = result.get("engine", "")
        host = result.get("host", "")
        metrics = result.get("metrics") or {}
        sizing = result.get("sizing") or {}
        cache_opp = result.get("cache_opportunity") or {}

        # Header — show "AUDIT SHOW <run_id>" breadcrumb when rendering a stored
        # run, since the user invoked `rdst audit show <id>`. Falls back to the
        # legacy "Audit: <target>" format if no run_id is present (live audit).
        run_id = result.get("run_id") or result.get("snapshot_id") or ""
        if run_id:
            console.print(f"\n[bold]AUDIT SHOW {run_id}[/bold]  ({target}, {engine})")
        else:
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
