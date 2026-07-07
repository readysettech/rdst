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
  reason?: string;
  table?: string;
  estimated_impact?: string;
}

export interface WorkloadAnalysis {
  health_score?: number;
  workload_characterization?: string;
  read_write_ratio?: string;
  top_bottlenecks?: string[];
  index_recommendations?: WorkloadIndexRecommendation[];
  caching_candidates?: string[];
  capacity_insights?: string[];
  optimization_priorities?: string[];
}

export interface WorkloadSummary {
  unique_queries?: number;
  total_executions?: number;
  total_query_time_ms?: number;
  duration_seconds?: number;
  path?: string | null;
  has_analysis?: boolean;
  queries?: WorkloadQuery[];
}

export interface WorkloadSnapshot {
  when?: string;
  cache_hit_ratio?: number | null;
  active_connections?: number;
}

// Full payload returned by GET /api/audit/runs/{id} for capture runs.
export interface WorkloadRun {
  run_id?: string;
  target_name?: string;
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

export interface AuditReport {
  target_name?: string;
  engine?: string;
  host?: string;
  region?: string | null;
  instance_class?: string | null;
  audited_at?: string | null;
  error?: string | null;
  metrics?: AuditMetrics | null;
  sizing?: AuditSizing | null;
  cache_opportunity?: AuditCacheOpportunity | null;
  top_queries?: AuditTopQuery[];
  health_analysis?: HealthAnalysis | null;
  cloudwatch_cpu?: Record<string, unknown> | null;
  health_report?: Record<string, unknown> | null;
}
