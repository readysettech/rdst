// Audit Types
// ---------------------------------------------------------------------------
//
// REST-backed shapes come from the generated OpenAPI types. The audit report
// payload itself travels as a free-form dict (SSE `target_complete.result`
// and the run-detail endpoint), so its shape is hand-typed here, mirroring
// rdst/features/audit/models.py (AuditResult) and the health-analysis JSON
// contract in rdst/features/audit/health_prompt.py.

import type { components } from '../lib/api.generated';

export type AuditRunSummary = components['schemas']['AuditRunSummary'];
export type AuditRunListResponse = components['schemas']['AuditRunListResponse'];
export type AuditEvent = components['schemas']['AuditEvent'];
export type WorkloadEvent = components['schemas']['WorkloadEvent'];

export type AuditRunState = 'idle' | 'running' | 'complete' | 'error';

// ---------------------------------------------------------------------------
// Workload capture shapes
// ---------------------------------------------------------------------------
//
// The capture-complete summary and the run-detail endpoint travel as free-form
// dicts, so their shapes are hand-typed here, mirroring the WorkloadRun /
// WorkloadAnalysis payloads produced by the capture service.

export type CaptureState = 'idle' | 'capturing' | 'analyzing' | 'complete' | 'error';

export interface WorkloadQuery {
  query_hash?: string;
  query_text?: string;
  normalized_query?: string;
  calls?: number;
  total_time_ms?: number;
  avg_time_ms?: number;
  pct_total_time?: number;
}

export interface WorkloadIndexRecommendation {
  sql?: string;
  // Audit analysis currently asks the LLM for this key. Older/simpler
  // payloads use `sql`, so report renderers accept both.
  create_index_sql?: string;
  reason?: string;
  table?: string;
  columns?: string[];
  expression?: string;
  estimated_impact?: string;
  affected_queries?: string[];
  query_hashes?: string[];
  queries?: string[];
}

// An optimization priority from the capture LLM. It is an untyped dict whose
// keys vary across audit versions (e.g. rank/description/recommendation or
// priority/action/details), so all fields are optional and consumers read
// whichever are present. Some payloads use plain strings instead.
export interface WorkloadOptimizationPriority {
  rank?: number;
  priority?: number;
  category?: string;
  description?: string;
  action?: string;
  details?: string;
  impact?: string;
  effort?: string;
  affected_queries?: string[];
  recommendation?: string;
}

export interface WorkloadAnalysis {
  health_score?: number;
  workload_characterization?: string;
  read_write_ratio?: string;
  // LLM-generated; entries may be plain strings or objects with varying keys
  // (rendered via bulletContent, which reads whichever fields are present).
  top_bottlenecks?: (string | Record<string, unknown>)[];
  index_recommendations?: WorkloadIndexRecommendation[];
  caching_candidates?: (string | Record<string, unknown>)[];
  capacity_insights?: string[];
  optimization_priorities?: (string | WorkloadOptimizationPriority)[];
}

export interface ReadysetComparisonQuery {
  query_hash?: string;
  query_text?: string;
  supported?: boolean;
  reason?: string;
  upstream_ms?: number;
  readyset_ms?: number;
  speedup?: number;
  upstream_source?: string;
  deep_supported?: boolean | null;
  deep_reason?: string | null;
  static_cacheable?: boolean | null;
  static_reason?: string | null;
}

export interface ReadysetComparison {
  queries_tested?: number;
  supported_count?: number;
  unsupported_count?: number;
  avg_speedup?: number;
  deep_supported_count?: number;
  deep_unsupported_count?: number;
  deep_unknown_count?: number;
  queries?: ReadysetComparisonQuery[];
}

export interface WorkloadSummary {
  unique_queries?: number;
  total_executions?: number;
  total_queries?: number;
  total_query_time_ms?: number;
  duration_seconds?: number;
  path?: string | null;
  has_analysis?: boolean;
  queries?: WorkloadQuery[];
  readyset_comparison?: ReadysetComparison | null;
}

export interface WorkloadSnapshot {
  when?: string;
  cache_hit_ratio?: number | null;
  active_connections?: number;
}

// Full payload returned by GET /api/audit/runs/{id} for capture runs.
//
// Duration captures may be enriched with the metrics audit collected for the
// same target, so saved captures and fleet results share the complete report
// layout; those shared fields are picked from AuditReport.
export interface WorkloadRun
  extends Pick<
    AuditReport,
    | 'target_name'
    | 'engine'
    | 'host'
    | 'region'
    | 'group'
    | 'tags'
    | 'instance_class'
    | 'instance_class_source'
    | 'audited_at'
    | 'metrics'
    | 'sizing'
    | 'cache_opportunity'
    | 'top_queries'
    | 'health_analysis'
    | 'cloudwatch_cpu'
    | 'health_report'
    | 'readyset_comparison'
  > {
  run_id?: string;
  db_engine?: string;
  started_at?: string | null;
  duration_seconds?: number;
  source?: string;
  queries?: WorkloadQuery[];
  snapshot_start?: WorkloadSnapshot | null;
  snapshot_end?: WorkloadSnapshot | null;
  analysis?: WorkloadAnalysis | null;
  total_queries?: number;
  total_query_time_ms?: number;
}

export interface AuditMetrics {
  max_connections?: number;
  active_connections?: number;
  idle_connections?: number;
  connection_utilization_pct?: number;
  cache_hit_rate?: number;
  shared_buffers_mb?: number;
  working_set_mb?: number;
  read_pct?: number;
  write_pct?: number;
  is_replica?: boolean;
  replication_lag_seconds?: number | null;
  database_size_mb?: number;
  server_version?: string;
  tracked_query_count?: number;
  total_query_time_ms?: number;
  uptime_seconds?: number;
  stats_window_seconds?: number;
  stats_reset_at?: string | null;
  collected_at?: string | null;
  storage_allocated_gb?: number | null;
  storage_used_pct?: number | null;
  storage_type?: string | null;
}

export interface AuditSizing {
  verdict?: string;
  explanation?: string;
  current_monthly_cost_usd?: number | null;
  suggested_instance_class?: string | null;
  suggested_monthly_cost_usd?: number | null;
  potential_savings_usd?: number | null;
  readyset_projected_class?: string | null;
  readyset_projected_cost_usd?: number | null;
  readyset_projected_savings_usd?: number | null;
  readyset_offload_pct?: number | null;
  concurrent_query_load?: number | null;
  estimated_cpu_pct?: number | null;
}

export interface AuditCacheOpportunity {
  score?: number;
  level?: string;
  explanation?: string;
  factors?: Record<string, number>;
}

export interface AuditTopQuery {
  query_hash?: string;
  query_text?: string;
  normalized_query?: string;
  calls?: number;
  total_time_ms?: number;
  avg_time_ms?: number;
  pct_total_time?: number;
}

export interface HealthFinding {
  severity?: 'ok' | 'warn' | 'crit' | 'info' | string;
  title?: string;
  body?: string;
}

export interface HealthAction {
  rank?: number;
  title?: string;
  body?: string;
}

export interface HealthAnalysis {
  health_score?: number;
  health_label?: string;
  health_score_rationale?: string;
  executive_summary?: string;
  top_findings?: HealthFinding[];
  findings?: HealthFinding[];
  recommended_actions?: HealthAction[];
  index_suggestions?: Array<{ sql?: string; reason?: string }>;
  query_commentary?: Array<{ query_hash?: string; observation?: string }>;
  error?: string;
}

export interface CloudwatchCpu {
  avg_cpu?: number;
  max_cpu?: number;
  min_cpu?: number;
  hours?: number;
  [key: string]: unknown;
}

export interface AuditHealthReport {
  engine?: string;
  vacuum_bloat?: Record<string, unknown> | null;
  index_health?: Record<string, unknown> | null;
  connections?: Record<string, unknown> | null;
  config_audit?: Record<string, unknown> | null;
  replication?: Record<string, unknown> | null;
  collection_error?: string | null;
  section_errors?: Record<string, string>;
  [key: string]: unknown;
}

export interface AuditReport {
  target_name?: string;
  engine?: string;
  host?: string;
  database?: string | null;
  region?: string | null;
  group?: string | null;
  tags?: string[];
  instance_class?: string | null;
  /** Where the instance class came from: "aws" metadata, an "estimated" guess, or unknown. */
  instance_class_source?: 'aws' | 'estimated' | string | null;
  audited_at?: string | null;
  error?: string | null;
  metrics?: AuditMetrics | null;
  sizing?: AuditSizing | null;
  cache_opportunity?: AuditCacheOpportunity | null;
  top_queries?: AuditTopQuery[];
  health_analysis?: HealthAnalysis | null;
  cloudwatch_cpu?: CloudwatchCpu | null;
  health_report?: AuditHealthReport | null;
  workload?: WorkloadSummary & { analysis?: WorkloadAnalysis | null; error?: string };
  readyset_comparison?: ReadysetComparison | null;
}
