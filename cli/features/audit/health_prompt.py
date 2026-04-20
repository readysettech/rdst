"""LLM prompt for the Gautam v3 §2 'Fleet Analysis' style output.

Takes the full HealthReport (§3-§6, §8) plus existing audit metrics / top
queries and asks the LLM to produce:

- A one-paragraph executive summary (like the ibox-info on top of §2)
- A list of findings with severity dots (ok | warn | crit | info)
- A numbered list of recommended actions

The prompt is generic — it describes 'caching' rather than 'Readyset' so the
output matches Gautam's neutral health-tool framing. No "Readyset verdict"
field, no health score, no dollar claims. The existing Readyset-oriented
prompt in audit_prompts.py still runs for duration captures.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


HEALTH_ANALYSIS_PROMPT = """You are a senior database reliability engineer producing a
neutral, vendor-agnostic health assessment of a PostgreSQL or MySQL database.

Your output will render as the 'Fleet Analysis' section of a generic database
health report. It sits alongside SIX data-driven sections the report renders
separately (topology, vacuum/bloat, index health, connections, configuration,
replication, query performance, sizing). Do NOT duplicate the raw data — refer
to it.

TONE RULES
- Neutral and technical. Do not pitch a product. Do not sound like a marketing doc
  or an LLM auto-summary — write like a senior DBRE reviewing real data.
- Discuss "caching" generically when relevant (app cache, materialized views,
  a caching proxy). Do NOT single out any vendor by name.
- No dollar figures. The report's Sizing section owns those.
- No emojis under any circumstances.
- No letter grades outside of the health_label field.
- Base every finding on data in the JSON below. Do not invent numbers.
- Avoid filler words: "leverage", "unlock", "empower", "comprehensive", "holistic",
  "seamless", "robust". Prefer plain verbs.

TARGET
name: {target_name}
engine: {engine}
instance_class: {instance_class}

AUDIT METRICS (summary)
{metrics_block}

SIZING ASSESSMENT
{sizing_block}

CLOUDWATCH CPU (7-day, if available)
{cpu_block}

TOP QUERIES BY TOTAL TIME (from pg_stat_statements / performance_schema)
{top_queries_block}

HEALTH REPORT — SECTIONS §3-§6, §8
The following blob contains vacuum_bloat, index_health, connections,
config_audit, and replication sub-sections. Missing sub-sections are null.
{health_report_json}

TASK
Respond ONLY with valid JSON in this exact shape:

{{
  "health_score": <integer 0-100>,
  "health_label": "CRITICAL|POOR|FAIR|GOOD|EXCELLENT",
  "health_score_rationale": "One sentence explaining the score (e.g., 'Buffer cache is perfect and no critical risks, but 3 unused indexes and moderate bloat warrant attention.')",
  "executive_summary": "One paragraph (3-5 sentences) characterizing the
    database's overall health, workload shape, and the most load-bearing
    observation. Reference concrete numbers from the data above. Do NOT include the
    health score here — it is rendered separately.",
  "top_findings": [
    {{
      "severity": "crit|warn|info",
      "title": "Short bold title (<=6 words)",
      "body": "One sentence hook that teases the detail in findings below"
    }}
  ],
  "findings": [
    {{
      "severity": "ok|warn|crit|info",
      "title": "Short bold title (<=6 words)",
      "body": "1-2 sentence explanation with specific numbers from the data"
    }}
  ],
  "recommended_actions": [
    {{
      "rank": 1,
      "title": "Imperative-form action title (<=8 words)",
      "body": "2-3 sentences: what to do, why, and expected outcome"
    }}
  ],
  "index_suggestions": [
    {{
      "sql": "CREATE INDEX CONCURRENTLY idx_name ON table (col1, col2);",
      "reason": "Short reason tied to a captured query or finding"
    }}
  ],
  "query_commentary": [
    {{
      "query_hash": "8-char hash from the top queries block",
      "observation": "One sentence on why this query matters — repetition, latency, or optimization angle"
    }}
  ]
}}

HEALTH SCORE GUIDANCE
- 90-100 EXCELLENT: no warnings, cache hit >98%, no wraparound risk, healthy replication
- 75-89 GOOD: minor warnings only (unused indexes, slightly off config)
- 60-74 FAIR: moderate warnings (bloat on one+ table, sub-optimal config, idle-in-tx)
- 40-59 POOR: at least one crit or multiple warns (high TXID age, broken replication, autovacuum off)
- <40 CRITICAL: imminent risk (TXID >50%, autovacuum disabled + active bloat, replication broken)

TOP FINDINGS GUIDANCE (separate from findings array)
- Exactly 2-3 items — the 2 or 3 most load-bearing observations
- Rendered as hero-level callouts above the detailed findings
- Must be the most surprising or highest-impact observations from the data

FINDINGS GUIDANCE
- 4-8 findings total. Balance severities — not all 'warn'.
- Prefer findings that are SURPRISING or LOAD-BEARING over restating visible data.
- 'crit' only for genuine risk (TXID wraparound >50%, idle-in-tx over 5 min
  holding locks, replication broken, autovacuum disabled).
- 'warn' for sub-optimal config, unused indexes, moderate bloat, high max_connections.
- 'ok' reinforces something done well (perfect cache hit, clean vacuum state,
  healthy replication lag). Include at least one 'ok' when warranted.
- 'info' for observations that aren't problems but inform decisions
  (workload shape, query repetition, read/write ratio).

ACTIONS GUIDANCE
- 3-5 actions, ranked by impact.
- Each must tie to a finding above.
- Include CREATE INDEX / DROP INDEX / ALTER SYSTEM SQL when directly applicable.
- Frame caching actions generically ("introduce a caching layer — options
  include Redis, materialized views, or a transparent SQL cache").
- Do not include "deploy Readyset" or any product name.

Respond ONLY with valid JSON. No markdown fence. No prose outside the JSON."""


def _truncate(s: str, n: int) -> str:
    if not s:
        return ""
    return s if len(s) <= n else s[: n - 1] + "\u2026"


def _format_metrics(metrics: Dict[str, Any]) -> str:
    if not metrics:
        return "(not collected)"
    keys = [
        "max_connections",
        "active_connections",
        "idle_connections",
        "connection_utilization_pct",
        "cache_hit_rate",
        "shared_buffers_mb",
        "read_pct",
        "write_pct",
        "is_replica",
        "replication_lag_seconds",
        "database_size_mb",
        "server_version",
        "tracked_query_count",
        "total_query_time_ms",
    ]
    lines = []
    for k in keys:
        if k in metrics and metrics[k] not in (None, "", 0.0):
            v = metrics[k]
            if isinstance(v, float):
                lines.append(f"  {k}: {v:.2f}")
            else:
                lines.append(f"  {k}: {v}")
    return "\n".join(lines) or "(no metric values populated)"


def _format_sizing(sizing: Dict[str, Any]) -> str:
    if not sizing:
        return "(not collected)"
    verdict = sizing.get("verdict")
    if hasattr(verdict, "value"):
        verdict = verdict.value
    parts = [f"  verdict: {verdict}"]
    for k in (
        "explanation",
        "current_monthly_cost_usd",
        "suggested_instance_class",
        "concurrent_query_load",
        "estimated_cpu_pct",
    ):
        if sizing.get(k) not in (None, ""):
            parts.append(f"  {k}: {sizing[k]}")
    return "\n".join(parts)


def _format_cpu(cpu: Optional[Dict[str, Any]]) -> str:
    if not cpu:
        return "(not available — non-AWS or no credentials)"
    return (
        f"  avg_cpu: {cpu.get('avg_cpu')}%\n"
        f"  max_cpu: {cpu.get('max_cpu')}%\n"
        f"  min_cpu: {cpu.get('min_cpu')}%\n"
        f"  window_hours: {cpu.get('hours')}"
    )


def _format_top_queries(queries: List[Dict[str, Any]], limit: int = 10) -> str:
    if not queries:
        return "(no query stats available)"
    lines = []
    for i, q in enumerate(queries[:limit]):
        sql = _truncate(q.get("normalized_query") or q.get("query_text") or "", 180)
        lines.append(
            f"  {i+1}. [{(q.get('query_hash') or '?')[:8]}] "
            f"calls={q.get('calls', 0)}, "
            f"avg={q.get('avg_time_ms', 0):.1f}ms, "
            f"pct={q.get('pct_total_time', 0):.1f}%\n"
            f"     {sql}"
        )
    return "\n".join(lines)


def _trim_health_report(report: Dict[str, Any]) -> Dict[str, Any]:
    """Strip overly verbose fields to keep prompt size bounded."""
    if not report:
        return {}
    trimmed: Dict[str, Any] = {}
    for k, v in report.items():
        trimmed[k] = v

    # Index health: cap all_indexes at 40 rows
    ih = trimmed.get("index_health")
    if isinstance(ih, dict):
        all_idx = ih.get("all_indexes") or []
        if len(all_idx) > 40:
            ih["all_indexes"] = all_idx[:40]
            ih["all_indexes_truncated"] = True

    # Vacuum: tables cap at 30
    vb = trimmed.get("vacuum_bloat")
    if isinstance(vb, dict):
        tbls = vb.get("tables") or []
        if len(tbls) > 30:
            vb["tables"] = tbls[:30]
            vb["tables_truncated"] = True

    return trimmed


def build_health_analysis_prompt(
    target_name: str,
    engine: str,
    instance_class: Optional[str],
    health_report: Dict[str, Any],
    metrics: Dict[str, Any],
    sizing: Dict[str, Any],
    cloudwatch_cpu: Optional[Dict[str, Any]] = None,
    top_queries: Optional[List[Dict[str, Any]]] = None,
) -> str:
    trimmed = _trim_health_report(health_report)
    return HEALTH_ANALYSIS_PROMPT.format(
        target_name=target_name,
        engine=engine,
        instance_class=instance_class or "unknown",
        metrics_block=_format_metrics(metrics),
        sizing_block=_format_sizing(sizing),
        cpu_block=_format_cpu(cloudwatch_cpu),
        top_queries_block=_format_top_queries(top_queries or []),
        health_report_json=json.dumps(trimmed, default=str, indent=2)[:20000],
    )


__all__ = ["build_health_analysis_prompt", "HEALTH_ANALYSIS_PROMPT"]
