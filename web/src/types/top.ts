/**
 * Types for Top Queries feature
 */

export interface TopQuery {
  query_hash: string;
  query_text: string;
  normalized_query: string;
  freq: number;
  total_time: string;
  avg_time: string;
  pct_load: string;
  qps?: number | null;
  max_duration_ms?: number | null;
  current_instances_running?: number | null;
  observation_count?: number | null;
}

export interface TopOptions {
  target: string;
  limit?: number;
  source?: 'auto' | 'pg_stat' | 'activity' | 'digest';
  sort?: 'total_time' | 'freq' | 'avg_time' | 'load';
  filter_pattern?: string;
  duration?: number;
  auto_save?: boolean;
  min_freq?: number;
  min_load_pct?: number;
}

export type TopMode = 'historical' | 'realtime';
export type TopState = 'idle' | 'loading' | 'streaming' | 'complete' | 'error';

export interface TopConnectionInfo {
  target: string;
  engine: string;
  source: string;
}

export interface TopSourceFallback {
  from_source: string;
  to_source: string;
  reason: string;
}

// SSE event payload types are generated from the backend OpenAPI schema;
// consume them via `components['schemas']['TopEvent']` in `lib/api.generated`.
// The DB limit warning payload is also returned (sans discriminator) from the
// historical JSON endpoint, so we keep its local alias.
export interface TopDbLimitWarningEventData {
  db_limit_bytes: number;
  recommended_bytes: number;
  setting_name: string;
  db_engine: string;
}

// Historical JSON Response
export interface TopHistoricalResponse {
  success: boolean;
  target?: string;
  engine?: string;
  source?: string;
  queries?: TopQuery[];
  newly_saved?: number;
  error?: string;
  code?: string;
  category?: string;
  db_limit_warning?: TopDbLimitWarningEventData;
}
