// Cache Management Types
// ---------------------------------------------------------------------------

export interface CacheStatusResponse {
  deployed: boolean;
  running: boolean;
  endpoint?: string;
  cache_target?: string;
  container_name?: string;
}

export interface CacheDeployRequest {
  target: string;
  mode: 'docker' | 'kubernetes' | 'systemd';
  port?: number;
  namespace?: string;
  host?: string;
  ssh_user?: string;
}

export interface CacheAddRequest {
  query: string;
  target: string;
  tag?: string;
  dry_run?: boolean;
}

export interface CacheAddResponse {
  success: boolean;
  supported: boolean;
  query: string;
  query_hash?: string;
  detail?: string;
  error?: string;
}

export interface CacheEntry {
  cache_id: string;
  cache_name: string;
  query: string;
  type: 'shallow' | 'full';
  ttl: string;
  registry_hash?: string;
}

export interface CacheListResponse {
  success: boolean;
  caches: CacheEntry[];
  count: number;
}

export type CacheDeployState = 'idle' | 'deploying' | 'complete' | 'error';

// Performance comparison (cache run)

export interface CacheRunRequest {
  query: string;
  target: string;
  iterations?: number;
  warmup?: number;
}

export interface CacheRunStats {
  mean: number;
  median: number;
  min: number;
  max: number;
  p50: number;
  p95: number;
  p99: number;
  stddev?: number;
}

export interface CacheRunResult {
  success: boolean;
  query: string;
  iterations: number;
  origin_stats: CacheRunStats;
  cache_stats: CacheRunStats;
  speedup_mean: number;
  speedup_median: number;
  improvement_pct: number;
  winner: 'readyset' | 'origin';
}

export type CacheRunState = 'idle' | 'running' | 'complete' | 'error';
