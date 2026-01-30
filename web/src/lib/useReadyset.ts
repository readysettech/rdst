import { useState, useCallback, useRef } from 'react';
import { useTargetSwitchLock } from './targetSwitchLock';
import {
  ReadysetOperationState,
  ReadysetSetupResult,
  ReadysetExplainResult,
  ReadysetCreateCacheResult,
  ProgressEvent,
} from './api';

interface UseReadysetReturn {
  setupContainers: (target?: string) => Promise<void>;
  explainQuery: (query: string, target?: string) => Promise<void>;
  createCache: (query: string, target?: string) => Promise<void>;
  cancel: () => void;
  state: ReadysetOperationState;
  progress: ProgressEvent | undefined;
  setupResult: ReadysetSetupResult | undefined;
  explainResult: ReadysetExplainResult | undefined;
  cacheResult: ReadysetCreateCacheResult | undefined;
  error: string | undefined;
  reset: () => void;
}

export function useReadyset(): UseReadysetReturn {
  const [state, setState] = useState<ReadysetOperationState>('idle');
  const [progress, setProgress] = useState<ProgressEvent | undefined>(undefined);
  const [setupResult, setSetupResult] = useState<ReadysetSetupResult | undefined>(undefined);
  const [explainResult, setExplainResult] = useState<ReadysetExplainResult | undefined>(undefined);
  const [cacheResult, setCacheResult] = useState<ReadysetCreateCacheResult | undefined>(undefined);
  const [error, setError] = useState<string | undefined>(undefined);

  const abortControllerRef = useRef<AbortController | null>(null);
  useTargetSwitchLock('readyset', state === 'running');

  const reset = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setState('idle');
    setProgress(undefined);
    setSetupResult(undefined);
    setExplainResult(undefined);
    setCacheResult(undefined);
    setError(undefined);
  }, []);

  const cancel = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setState('idle');
  }, []);

  const processSSE = useCallback(async (
    url: string,
    body: Record<string, unknown>,
    onComplete: (data: unknown) => void,
  ) => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }

    const controller = new AbortController();
    abortControllerRef.current = controller;

    setState('running');
    setProgress(undefined);
    setError(undefined);

    try {
      const response = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: controller.signal,
      });

      if (!response.ok) {
        const errorText = await response.text();
        throw new Error(`HTTP error ${response.status}: ${errorText}`);
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
        if (done) break;

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
                case 'progress':
                  setProgress(data as ProgressEvent);
                  break;
                case 'complete':
                  onComplete(data);
                  setState('complete');
                  break;
                case 'error':
                  setError(data.message);
                  setState('error');
                  break;

                case 'unknown':
                  console.warn('[Readyset SSE] Unknown event payload (ignored):', data);
                  break;

                default:
                  console.warn('[Readyset SSE] Unknown event type (ignored):', currentEvent, data);
                  break;
              }
            } catch {
              console.error('[Readyset SSE] Failed to parse JSON:', dataStr);
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
  }, []);

  const setupContainers = useCallback(async (target?: string) => {
    setSetupResult(undefined);
    await processSSE('/api/readyset/setup', { target }, (data) => {
      setSetupResult(data as ReadysetSetupResult);
    });
  }, [processSSE]);

  const explainQuery = useCallback(async (query: string, target?: string) => {
    setExplainResult(undefined);
    await processSSE('/api/readyset/explain', { query, target }, (data) => {
      setExplainResult(data as ReadysetExplainResult);
    });
  }, [processSSE]);

  const createCache = useCallback(async (query: string, target?: string) => {
    setCacheResult(undefined);
    await processSSE('/api/readyset/cache', { query, target }, (data) => {
      setCacheResult(data as ReadysetCreateCacheResult);
    });
  }, [processSSE]);

  return {
    setupContainers,
    explainQuery,
    createCache,
    cancel,
    state,
    progress,
    setupResult,
    explainResult,
    cacheResult,
    error,
    reset,
  };
}
