/**
 * Hook for Top Queries SSE streaming and historical fetch
 */

import { useState, useCallback, useRef } from 'react';
import { useTargetSwitchLock } from './targetSwitchLock';
import type { components } from './api.generated';
import type {
  TopQuery,
  TopState,
  TopConnectionInfo,
  TopSourceFallback,
  TopDbLimitWarningEventData,
  TopHistoricalResponse,
} from '../types/top';

// SSE event types are derived from the backend-generated discriminated union.
// Backend source of truth: rdst/features/top/events.py (TopEvent).
type TopEvent = components['schemas']['TopEvent'];
export type TopEventType = TopEvent['type'];

type TopConnectedEvent = Extract<TopEvent, { type: 'connected' }>;
type TopSourceFallbackEvent = Extract<TopEvent, { type: 'source_fallback' }>;
type TopDbLimitWarningEvent = Extract<TopEvent, { type: 'db_limit_warning' }>;
type TopQueriesEvent = Extract<TopEvent, { type: 'queries' }>;
type TopQuerySavedEvent = Extract<TopEvent, { type: 'query_saved' }>;
type TopCompleteEvent = Extract<TopEvent, { type: 'complete' }>;
type TopErrorEvent = Extract<TopEvent, { type: 'error' }>;

interface UseTopOptions {
  limit?: number;
  source?: 'auto' | 'pg_stat' | 'activity' | 'digest';
  sort?: 'total_time' | 'freq' | 'avg_time' | 'load';
  filter_pattern?: string;
  duration?: number;
  auto_save?: boolean;
  min_freq?: number;
  min_load_pct?: number;
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
  dbLimitWarning: TopDbLimitWarningEventData | null;
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
  const [dbLimitWarning, setDbLimitWarning] = useState<TopDbLimitWarningEventData | null>(null);
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
    setDbLimitWarning(null);
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
    setDbLimitWarning(null);
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
      if (options?.min_freq && options.min_freq > 0) {
        params.set('min_freq', String(options.min_freq));
      }
      if (options?.min_load_pct && options.min_load_pct > 0) {
        params.set('min_load_pct', String(options.min_load_pct));
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
      if (data.db_limit_warning) {
        setDbLimitWarning(data.db_limit_warning);
      }
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
    setDbLimitWarning(null);
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
    if (options?.min_freq && options.min_freq > 0) {
      params.set('min_freq', String(options.min_freq));
    }
    if (options?.min_load_pct && options.min_load_pct > 0) {
      params.set('min_load_pct', String(options.min_load_pct));
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

                const eventType = currentEvent as TopEventType;
                switch (eventType) {
                  case 'status':
                    // Status messages - could log or update UI
                    break;

                  case 'connected': {
                    const connData = data as TopConnectedEvent;
                    setConnectionInfo({
                      target: connData.target_name,
                      engine: connData.db_engine,
                      source: connData.source,
                    });
                    break;
                  }

                  case 'source_fallback': {
                    const fallbackData = data as TopSourceFallbackEvent;
                    setSourceFallback({
                      from_source: fallbackData.from_source,
                      to_source: fallbackData.to_source,
                      reason: fallbackData.reason,
                    });
                    break;
                  }

                  case 'db_limit_warning': {
                    const warningData = data as TopDbLimitWarningEvent;
                    // Strip the discriminant before storing.
                    const { type: _t, ...payload } = warningData;
                    void _t;
                    setDbLimitWarning(payload);
                    break;
                  }

                  case 'queries': {
                    const queriesData = data as TopQueriesEvent;
                    // Backend TopQueryData differs slightly from the UI TopQuery
                    // shape (e.g., `current_instances` vs `current_instances_running`);
                    // cast via unknown — pre-existing field-name divergence.
                    setQueries(queriesData.queries as unknown as TopQuery[]);
                    if (queriesData.runtime_seconds != null) {
                      setRuntimeSeconds(queriesData.runtime_seconds);
                    }
                    if (queriesData.total_tracked != null) {
                      setTotalTracked(queriesData.total_tracked);
                    }
                    break;
                  }

                  case 'query_saved': {
                    const savedData = data as TopQuerySavedEvent;
                    if (savedData.is_new) {
                      setNewlySaved((prev) => prev + 1);
                      setSavedHashes((prev) => {
                        if (prev.has(savedData.query_hash)) return prev;
                        return new Set(prev).add(savedData.query_hash);
                      });
                    }
                    break;
                  }

                  case 'complete': {
                    const completeData = data as TopCompleteEvent;
                    setQueries(completeData.queries as unknown as TopQuery[]);
                    setNewlySaved(completeData.newly_saved);
                    setState('complete');
                    break;
                  }

                  case 'error': {
                    const errorData = data as TopErrorEvent;
                    setError(errorData.message);
                    setState('error');
                    break;
                  }

                  default: {
                    // Exhaustiveness guard: adding a variant to TopEventType
                    // without handling it here fails tsc. Do NOT use `as never`.
                    const _exhaustive: never = eventType;
                    console.warn('[Top SSE] Unknown event type (ignored):', currentEvent, data);
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
    dbLimitWarning,
    runtimeSeconds,
    totalTracked,
    newlySaved,
    savedHashes,
    error,
  };
}
