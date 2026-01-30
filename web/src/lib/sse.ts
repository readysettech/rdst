import { useState, useCallback, useRef } from 'react';
import { useTargetSwitchLock } from './targetSwitchLock';
import {
  AnalyzeRequest,
  AnalysisState,
  ProgressEvent,
  CompleteEvent,
  RewriteTesting,
  ReadysetCacheability,
  BenchmarkRequest,
  BenchmarkProgress,
  BenchmarkState,
} from './api';

interface UseAnalyzeReturn {
  analyze: (request: AnalyzeRequest) => Promise<void>;
  state: AnalysisState;
  progress: ProgressEvent | undefined;
  results: CompleteEvent | undefined;
  rewriteTesting: RewriteTesting | undefined;
  readysetCacheability: ReadysetCacheability | undefined;
  error: string | undefined;
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
  
  const abortControllerRef = useRef<AbortController | null>(null);
  useTargetSwitchLock('analyze', state === 'analyzing');

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
        const errorText = await response.text();
        console.error('[SSE] HTTP error response body:', errorText);
        throw new Error(`HTTP error! status: ${response.status}, body: ${errorText}`);
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
              
              switch (currentEvent) {
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
                  
                case 'complete':
                  console.log('[SSE] Analysis complete:', data);
                  setResults(data as CompleteEvent);
                  setRewriteTesting(
                    normalizeRewriteTesting(data.rewrite_testing) ??
                      normalizeRewriteTesting(data.formatted?.rewrite_testing)
                  );
                  if (data.readyset_cacheability) {
                    setReadysetCacheability(data.readyset_cacheability);
                  }
                  setState('complete');
                  break;
                  
                case 'error':
                  console.log('[SSE] Error:', data.message);
                  setError(data.message);
                  setState('error');
                  break;

                case 'unknown':
                  console.warn('[SSE] Unknown event payload (ignored):', data);
                  break;
                    
                default:
                  console.warn('[SSE] Unknown event type (ignored):', currentEvent, data);
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
      const errorMessage = err instanceof Error ? err.message : 'An error occurred';
      console.error('[SSE] Error during analysis:', err);
      setError(errorMessage);
      setState('error');
    } finally {
      abortControllerRef.current = null;
    }
  }, []);

  return {
    analyze,
    state,
    progress,
    results,
    rewriteTesting,
    readysetCacheability,
    error,
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
              const data = JSON.parse(dataStr) as BenchmarkProgress;
              console.log('[Benchmark SSE] Data:', data.type, 'executions:', data.total_executions);
              
              setProgress(data);

              if (data.type === 'complete') {
                console.log('[Benchmark SSE] Benchmark complete');
                setState('complete');
              } else if (data.type === 'error') {
                console.log('[Benchmark SSE] Error:', data.error);
                setError(data.error || 'Unknown error');
                setState('error');
              } else if (currentEvent === 'unknown') {
                console.warn('[Benchmark SSE] Unknown event payload (ignored):', data);
              } else if (data.type !== 'progress') {
                console.warn('[Benchmark SSE] Unknown benchmark data type (ignored):', data.type, data);
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
