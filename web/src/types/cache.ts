// Cache Management Types
// ---------------------------------------------------------------------------
//
// REST-backed shapes are re-exported from the generated OpenAPI types so the
// frontend stays pinned to what the backend actually returns. SSE event
// payloads (CacheRunResult, CacheRunStats) are still hand-written because
// sse_starlette responses aren't in the OpenAPI schema yet.

import type { components } from '../lib/api.generated';

export type CacheStatusResponse = components['schemas']['CacheStatusResponse'];
export type CacheDeployRequest = components['schemas']['CacheDeployRequest'];
export type CacheAddRequest = components['schemas']['CacheAddRequest'];
export type CacheAddResponse = components['schemas']['CacheAddResponse'];
export type CacheEntry = components['schemas']['CacheEntryResponse'];
export type CacheListResponse = components['schemas']['CacheListResponse'];
export type CacheRunRequest = components['schemas']['CacheRunRequest'];

// UI state machines — not part of the API contract.
export type CacheDeployState = 'idle' | 'deploying' | 'complete' | 'error';
export type CacheRunState = 'idle' | 'running' | 'complete' | 'error';

// Cache performance comparison — consumed from the /api/cache/run SSE stream,
// not from any REST endpoint, so still hand-typed until SSE payloads make it
// into OpenAPI components.
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
