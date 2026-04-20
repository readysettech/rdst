# Fleet & Audit Report Architecture

## Overview

`rdst audit` and `rdst fleet audit` produce health reports for
individual databases and multi-target fleets. Reports are delivered
as hosted HTML links via email (default) or printed to the terminal
(`--verbose`).

## User Flow

```
rdst fleet discover --regions us-east-1,us-west-2
  → calls AWS RDS API to find all instances + Aurora clusters
  → auto-detects engine, version, instance class, region
  → groups Aurora cluster members under cluster name
  → prompts for credentials (3 options below)
  → saves all targets to ~/.rdst/config.toml
  → prints: "Next: rdst fleet status" + "rdst fleet audit --group <group>"

rdst fleet audit --group production --duration 30s
  → validates API key (1-token preflight)
  → audits each target (metrics + health + LLM)
  → captures live queries during --duration window
  → benchmarks captured queries against Readyset cache (if deployed)
  → generates HTML report (3 sections)
  → prompts for email (first run only, then auto-sends)
  → sends hosted link via keyservice
```

## Fleet Discovery (`features/fleet/discovery.py`)

### AWS Integration
- Uses boto3 with `AWS_PROFILE` or default credentials
- Calls `describe_db_instances()` for standalone RDS instances
- Calls `describe_db_clusters()` for Aurora clusters
- Extracts: endpoint, port, engine, engine version, instance class,
  region, multi-AZ, storage type, allocated storage
- Aurora cluster members auto-grouped under cluster identifier
- Supports `--regions` for multi-region scanning
- Supports `--dry-run` to preview without saving

### Credential Setup (interactive, post-discover)
Three options presented to the user:

1. **Shared password** — one `FLEET_DB_PASS` env var for all targets.
   Tries OS keyring first (persists across sessions), falls back to
   env var with export instructions.
2. **Per-target** — individual password per target with unique env
   var names. Supports Secrets Manager ARN as alternative.
3. **Skip** — configure later via `rdst configure edit <target>`

After setup, prints env var export commands the user needs to add
to their shell profile.

### CloudWatch Integration
- Pulls CPUUtilization from CloudWatch for each RDS instance
- Default: 24-hour window, hourly granularity
- Requires `cloudwatch:GetMetricStatistics` IAM permission
- Gracefully returns None when AWS credentials unavailable
- Data shows in report as "CPU (1-day avg)" gauge
- Derives RDS instance identifier from hostname for API calls
- Aurora cluster endpoints resolved via fallback patterns

### Instance Class Detection
When `instance_class` is not configured (non-AWS or manual targets):
- Estimated from `shared_buffers` size (PG) — `shared_buffers * 4`
  approximates total RAM, matched to closest RDS instance class
- Used for pricing lookups + config audit scaling

## Report Structure (3 Sections)

### §1 Overview
- Topology cards (primary/replica, engine, QPS, connections)
- Resource gauges (CPU, cache hit, connection use, storage, TXID)
- Sizing & Caching Candidates table (per-target verdict pills)
- Executive summary + findings (from fleet LLM or synthesized)

### §2 Detailed Analysis
- Per-target collapsible subsections (fleet) or inline (single)
- Database Overview (kv-table: version, size, connections, R/W)
- Key Findings (severity-colored checkmarks)
- Captured Queries & Performance (query table with RS speedup)
- Index Recommendations (syntax-highlighted CREATE INDEX)
- Optimization Priorities (effort/impact matrix)
- Caching verdict at end of each target

### §3 Sizing & Savings (only when AWS pricing available)
- Per-target breakdown: Current → Rightsize → With Caching
- Fleet total with replica elimination + cache infra estimate

### §4 Next Steps
- Numbered actions with `rdst` commands inline
- Savings estimates per action

## LLM Calls

Two separate LLM calls per target, each seeing different data:

### Health LLM (`features/audit/health_prompt.py`)
- **When:** Runs in a **background thread** during the `--duration`
  capture window, overlapping with the capture for free. For
  non-duration audits, runs synchronously after `audit_target()`.
- **Sees:** Health collector data (vacuum, indexes, connections,
  config, replication) + basic metrics + CloudWatch CPU + top
  queries from pg_stat_statements
- **Produces:** `health_score` (0-100), `health_label`, findings,
  recommended_actions, index_suggestions, query_commentary
- **Used in:** Hero score ring, top findings cards, §1 findings,
  §2 key findings, §4 next steps

### Workload LLM (`features/audit/prompts.py`)
- **When:** After `--duration` capture completes (main thread)
- **Sees:** Captured query delta (calls, avg_time, load%), time
  series snapshots, cumulative stats, schema context
- **Produces:** workload_characterization, readyset_recommendation,
  top_bottlenecks, index_recommendations, caching_candidates,
  optimization_priorities
- **Used in:** `audit show` detailed workload analysis panels

### Parallelism (with `--duration`)

```
audit_target() → metrics + health collectors (~2s)
                    ↓
    ┌───────────────┴───────────────┐
    │                               │
 Thread 1                     Main thread
 Health LLM (~5s)             Duration capture (30s)
                              + Workload LLM (~5s)
    │                               │
    └───────────────┬───────────────┘
                    ↓
            Merge → report
```

The health LLM runs entirely during the capture window wait time,
so the user pays zero extra wall-clock time for it.

### Fleet LLM (`features/fleet/llm.py`)
- **When:** After all per-target audits complete (fleet only)
- **Sees:** All per-target results aggregated
- **Produces:** fleet `health_score`, `top_findings` (cross-target),
  `fleet_findings`, `next_steps` with commands + savings
- **Scoring rubric:** Weighted by replica replaceability — idle
  replicas on small datasets = low score

## Health Data Collectors (`features/audit/health/`)

Five SQL collectors run on every audit. Data persisted in snapshot
JSON but NOT rendered in the current report. The LLM sees the data
and can reference it in findings.

| Collector | PG Source | MySQL Source |
|-----------|-----------|--------------|
| Vacuum/Bloat | pg_stat_user_tables, age(datfrozenxid) | information_schema.TABLES DATA_FREE |
| Index Health | pg_stat_user_indexes, pg_indexes | information_schema.STATISTICS |
| Connections | pg_stat_activity | performance_schema.threads |
| Config Audit | pg_settings (8 params) | SHOW VARIABLES |
| Replication | pg_stat_replication, pg_replication_slots | SHOW REPLICA STATUS |

## Readyset Cache Testing

When `--duration` is used and a Readyset cache is deployed:

1. Check if RS cache target exists (`<target>-cache` convention)
2. Record pre-existing cache IDs
3. For each captured SELECT query (up to 20):
   - Normalize MySQL DIGEST_TEXT spacing (`SUM (` → `SUM(`)
   - Filter system queries (`@@`, `performance_schema`, `pg_stat_`)
   - Create shallow cache via RS
   - Benchmark: 3 runs (1 warmup + 2 measured)
   - Record speedup = DB avg / RS avg
4. Drop only caches we created (preserve pre-existing)
5. Results flow into report query table (Cached Avg + Speedup columns)

## Query Hash Resolution

All hashes use the query registry's `hash_sql()` function as the
single source of truth. When queries are saved during audit capture,
the registry returns the hash and the WorkloadQuery object is updated
to use it. This ensures `rdst analyze --hash <X>` always resolves
directly — same mechanism as `rdst top`.

For MySQL: DIGEST_TEXT from performance_schema has extra spacing
around function parens and dot notation that breaks SQLGlot parsing.
The capture service normalizes this before saving to the registry.

## Email Delivery

### First-time flow
1. Audit completes → CLI prompts "Sending to X, press Enter"
2. If no email on file → prompt with validation + retry loop
3. CLI calls keyservice `/register-report` → verification email
4. User clicks verification link → CLI polls `/report-status`
5. Token saved to `~/.rdst/config.toml`

### Subsequent runs
Token on file → send directly, no verification prompt.

### `--verbose` mode
Terminal-only output with command hints. No email. No HTML generated.
Snapshots still saved.

## Keyservice (`keyservice/src/index.py`)

Cloudflare Worker with D1 database.

| Endpoint | Purpose |
|----------|---------|
| POST /register-report | Register email, send verification |
| GET /verify-report | User clicks link to verify |
| GET /report-status | CLI polls for verification |
| POST /send-report | Send report (mode=link or inline) |
| GET /report/:id | Render hosted report (password gate) |
| GET /admin/reports | Per-email report metrics |

### Hosted Reports
- HTML stored in `hosted_reports` D1 table
- Random UUID URLs (122-bit entropy)
- Password-protected: 8-char random password, SHA-256 checked
  client-side, password shown in the email body
- 30-day TTL with opportunistic cleanup
- `noindex, nofollow` headers
- `?download=1` forces save dialog

## PostHog Telemetry

| Event | When | Slack? |
|-------|------|--------|
| `audit_report_generated` | Every audit | No |
| `fleet_audit_report_generated` | Every fleet audit | No |
| `first_audit` | First audit per device | Yes |
| `first_fleet_audit` | First fleet audit per device | Yes |

Properties include: email, target, engine, health_score,
sizing_verdict, potential_savings_usd, captured_queries.

`posthog.identify()` links device_id to email for cross-device
correlation.

## CLI Output Modes

| Mode | What renders | Email? | HTML saved? |
|------|-------------|--------|-------------|
| Default (no flag) | Compact summary | Yes | Yes (~/.rdst/reports/) |
| `--verbose` | Full terminal report | No | No |
| `--json` | Raw JSON | No | No |

## Preflight Checks

Before any `--duration` capture:
- **API key:** 1-token LLM call validates key works
- **PG:** `SELECT 1 FROM pg_stat_statements LIMIT 1`
- **MySQL:** `SHOW VARIABLES LIKE 'performance_schema'` must be ON

Failure aborts immediately with actionable fix instructions.

## Sizing & Savings Calculation

Per-target:
- `monthly_cost()` from RDS pricing table
- `suggest_downsize()` — one size smaller in same family
- `suggest_downsize_with_readyset()` — 75% read offload cap,
  find smallest instance meeting post-offload memory needs

Fleet:
- `compute_fleet_savings()` = sum(replica costs) +
  sum(non-replica rightsize) - cache_infra (50% of max target cost)

## Key Files

| File | What it does |
|------|-------------|
| `features/audit/service.py` | audit_target() — metrics, health, LLM |
| `features/audit/cli/command.py` | CLI flow, terminal renderer, email |
| `features/audit/capture_service.py` | Duration capture, RS benchmarking |
| `features/audit/report/report.py` | HTML report renderer |
| `features/audit/health_prompt.py` | Per-target health LLM prompt |
| `features/audit/health/` | 5 SQL health collectors |
| `features/audit/email_service.py` | Email delivery client |
| `features/fleet/cli/command.py` | Fleet CLI flow |
| `features/fleet/llm.py` | Fleet LLM prompt |
| `features/fleet/pricing.py` | RDS pricing + savings math |
| `features/audit/scoring.py` | Sizing verdict + RS projected |
| `keyservice/src/index.py` | Cloudflare Worker endpoints |
| `shared/telemetry_manager.py` | PostHog event tracking |
