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
  qps?: number;
  max_duration_ms?: number;
  current_instances_running?: number;
  observation_count?: number;
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

// SSE Event Data Types
export interface TopStatusEventData {
  message: string;
}

export interface TopConnectedEventData {
  target_name: string;
  db_engine: string;
  source: string;
}

export interface TopSourceFallbackEventData {
  from_source: string;
  to_source: string;
  reason: string;
}

export interface TopQueriesEventData {
  queries: TopQuery[];
  source: string;
  target_name: string;
  db_engine: string;
  runtime_seconds?: number;
  total_tracked?: number;
}

export interface TopQuerySavedEventData {
  query_hash: string;
  is_new: boolean;
}

export interface TopCompleteEventData {
  success: boolean;
  queries: TopQuery[];
  source: string;
  newly_saved: number;
}

export interface TopErrorEventData {
  message: string;
  stage?: string;
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
}
