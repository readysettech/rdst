/**
 * Hook for Top Queries SSE streaming and historical fetch
 */

import { useState, useCallback, useRef } from 'react';
import { useTargetSwitchLock } from './targetSwitchLock';
import type {
  TopQuery,
  TopState,
  TopConnectionInfo,
  TopSourceFallback,
  TopConnectedEventData,
  TopSourceFallbackEventData,
  TopQueriesEventData,
  TopQuerySavedEventData,
  TopCompleteEventData,
  TopErrorEventData,
  TopHistoricalResponse,
} from '../types/top';

interface UseTopOptions {
  limit?: number;
  source?: 'auto' | 'pg_stat' | 'activity' | 'digest';
  sort?: 'total_time' | 'freq' | 'avg_time' | 'load';
  filter_pattern?: string;
  duration?: number;
  auto_save?: boolean;
}

interface UseTopReturn {
  // Actions
  getTop: (target: string, options?: UseTopOptions) => Promise<void>;
  startRealtime: (target: string, options?: UseTopOptions) => void;
  stopRealtime: () => void;
  reset: () => void;

  // State
  state: TopState;
  queries: TopQuery[];
  connectionInfo: TopConnectionInfo | null;
  sourceFallback: TopSourceFallback | null;
  runtimeSeconds: number;
  totalTracked: number;
  newlySaved: number;
  savedHashes: Set<string>;
  error: string | null;
}

export function useTop(): UseTopReturn {
  const [state, setState] = useState<TopState>('idle');
  const [queries, setQueries] = useState<TopQuery[]>([]);
  const [connectionInfo, setConnectionInfo] = useState<TopConnectionInfo | null>(null);
  const [sourceFallback, setSourceFallback] = useState<TopSourceFallback | null>(null);
  const [runtimeSeconds, setRuntimeSeconds] = useState(0);
  const [totalTracked, setTotalTracked] = useState(0);
  const [newlySaved, setNewlySaved] = useState(0);
  const [savedHashes, setSavedHashes] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);

  const abortControllerRef = useRef<AbortController | null>(null);
  useTargetSwitchLock('top', state === 'loading' || state === 'streaming');

  const reset = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setState('idle');
    setQueries([]);
    setConnectionInfo(null);
    setSourceFallback(null);
    setRuntimeSeconds(0);
    setTotalTracked(0);
    setNewlySaved(0);
    setSavedHashes(new Set());
    setError(null);
  }, []);

  const stopRealtime = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    if (state === 'streaming') {
      setState('complete');
    }
  }, [state]);

  // Historical one-shot fetch (JSON response)
  const getTop = useCallback(async (target: string, options?: UseTopOptions) => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }

    const controller = new AbortController();
    abortControllerRef.current = controller;

    setState('loading');
    setQueries([]);
    setConnectionInfo(null);
    setSourceFallback(null);
    setError(null);

    try {
      const params = new URLSearchParams({
        target,
        limit: String(options?.limit ?? 10),
        source: options?.source ?? 'auto',
        sort: options?.sort ?? 'total_time',
        auto_save: String(options?.auto_save ?? true),
      });
      if (options?.filter_pattern) {
        params.set('filter_pattern', options.filter_pattern);
      }

      const response = await fetch(`/api/top?${params}`, {
        method: 'GET',
        signal: controller.signal,
      });

      if (!response.ok) {
        const errorText = await response.text();
        throw new Error(`HTTP error! status: ${response.status}, body: ${errorText}`);
      }

      const data: TopHistoricalResponse = await response.json();

      if (!data.success) {
        setError(data.error || 'Unknown error');
        setState('error');
        return;
      }

      setConnectionInfo({
        target: data.target || target,
        engine: data.engine || 'unknown',
        source: data.source || 'unknown',
      });
      setQueries(data.queries || []);
      setNewlySaved(data.newly_saved || 0);
      setState('complete');
    } catch (err: unknown) {
      if (err instanceof Error && err.name === 'AbortError') {
        return;
      }
      const errorMessage = err instanceof Error ? err.message : 'An error occurred';
      setError(errorMessage);
      setState('error');
    } finally {
      abortControllerRef.current = null;
    }
  }, []);

  // Realtime SSE streaming
  const startRealtime = useCallback((target: string, options?: UseTopOptions) => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }

    const controller = new AbortController();
    abortControllerRef.current = controller;

    setState('streaming');
    setQueries([]);
    setConnectionInfo(null);
    setSourceFallback(null);
    setRuntimeSeconds(0);
    setTotalTracked(0);
    setNewlySaved(0);
    setSavedHashes(new Set());
    setError(null);

    const params = new URLSearchParams({
      target,
      limit: String(options?.limit ?? 10),
      realtime: 'true',
      auto_save: String(options?.auto_save ?? true),
    });
    if (options?.duration) {
      params.set('duration', String(options.duration));
    }

    // Use fetch with manual SSE parsing (same pattern as useAnalyze)
    const streamSSE = async () => {
      try {
        const response = await fetch(`/api/top?${params}`, {
          method: 'GET',
          signal: controller.signal,
        });

        if (!response.ok) {
          const errorText = await response.text();
          throw new Error(`HTTP error! status: ${response.status}, body: ${errorText}`);
        }

        if (!response.body) {
          throw new Error('No response body');
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let currentEvent = '';

        while (true) {
          const { done, value } = await reader.read();

          if (done) {
            setState((prev) => (prev === 'streaming' ? 'complete' : prev));
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
            } else if (trimmed.startsWith('data:')) {
              const dataStr = trimmed.substring(5).trim();

              try {
                const data = JSON.parse(dataStr);

                switch (currentEvent) {
                  case 'status':
                    // Status messages - could log or update UI
                    break;

                  case 'connected': {
                    const connData = data as TopConnectedEventData;
                    setConnectionInfo({
                      target: connData.target_name,
                      engine: connData.db_engine,
                      source: connData.source,
                    });
                    break;
                  }

                  case 'source_fallback': {
                    const fallbackData = data as TopSourceFallbackEventData;
                    setSourceFallback({
                      from_source: fallbackData.from_source,
                      to_source: fallbackData.to_source,
                      reason: fallbackData.reason,
                    });
                    break;
                  }

                  case 'queries': {
                    const queriesData = data as TopQueriesEventData;
                    setQueries(queriesData.queries);
                    if (queriesData.runtime_seconds !== undefined) {
                      setRuntimeSeconds(queriesData.runtime_seconds);
                    }
                    if (queriesData.total_tracked !== undefined) {
                      setTotalTracked(queriesData.total_tracked);
                    }
                    break;
                  }

                  case 'query_saved': {
                    const savedData = data as TopQuerySavedEventData;
                    if (savedData.is_new) {
                      setNewlySaved((prev) => prev + 1);
                      setSavedHashes((prev) => new Set(prev).add(savedData.query_hash));
                    }
                    break;
                  }

                  case 'complete': {
                    const completeData = data as TopCompleteEventData;
                    setQueries(completeData.queries);
                    setNewlySaved(completeData.newly_saved);
                    setState('complete');
                    break;
                  }

                  case 'error': {
                    const errorData = data as TopErrorEventData;
                    setError(errorData.message);
                    setState('error');
                    break;
                  }

                  case 'unknown':
                    console.warn('[Top SSE] Unknown event payload (ignored):', data);
                    break;

                  default:
                    console.warn('[Top SSE] Unknown event type (ignored):', currentEvent, data);
                    break;
                }
              } catch (e) {
                console.error('[SSE] Failed to parse JSON:', e);
              }
            }
          }
        }
      } catch (err: unknown) {
        if (err instanceof Error && err.name === 'AbortError') {
          return;
        }
        const errorMessage = err instanceof Error ? err.message : 'An error occurred';
        setError(errorMessage);
        setState('error');
      } finally {
        abortControllerRef.current = null;
      }
    };

    streamSSE();
  }, []);

  return {
    getTop,
    startRealtime,
    stopRealtime,
    reset,
    state,
    queries,
    connectionInfo,
    sourceFallback,
    runtimeSeconds,
    totalTracked,
    newlySaved,
    savedHashes,
    error,
  };
}
