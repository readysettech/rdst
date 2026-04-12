"""Workload analysis LLM prompts."""

from typing import Any, Dict, List, Optional

from .models import WorkloadQuery


WORKLOAD_ANALYSIS_PROMPT = """You are a senior database performance architect advising on Readyset adoption.
Analyze this database workload holistically and provide optimization priorities.

Readyset is a transparent SQL caching proxy. It caches query RESULTS at the application layer and
keeps them automatically up-to-date. It works best for: read-heavy OLTP workloads with highly
repetitive queries (same query shape executed thousands of times). Always refer to it as "Readyset"
(lowercase s). Readyset REQUIRES the upstream database to remain running — it is a cache layer
in front of the database, not a replacement. Never suggest eliminating a database with Readyset.

PERFORMANCE CLAIMS:
- Readyset serves cached queries in single-digit milliseconds (sub-10ms). Do NOT claim sub-1ms
  or sub-millisecond latency. Say "single-digit milliseconds" or "sub-10ms" when discussing
  Readyset performance benefits.
- Most read-only SELECT queries are cacheable by Readyset. When classifying queries as
  "not cacheable", only do so for write queries (INSERT/UPDATE/DELETE) or queries that are
  genuinely non-deterministic (NOW(), RANDOM()). Do not mark queries as not cacheable simply
  because they are complex or analytical — Readyset can cache those too.

KEY CACHEABILITY SIGNALS:
- HIGH CALL COUNT = HIGH CACHEABILITY. Queries with thousands of calls are prime Readyset candidates
  because the cached result is reused on every call. The "calls" column below is the #1 signal.
- Readyset can cache ANY read-only SELECT query regardless of complexity. The question is whether
  caching provides meaningful benefit, which depends on repetition (call count) and latency savings.
- OLTP queries (simple lookups, joins, counts with equality filters) are the best Readyset candidates
  because they tend to be highly repetitive and fast to serve from cache.
- ANALYTICAL queries (complex aggregations, window functions, large scans) CAN be cached by Readyset
  but typically have lower repetition. If an analytical query runs frequently (hundreds+ of calls),
  it IS a good caching candidate. If it runs rarely, the cache provides little benefit.
- Read-only queries (SELECT) are cacheable. Write queries (INSERT/UPDATE/DELETE) are not.

DATABASE: {db_engine} {server_version}
DATABASE SIZE: {database_size_mb} MB
WORKLOAD WINDOW: {duration}s ({start_time} to {end_time})
CAPTURE SOURCE: {source}

{audit_metrics_section}

== WORKLOAD SUMMARY ==
Total unique queries: {unique_queries}
Total executions: {total_executions}
Total query time: {total_time_ms:.0f}ms
Cache hit ratio: start={start_cache_hit:.1f}%, end={end_cache_hit:.1f}%
Repetition ratio: {repetition_ratio} executions per unique query

== DATABASE STATS DELTA (over {duration}s) ==
{delta_stats_formatted}

== TOP {limit} QUERIES BY TOTAL TIME ==
{queries_table}

{time_series_section}

{cumulative_queries_section}

{schema_context}

INDEX RECOMMENDATION RULES:
- Check SCHEMA CONTEXT above before recommending indexes
- Do NOT recommend indexes that already exist (check index column composition, not just name)
- If a composite index covers the query's columns, do not recommend a subset index
- If an index exists but the query is still slow, note that the index may not be used (wrong column order, low selectivity, etc.)
- For truncated queries (ending abruptly), note that recommendations may be incomplete
{truncation_warning}

Provide your analysis as JSON with these fields:
{{
  "workload_characterization": "Brief description (e.g., 'read-heavy OLTP with high query repetition')",
  "health_score": <1-100>,
  "read_write_ratio": "X% reads / Y% writes",
  "readyset_recommendation": {{
    "verdict": "strong_candidate|good_candidate|marginal|not_recommended",
    "summary": "1-2 sentence recommendation for/against Readyset adoption",
    "estimated_cacheable_pct": <0-100>,
    "key_reasons": ["reason1", "reason2"]
  }},
  "query_classifications": [
    {{"query_hash": "...", "type": "oltp|analytical|mixed|utility", "cacheable_by_readyset": true, "reason": "..."}}
  ],
  "top_bottlenecks": [
    {{"rank": 1, "category": "missing_index|full_scan|lock_contention|temp_files|other", "description": "...", "impact": "high|medium|low", "affected_queries": ["hash1"], "recommendation": "..."}}
  ],
  "index_recommendations": [
    {{"table": "...", "columns": ["col1", "col2"], "type": "btree", "reason": "...", "estimated_impact": "...", "create_index_sql": "CREATE INDEX idx_table_col1_col2 ON table(col1, col2)"}}
  ],
  "caching_candidates": [
    {{"query_hash": "...", "calls": <int>, "reason": "...", "estimated_benefit": "..."}}
  ],
  "not_cacheable": [
    {{"query_hash": "...", "reason": "analytical query|non-deterministic|too complex|write query"}}
  ],
  "capacity_insights": ["insight1", "insight2"],
  "optimization_priorities": [
    {{"priority": 1, "action": "...", "category": "index|rewrite|config|schema|cache", "effort": "low|medium|high", "impact": "high|medium|low", "details": "..."}}
  ]
}}

Respond ONLY with valid JSON. No markdown, no explanation outside the JSON."""


def build_audit_capture_prompt(
    run_data: Dict[str, Any],
    limit: int = 30,
    cumulative_top_queries: Optional[List[Dict[str, Any]]] = None,
    audit_result: Optional[Dict[str, Any]] = None,
    schema_context: str = "",
) -> str:
    """Build the audit capture analysis prompt from a WorkloadRun dict."""
    queries = run_data.get("queries", [])
    snapshot_start = run_data.get("snapshot_start") or {}
    snapshot_end = run_data.get("snapshot_end") or {}
    delta_stats = run_data.get("delta_stats") or {}
    table_stats_end = run_data.get("table_stats_end") or []
    intermediate = run_data.get("intermediate_snapshots") or []

    # Format queries table
    queries_lines = []
    for i, q in enumerate(queries[:min(limit, 20)]):
        sql = (q.get("normalized_query") or "")[:200]
        queries_lines.append(
            f"{i+1}. [{q.get('query_hash', '?')[:8]}] "
            f"calls={q.get('calls', 0)}, "
            f"total={q.get('total_time_ms', 0):.0f}ms, "
            f"avg={q.get('avg_time_ms', 0):.1f}ms, "
            f"pct={q.get('pct_total_time', 0):.1f}%\n"
            f"   {sql}"
        )
    queries_table = "\n".join(queries_lines) if queries_lines else "(no queries captured)"

    # Format delta stats
    delta_lines = []
    for key, val in sorted(delta_stats.items()):
        if isinstance(val, float):
            delta_lines.append(f"  {key}: {val:.1f}")
        else:
            delta_lines.append(f"  {key}: {val}")
    delta_formatted = "\n".join(delta_lines) if delta_lines else "(no delta data)"

    # Format table stats
    table_lines = []
    for t in table_stats_end[:20]:
        seq = t.get("seq_scan", 0) or 0
        idx = t.get("idx_scan", 0) or 0
        dead = t.get("n_dead_tup", 0) or 0
        live = t.get("n_live_tup", 0) or 0
        table_lines.append(
            f"  {t.get('table_name', '?')}: "
            f"seq_scan={seq}, idx_scan={idx}, "
            f"rows={live}, dead_tuples={dead}"
        )
    table_formatted = "\n".join(table_lines) if table_lines else "(no table data)"

    # Time series with activity sampling
    time_series_section = ""
    if intermediate:
        time_series_section = "== TIME SERIES OBSERVATIONS (sampled every 30s) ==\n"
        for snap in intermediate:
            line = (
                f"  T+{snap.get('elapsed_seconds', 0):.0f}s: "
                f"cache={snap.get('cache_hit_ratio', 0):.1f}%, "
                f"conns={snap.get('active_connections', 0)}, "
                f"tps={snap.get('transactions_per_sec', 0):.1f}"
            )
            # Add activity details if available
            active_q = snap.get('active_queries', 0)
            longest = snap.get('longest_query_ms', 0)
            lock_w = snap.get('waiting_on_lock', 0)
            if active_q > 0:
                line += f", running_queries={active_q}"
            if longest > 100:
                line += f", longest_query={longest:.0f}ms"
            if lock_w > 0:
                line += f", lock_waits={lock_w}"
            idle_tx = snap.get('idle_in_transaction', 0)
            if idle_tx > 0:
                line += f", idle_in_tx={idle_tx}"
            time_series_section += line + "\n"

    # Format audit metrics (sizing, connections, cache, etc.) if available
    audit_metrics_section = ""
    if audit_result:
        metrics = audit_result.get("metrics") or {}
        sizing = audit_result.get("sizing") or {}
        cache = audit_result.get("cache_opportunity") or {}
        audit_metrics_section = (
            f"== AUDIT METRICS ==\n"
            f"Instance: {audit_result.get('instance_class') or 'unknown'}\n"
            f"Region: {audit_result.get('region') or 'unknown'}\n"
            f"Sizing: {sizing.get('verdict', 'unknown')} — {sizing.get('explanation', '')}\n"
            f"Est. Cost: ${sizing.get('current_monthly_cost_usd') or '?'}/month\n"
            f"Connections: {metrics.get('active_connections', 0)} active / {metrics.get('max_connections', 0)} max "
            f"({metrics.get('connection_utilization_pct', 0):.1f}%)\n"
            f"Buffer Cache Hit: {metrics.get('cache_hit_rate', 0):.1f}%\n"
            f"Read/Write: {metrics.get('read_pct', 0):.0f}% read / {metrics.get('write_pct', 0):.0f}% write\n"
            f"Is Replica: {metrics.get('is_replica', False)}\n"
            f"Cache Opportunity: {cache.get('score', 0)}/100 ({cache.get('level', 'unknown')})\n"
        )

    # Format cumulative top queries (historical, from pg_stat_statements / performance_schema)
    cumulative_queries_section = ""
    if cumulative_top_queries:
        cumulative_lines = [
            "== CUMULATIVE TOP QUERIES (from pg_stat_statements / performance_schema, since last reset) ==",
            "NOTE: These represent the FULL historical workload, not just the capture window above.",
            "If the capture window shows few queries but cumulative data shows many, the database",
            "likely has significant traffic outside the capture window. Factor this into your",
            "Readyset recommendation — the typical workload may be much heavier than what was captured.",
            "",
        ]
        for i, q in enumerate(cumulative_top_queries[:15]):
            sql = (q.get("normalized_query") or "")[:200]
            cumulative_lines.append(
                f"{i+1}. [{q.get('query_hash', '?')[:8]}] "
                f"calls={q.get('calls', 0)}, "
                f"total={q.get('total_time_ms', 0):.0f}ms, "
                f"avg={q.get('avg_time_ms', 0):.1f}ms, "
                f"pct={q.get('pct_total_time', 0):.1f}%\n"
                f"   {sql}"
            )
        cumulative_queries_section = "\n".join(cumulative_lines)

    # Compute repetition ratio (total executions / unique queries)
    unique_count = len(queries)
    total_exec = run_data.get("total_queries", 0)
    repetition_ratio = round(total_exec / unique_count, 1) if unique_count > 0 else 0

    # Detect truncated queries (performance_schema default 1024 byte limit)
    truncation_warning = ""
    truncated_count = 0
    for q in queries:
        sql = q.get("normalized_query") or ""
        if len(sql) >= 1020:
            truncated_count += 1
    if truncated_count > 0:
        truncation_warning = (
            f"WARNING: {truncated_count} queries are truncated by the database "
            f"(performance_schema digest text limit). Index recommendations for "
            f"these may be incomplete — referenced columns could be missing."
        )

    return WORKLOAD_ANALYSIS_PROMPT.format(
        db_engine=run_data.get("db_engine", "unknown"),
        server_version=snapshot_start.get("server_version", ""),
        database_size_mb=snapshot_start.get("database_size_mb", 0),
        duration=run_data.get("duration_seconds", 0),
        start_time=run_data.get("started_at", "")[:19],
        end_time=run_data.get("ended_at", "")[:19],
        source=run_data.get("source", "unknown"),
        unique_queries=unique_count,
        total_executions=total_exec,
        total_time_ms=run_data.get("total_query_time_ms", 0),
        start_cache_hit=snapshot_start.get("cache_hit_ratio") or 0,
        end_cache_hit=snapshot_end.get("cache_hit_ratio") or 0,
        repetition_ratio=repetition_ratio,
        delta_stats_formatted=delta_formatted,
        limit=min(limit, len(queries)),
        queries_table=queries_table,
        audit_metrics_section=audit_metrics_section,
        time_series_section=time_series_section,
        cumulative_queries_section=cumulative_queries_section,
        schema_context=schema_context,
        truncation_warning=truncation_warning,
    )
