"""Fleet-level LLM insights — prompt construction and analysis."""

from __future__ import annotations

from typing import Any


FLEET_INSIGHTS_PROMPT = """You are a senior database reliability engineer producing a
neutral fleet health assessment for the RDST tool. You see {count} targets.

SCORING RUBRIC (0-100, strict)
Score is primarily about "how much can be saved by replacing replicas with a
caching layer." Fleets with many idle replicas serving repetitive reads get
LOW scores. Fleets that are already right-sized or have no redundant replicas
get HIGH scores. Apply these adjustments from a base of 100:
- Replica utilization: for each oversized replica on a small dataset (<50 GB)
  serving repetitive reads, subtract 8 points.
- Primary misuse: subtract 10 points if the primary receives >95% read traffic
  (indicates routing misconfiguration).
- Unused resources: subtract 5 points per instance showing under 20%
  connection utilization AND over 99% buffer cache hit.
- Bloat or TXID risk: subtract 10 points per target with n_dead_ratio > 30%
  or TXID age > 50%.
- Caching fit: subtract 5 points per target with cache_opportunity_score >= 70
  (these are the most-savings-available targets and drag the "current state"
  score down).
- Clamp to [0, 100].

SEVERITY GUIDE (for top_findings and fleet_findings)
- "crit" — imminent operational risk (replication broken, TXID wraparound
  close, autovacuum disabled with active bloat)
- "warn" — meaningful but not urgent (over-provisioning, unused indexes,
  misconfigured routing)
- "info" — observation that shapes recommendations (read-heavy workload,
  repetitive query patterns, idle replicas)
- "ok" — something done well worth calling out (clean vacuum, zero lag)

TONE RULES
- Neutral, technical, no marketing language.
- Refer to "caching" or "caching layer" generically. Recommend measuring a
  representative query before making a deployment decision.
- Dollars appear ONLY in next_steps[].estimated_savings_usd. Not in findings,
  not in bodies, not in summaries.
- No emojis.

== FLEET AUDIT SUMMARY ==
{fleet_table}

Respond ONLY with valid JSON in this exact shape. No markdown, no prose outside JSON.

{{
  "health_score": <integer 0-100>,
  "health_label": "CRITICAL|POOR|FAIR|GOOD|EXCELLENT",
  "score_rationale": "One sentence explaining the score in plain English.",
  "executive_summary": "One paragraph (3-4 sentences) characterizing the fleet — workload shape, the load-bearing observation, and the overall direction. No dollars.",
  "top_findings": [
    {{"severity": "crit|warn|info|ok", "title": "<=6 word headline", "body": "One sentence with concrete numbers from the data — cross-target observations preferred."}}
  ],
  "fleet_findings": [
    {{"severity": "crit|warn|info|ok", "title": "<=6 word headline", "body": "1-2 sentence detailed finding with specific numbers."}}
  ],
  "next_steps": [
    {{"rank": 1, "title": "Imperative action (<=10 words)", "body": "2-3 sentence explanation of what to do and why.", "commands": ["rdst ..."], "estimated_savings_usd": <number or null>}}
  ]
}}

FINDINGS GUIDANCE
- top_findings: exactly 2-3 items. These render as hero-level callouts above
  the rest of the report. Must be the most load-bearing cross-target
  observations. Surprising > mundane. Specific > generic.
- fleet_findings: 4-6 items. These are the full list shown under the
  Overview section.

NEXT STEPS GUIDANCE
- 3-6 ranked actions.
- Each step's body explains *why* concretely — reference data.
- ONLY include `commands` from this exact whitelist — do NOT invent flags
  or subcommands that are not listed here:
    rdst analyze --target <name> --hash <8-char-hash>
    rdst audit --target <name> --duration <time>
    rdst fleet audit --group <name> --duration <time>
  Do NOT suggest: rdst cache deploy, rdst cache stats, rdst query show,
  or any command with flags like --focus, --monitor, --watch. These do not
  exist. If you want to recommend deploying a cache or viewing a query,
  describe the action in the body text without a commands[] entry.
- For caching-related actions, set estimated_savings_usd to the dollar
  impact if the data supports it; otherwise null.
- Do NOT duplicate the same action across multiple steps."""


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
