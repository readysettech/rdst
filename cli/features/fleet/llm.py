"""Fleet-level LLM insights — prompt construction and analysis."""

from __future__ import annotations

from typing import Any


FLEET_INSIGHTS_PROMPT = """You are a database fleet optimization advisor for Readyset, a SQL caching layer.
Analyze these {count} database instances and provide actionable recommendations.

Readyset is a transparent SQL caching proxy that sits between the application and the database.
It accelerates read-heavy workloads by caching query results and keeping them automatically
up-to-date. Always refer to it as "Readyset" (lowercase s). It works best with: read-heavy
workloads (>70% reads), frequently repeated queries, and databases under significant load.

IMPORTANT FACTS ABOUT READYSET:
- The "Cache Hit" percentage in the data below refers to the DATABASE's internal buffer
  cache (shared_buffers for PostgreSQL, InnoDB buffer pool for MySQL), NOT a Readyset cache.
  A high database cache hit rate does NOT mean Readyset wouldn't help — Readyset caches query
  RESULTS at the application layer, which is a different optimization.
- Readyset REQUIRES the upstream database to remain running. It is a cache layer IN FRONT of
  the database, not a replacement. NEVER suggest eliminating, removing, or replacing a database
  with Readyset. Readyset reduces load on the database but the database must always exist.
- Readyset CAN potentially replace read replicas, since it serves cached reads without needing
  a full database copy. But the primary database must always remain.
- Readyset serves cached queries in single-digit milliseconds (sub-10ms). Do NOT claim sub-1ms
  or sub-millisecond latency.
- Most read-only SELECT queries are cacheable by Readyset. Only write queries and genuinely
  non-deterministic queries (NOW(), RANDOM()) are not cacheable.

== FLEET AUDIT SUMMARY ==
{fleet_table}

Respond ONLY with valid JSON matching this exact structure. No markdown, no explanation outside the JSON.

{{
  "fleet_health_summary": "2-3 sentence overall fleet health assessment",
  "per_target": [
    {{
      "target_name": "exact target name from the data",
      "readyset_verdict": "strong_candidate|good_candidate|marginal|not_recommended",
      "readyset_summary": "1-2 sentence explanation of why this verdict",
      "estimated_cacheable_pct": <0-100>,
      "sizing_verdict": "under_provisioned|right_sized|oversized",
      "sizing_recommendation": "1 sentence — what to change or 'No change needed'",
      "key_findings": ["finding 1", "finding 2"]
    }}
  ],
  "immediate_actions": ["action 1 with target name", "action 2"],
  "estimated_monthly_savings_usd": <number or null>,
  "fleet_readyset_summary": "1-2 sentence overall Readyset recommendation for the fleet"
}}"""


SINGLE_TARGET_INSIGHTS_PROMPT = """You are a database optimization advisor for Readyset, a SQL caching layer.
Analyze this single database instance and provide specific, actionable recommendations.

Readyset is a transparent SQL caching proxy that sits between the application and the database.
It accelerates read-heavy workloads by caching query results and keeping them automatically
up-to-date. Always refer to it as "Readyset" (lowercase s).

IMPORTANT FACTS ABOUT READYSET:
- The "Cache Hit Rate" in the metrics below refers to the DATABASE's internal buffer
  cache (shared_buffers for PostgreSQL, InnoDB buffer pool for MySQL), NOT a Readyset cache.
  A high database cache hit rate does NOT mean Readyset wouldn't help — Readyset caches query
  RESULTS at the application layer, which is a different optimization.
- Readyset REQUIRES the upstream database to remain running. It is a cache layer IN FRONT of
  the database, not a replacement. NEVER suggest eliminating, removing, or replacing a database
  with Readyset. Readyset reduces load on the database but the database must always exist.
- Readyset CAN potentially replace read replicas, since it serves cached reads without needing
  a full database copy. But the primary database must always remain.
- Readyset serves cached queries in single-digit milliseconds (sub-10ms). Do NOT claim sub-1ms
  or sub-millisecond latency.
- Most read-only SELECT queries are cacheable by Readyset. Only write queries and genuinely
  non-deterministic queries (NOW(), RANDOM()) are not cacheable.

== DATABASE AUDIT ==
{target_details}

Respond ONLY with valid JSON matching this exact structure. No markdown, no explanation outside the JSON.

{{
  "health_summary": "2-3 sentence health assessment",
  "readyset_verdict": "strong_candidate|good_candidate|marginal|not_recommended",
  "readyset_summary": "1-2 sentence explanation of why this verdict",
  "estimated_cacheable_pct": <0-100>,
  "sizing_verdict": "under_provisioned|right_sized|oversized",
  "sizing_recommendation": "1 sentence — what to change or 'No change needed'",
  "key_findings": ["finding 1", "finding 2", "finding 3"],
  "top_concerns": ["concern 1 or 'None' if healthy"]
}}"""


def build_single_target_insights_prompt(result: dict[str, Any]) -> str:
    """Build prompt for single-target LLM analysis."""
    metrics = result.get("metrics") or {}
    sizing = result.get("sizing") or {}
    cache = result.get("cache_opportunity") or {}

    details = (
        f"Target: {result.get('target_name', '?')}\n"
        f"Engine: {result.get('engine', '?')}\n"
        f"Host: {result.get('host', '?')}\n"
        f"Instance Class: {result.get('instance_class') or 'unknown'}\n"
        f"Region: {result.get('region') or 'unknown'}\n"
        f"\nSizing Verdict: {sizing.get('verdict', 'unknown')}\n"
        f"Explanation: {sizing.get('explanation', '')}\n"
        f"Estimated Cost: ${sizing.get('current_monthly_cost_usd') or '?'}/month\n"
        f"\nServer Version: {metrics.get('server_version', 'unknown')}\n"
        f"Database Size: {metrics.get('database_size_mb', 0):.0f} MB\n"
        f"Connections: {metrics.get('active_connections', 0)} active / {metrics.get('max_connections', 0)} max "
        f"({metrics.get('connection_utilization_pct', 0):.1f}%)\n"
        f"Cache Hit Rate: {metrics.get('cache_hit_rate', 0):.2f}%\n"
        f"Shared Buffers: {metrics.get('shared_buffers_mb', 0):.0f} MB\n"
        f"Read/Write: {metrics.get('read_pct', 0):.0f}% read / {metrics.get('write_pct', 0):.0f}% write\n"
        f"Is Replica: {metrics.get('is_replica', False)}\n"
    )
    if metrics.get("replication_lag_seconds") is not None:
        details += f"Replication Lag: {metrics['replication_lag_seconds']:.1f}s\n"
    tracked = metrics.get("tracked_query_count", 0)
    details += f"Tracked Queries: {tracked}\n"
    if tracked == 0:
        if result.get("engine") == "postgresql":
            details += (
                "NOTE: pg_stat_statements is NOT enabled on this database. "
                "This means we have NO visibility into actual query patterns, "
                "frequency, or performance. This severely limits the accuracy "
                "of any Readyset recommendation. The user should enable "
                "pg_stat_statements for a meaningful assessment.\n"
            )
        else:
            details += (
                "NOTE: No query statistics found in performance_schema. "
                "This limits visibility into actual query patterns and performance.\n"
            )
    details += (
        f"\nCache Opportunity Score: {cache.get('score', 0)}/100 ({cache.get('level', 'unknown')})\n"
        f"Cache Explanation: {cache.get('explanation', '')}\n"
    )

    top_queries = result.get("top_queries") or []
    if top_queries:
        details += (
            f"\n== TOP QUERIES (cumulative since last stats reset, from pg_stat_statements / "
            f"performance_schema, {len(top_queries)} tracked) ==\n"
        )
        details += (
            "NOTE: Call counts are cumulative since the last pg_stat_statements_reset() "
            "or server restart, not per-second rates.\n"
        )
        for index, query in enumerate(top_queries[:15], start=1):
            sql = (query.get("normalized_query") or "")[:300]
            details += (
                f"{index}. [{query.get('query_hash', '?')[:8]}] "
                f"calls={query.get('calls', 0)}, "
                f"total={query.get('total_time_ms', 0):.0f}ms, "
                f"avg={query.get('avg_time_ms', 0):.1f}ms, "
                f"pct={query.get('pct_total_time', 0):.1f}%\n"
                f"   {sql}\n"
            )

    return SINGLE_TARGET_INSIGHTS_PROMPT.format(target_details=details)


def build_fleet_insights_prompt(results: list[dict[str, Any]]) -> str:
    """Build prompt for fleet-level LLM analysis."""
    lines: list[str] = []
    for result in results:
        if result.get("error"):
            lines.append(f"- {result.get('target_name', '?')}: ERROR - {result['error']}")
            continue

        metrics = result.get("metrics") or {}
        sizing = result.get("sizing") or {}
        cache = result.get("cache_opportunity") or {}
        tracked = metrics.get("tracked_query_count", 0)

        entry = (
            f"- {result.get('target_name', '?')} ({result.get('engine', '?')}, "
            f"{result.get('instance_class') or 'unknown class'}):\n"
            f"  Sizing: {sizing.get('verdict', 'unknown')}\n"
            f"  Connections: {metrics.get('active_connections', 0)}/{metrics.get('max_connections', 0)} "
            f"({metrics.get('connection_utilization_pct', 0):.1f}%)\n"
            f"  Buffer Cache Hit: {metrics.get('cache_hit_rate', 0):.1f}%\n"
            f"  R/W: {metrics.get('read_pct', 0):.0f}% read / {metrics.get('write_pct', 0):.0f}% write\n"
            f"  DB Size: {metrics.get('database_size_mb', 0):.0f} MB\n"
            f"  Tracked Queries: {tracked}\n"
            f"  Cache Opportunity: {cache.get('score', 0)}/100 ({cache.get('level', 'unknown')})\n"
            f"  Version: {metrics.get('server_version', 'unknown')}"
        )
        if tracked == 0:
            extension = "pg_stat_statements" if result.get("engine") == "postgresql" else "performance_schema"
            entry += f"\n  WARNING: {extension} not enabled — no query visibility"

        top_queries = result.get("top_queries") or []
        if top_queries:
            entry += (
                f"\n  Top Queries (cumulative since last stats reset, {len(top_queries)} tracked):"
            )
            for query in top_queries[:5]:
                sql = (query.get("normalized_query") or "")[:80]
                entry += (
                    f"\n    - [{query.get('query_hash', '')[:8]}] calls={query.get('calls', 0)}, "
                    f"avg={query.get('avg_time_ms', 0):.1f}ms: {sql}"
                )

        workload = result.get("workload") or {}
        analysis = workload.get("analysis") or {}
        if analysis:
            recommendation = analysis.get("readyset_recommendation") or {}
            entry += (
                f"\n  --- Workload Analysis ---"
                f"\n  Readyset Verdict: {recommendation.get('verdict', '?')} — {recommendation.get('summary', '')}"
                f"\n  Estimated Cacheable: {recommendation.get('estimated_cacheable_pct', '?')}%"
                f"\n  Workload Type: {analysis.get('workload_characterization', '?')}"
                f"\n  Health Score: {analysis.get('health_score', '?')}/100"
            )
            candidates = analysis.get("caching_candidates", [])[:3]
            if candidates:
                entry += "\n  Top Caching Candidates:"
                for candidate in candidates:
                    entry += (
                        f"\n    - [{candidate.get('query_hash', '')[:8]}] "
                        f"calls={candidate.get('calls', 0)}: {candidate.get('reason', '')}"
                    )
            classifications = analysis.get("query_classifications", [])
            oltp = sum(1 for item in classifications if item.get("type") == "oltp")
            analytical = sum(1 for item in classifications if item.get("type") == "analytical")
            if classifications:
                entry += (
                    f"\n  Query Mix: {oltp} OLTP, {analytical} analytical, "
                    f"{len(classifications) - oltp - analytical} other"
                )

        lines.append(entry)

    return FLEET_INSIGHTS_PROMPT.format(
        count=len(results),
        fleet_table="\n".join(lines),
    )
