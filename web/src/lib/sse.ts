import { useState, useCallback, useRef } from 'react';
import { useTargetSwitchLock } from './targetSwitchLock';
import type { components } from './api.generated';
import {
  AnalyzeRequest,
  AnalysisState,
  ProgressEvent,
  CompleteEvent,
  RewriteTesting,
  ReadysetCacheability,
  BenchmarkRequest,
  BenchmarkState,
} from './api';
import {
  type ApiErrorEnvelope,
  friendlySqlError,
  normalizeHttpError,
  normalizeSseError,
} from './errorContract';

// Benchmark SSE event types are derived from the backend-generated discriminated union.
// Backend source of truth: rdst/features/query_registry/events.py (QueryBenchmarkEvent).
type QueryBenchmarkEvent = components['schemas']['QueryBenchmarkEvent'];
type QueryBenchmarkProgressEvent = Extract<QueryBenchmarkEvent, { type: 'progress' }>;
type QueryBenchmarkCompleteEvent = Extract<QueryBenchmarkEvent, { type: 'complete' }>;
type QueryBenchmarkErrorEvent = Extract<QueryBenchmarkEvent, { type: 'error' }>;
// Progress state stores only counter-bearing variants; errors go to the `error` field.
export type BenchmarkProgress = QueryBenchmarkProgressEvent | QueryBenchmarkCompleteEvent;

// SSE event types are derived from the backend-generated discriminated union.
// Backend source of truth: rdst/features/analyze/events.py (AnalyzeEvent).
// The `CompleteEvent`/`ProgressEvent` shapes in api.ts refine the generated
// `{[key: string]: unknown}` payloads for downstream consumers.
type AnalyzeEvent = components['schemas']['AnalyzeEvent'];
export type AnalyzeEventType = AnalyzeEvent['type'];

interface UseAnalyzeReturn {
  analyze: (request: AnalyzeRequest) => Promise<void>;
  state: AnalysisState;
  progress: ProgressEvent | undefined;
  results: CompleteEvent | undefined;
  rewriteTesting: RewriteTesting | undefined;
  readysetCacheability: ReadysetCacheability | undefined;
  error: string | undefined;
  errorEnvelope: ApiErrorEnvelope | undefined;
  reset: () => void;
}

function normalizeRewriteTesting(candidate: unknown): RewriteTesting | undefined {
  if (!candidate || typeof candidate !== 'object') {
    return undefined;
  }

  const testing = candidate as RewriteTesting & {
    success?: boolean;
    rewrite_results?: unknown;
    best_rewrite?: unknown;
  };

  if (typeof testing.tested === 'boolean') {
    return testing;
  }

  if (testing.skipped_reason || testing.success === false) {
    return { ...testing, tested: false };
  }

  if (testing.success === true) {
    const rewriteResults = Array.isArray(testing.rewrite_results)
      ? testing.rewrite_results
      : [];
    return {
      ...testing,
      tested: rewriteResults.length > 0 || Boolean(testing.best_rewrite),
      rewrite_results: rewriteResults as RewriteTesting['rewrite_results'],
    };
  }

  return undefined;
}

export function useAnalyze(): UseAnalyzeReturn {
  const [state, setState] = useState<AnalysisState>('idle');
  const [progress, setProgress] = useState<ProgressEvent | undefined>(undefined);
  const [results, setResults] = useState<CompleteEvent | undefined>(undefined);
  const [rewriteTesting, setRewriteTesting] = useState<RewriteTesting | undefined>(undefined);
  const [readysetCacheability, setReadysetCacheability] = useState<ReadysetCacheability | undefined>(undefined);
  const [error, setError] = useState<string | undefined>(undefined);
  const [errorEnvelope, setErrorEnvelope] = useState<ApiErrorEnvelope | undefined>(undefined);

  const abortControllerRef = useRef<AbortController | null>(null);
  useTargetSwitchLock('analyze', state === 'analyzing');

  const failWith = useCallback((envelope: ApiErrorEnvelope) => {
    setError(envelope.message);
    setErrorEnvelope(envelope);
    setState('error');
  }, []);

  const reset = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setState('idle');
    setProgress(undefined);
    setResults(undefined);
    setRewriteTesting(undefined);
    setReadysetCacheability(undefined);
    setError(undefined);
    setErrorEnvelope(undefined);
  }, []);

  const analyze = useCallback(async (request: AnalyzeRequest) => {
    console.log('[SSE] Starting analysis with request:', request);
    
    if (abortControllerRef.current) {
      console.log('[SSE] Aborting previous request');
      abortControllerRef.current.abort();
    }

    const controller = new AbortController();
    abortControllerRef.current = controller;

    setState('analyzing');
    setProgress(undefined);
    setResults(undefined);
    setRewriteTesting(undefined);
    setReadysetCacheability(undefined);
    setError(undefined);
    setErrorEnvelope(undefined);

    try {
      console.log('[SSE] Sending POST to /api/analyze');
      const response = await fetch('/api/analyze', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(request),
        signal: controller.signal,
      });

      console.log('[SSE] Response status:', response.status);

      if (!response.ok) {
        // Normalize the HTTP failure into the shared envelope rather than
        // surfacing a raw status/body string to the UI (B7/T24).
        let parsedBody: unknown;
        try {
          parsedBody = await response.clone().json();
        } catch {
          parsedBody = await response.text().catch(() => undefined);
        }
        failWith(normalizeHttpError(response.status, parsedBody));
        return;
      }
      
      if (!response.body) {
        throw new Error('No response body');
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let currentEvent = '';

      console.log('[SSE] Starting to read stream...');

      while (true) {
        const { done, value } = await reader.read();
        
        if (done) {
          console.log('[SSE] Stream done');
          break;
        }
        
        const chunk = decoder.decode(value, { stream: true });
        buffer += chunk;
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          const trimmed = line.trim();
          
          if (!trimmed) {
            currentEvent = '';
            continue;
          }

          if (trimmed.startsWith('event:')) {
            currentEvent = trimmed.substring(6).trim();
            console.log('[SSE] Event:', currentEvent);
          } else if (trimmed.startsWith('data:')) {
            const dataStr = trimmed.substring(5).trim();
            
            try {
              const data = JSON.parse(dataStr);
              
              const eventType = currentEvent as AnalyzeEventType;
              switch (eventType) {
                case 'progress':
                  setProgress(data as ProgressEvent);
                  break;

                case 'explain_complete':
                  console.log('[SSE] EXPLAIN complete:', data);
                  break;

                case 'rewrites_tested':
                  console.log('[SSE] Rewrites tested:', data);
                  setRewriteTesting(normalizeRewriteTesting(data));
                  break;

                case 'readyset_checked':
                  console.log('[SSE] Readyset checked:', data);
                  setReadysetCacheability(data as ReadysetCacheability);
                  break;

                case 'complete': {
                  console.log('[SSE] Analysis complete:', data);
                  const completeData = data as CompleteEvent;
                  // A failed EXPLAIN (e.g. invalid SQL) comes back on the
                  // `complete` event as top-level success with
                  // explain_results.success === false. Render it as a real
                  // error, never a zero-score "success" (B3/T3).
                  if (completeData.explain_results?.success === false) {
                    const explainError = completeData.explain_results?.error as
                      | string
                      | undefined;
                    failWith({
                      code: 'invalid_sql',
                      message: friendlySqlError(explainError),
                      detail: explainError,
                    });
                    break;
                  }
                  setResults(completeData);
                  setRewriteTesting(
                    normalizeRewriteTesting(data.rewrite_testing) ??
                      normalizeRewriteTesting(data.formatted?.rewrite_testing)
                  );
                  if (data.readyset_cacheability) {
                    setReadysetCacheability(data.readyset_cacheability);
                  }
                  setState('complete');
                  break;
                }

                case 'error':
                  console.log('[SSE] Error:', data.message);
                  failWith(normalizeSseError(data));
                  break;

                default: {
                  // Exhaustiveness guard: adding a variant to AnalyzeEventType
                  // without handling it here fails tsc. Do NOT use `as never`.
                  const _exhaustive: never = eventType;
                  console.warn('[SSE] Unknown event type (ignored):', currentEvent, data);
                  void _exhaustive;
                  break;
                }
              }
            } catch (e) {
              console.error('[SSE] Failed to parse JSON:', e);
            }
          }
        }
      }
      
    } catch (err: unknown) {
      if (err instanceof Error && err.name === 'AbortError') {
        console.log('[SSE] Request aborted by user');
        return;
      }
      console.error('[SSE] Error during analysis:', err);
      // Transport-level failure (network down, stream aborted mid-flight):
      // normalize into the shared envelope instead of a raw err.message.
      failWith({
        code: 'network_error',
        message: 'Could not reach the analysis service. Check that it is running and try again.',
        detail: err instanceof Error ? err.message : undefined,
      });
    } finally {
      abortControllerRef.current = null;
    }
  }, [failWith]);

  return {
    analyze,
    state,
    progress,
    results,
    rewriteTesting,
    readysetCacheability,
    error,
    errorEnvelope,
    reset
  };
}

// ============================================================================
// Benchmark SSE Hook
// ============================================================================

interface UseBenchmarkReturn {
  start: (request: BenchmarkRequest) => Promise<void>;
  stop: () => void;
  state: BenchmarkState;
  progress: BenchmarkProgress | undefined;
  error: string | undefined;
  reset: () => void;
}

export function useBenchmark(): UseBenchmarkReturn {
  const [state, setState] = useState<BenchmarkState>('idle');
  const [progress, setProgress] = useState<BenchmarkProgress | undefined>(undefined);
  const [error, setError] = useState<string | undefined>(undefined);
  
  const abortControllerRef = useRef<AbortController | null>(null);
  useTargetSwitchLock('benchmark', state === 'running');

  const reset = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setState('idle');
    setProgress(undefined);
    setError(undefined);
  }, []);

  const stop = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setState('complete');
  }, []);

  const start = useCallback(async (request: BenchmarkRequest) => {
    console.log('[Benchmark SSE] Starting benchmark with request:', request);
    
    if (abortControllerRef.current) {
      console.log('[Benchmark SSE] Aborting previous request');
      abortControllerRef.current.abort();
    }

    const controller = new AbortController();
    abortControllerRef.current = controller;

    setState('running');
    setProgress(undefined);
    setError(undefined);

    try {
      console.log('[Benchmark SSE] Sending POST to /api/query-registry/benchmark');
      const response = await fetch('/api/query-registry/benchmark', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(request),
        signal: controller.signal,
      });

      console.log('[Benchmark SSE] Response status:', response.status);

      if (!response.ok) {
        const errorText = await response.text();
        console.error('[Benchmark SSE] HTTP error response body:', errorText);
        throw new Error(`HTTP error! status: ${response.status}, body: ${errorText}`);
      }
      
      if (!response.body) {
        throw new Error('No response body');
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let currentEvent = '';

      console.log('[Benchmark SSE] Starting to read stream...');

      while (true) {
        const { done, value } = await reader.read();
        
        if (done) {
          console.log('[Benchmark SSE] Stream done');
          if (state !== 'error') {
            setState('complete');
          }
          break;
        }
        
        const chunk = decoder.decode(value, { stream: true });
        buffer += chunk;
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          const trimmed = line.trim();
          
          if (!trimmed) {
            currentEvent = '';
            continue;
          }

          if (trimmed.startsWith('event:')) {
            currentEvent = trimmed.substring(6).trim();
            console.log('[Benchmark SSE] Event:', currentEvent);
          } else if (trimmed.startsWith('data:')) {
            const dataStr = trimmed.substring(5).trim();
            
            try {
              const data = JSON.parse(dataStr) as QueryBenchmarkEvent;
              console.log('[Benchmark SSE] Data:', data.type);

              switch (data.type) {
                case 'progress':
                  setProgress(data);
                  break;

                case 'complete':
                  console.log('[Benchmark SSE] Benchmark complete');
                  setProgress(data);
                  setState('complete');
                  break;

                case 'error': {
                  const errEvt = data as QueryBenchmarkErrorEvent;
                  console.log('[Benchmark SSE] Error:', errEvt.error);
                  setError(errEvt.error || 'Unknown error');
                  setState('error');
                  break;
                }

                default: {
                  // Exhaustiveness guard: adding a variant to
                  // QueryBenchmarkEvent['type'] without handling it here
                  // fails tsc.
                  const _exhaustive: never = data;
                  console.warn('[Benchmark SSE] Unknown benchmark event (ignored):', data);
                  void _exhaustive;
                  break;
                }
              }
            } catch (e) {
              console.error('[Benchmark SSE] Failed to parse JSON:', e);
            }
          }
        }
      }
      
    } catch (err: unknown) {
      if (err instanceof Error && err.name === 'AbortError') {
        console.log('[Benchmark SSE] Request aborted by user');
        setState('complete');
        return;
      }
      const errorMessage = err instanceof Error ? err.message : 'An error occurred';
      console.error('[Benchmark SSE] Error during benchmark:', err);
      setError(errorMessage);
      setState('error');
    } finally {
      abortControllerRef.current = null;
    }
  }, [state]);

  return {
    start,
    stop,
    state,
    progress,
    error,
    reset,
  };
}
