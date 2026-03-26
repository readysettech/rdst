import { useState, useCallback, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useTargetSwitchLock } from './targetSwitchLock';
import type { ProgressEvent } from './api';
import type {
  CacheDeployState,
  CacheDeployRequest,
  CacheStatusResponse,
  CacheListResponse,
  CacheAddRequest,
  CacheAddResponse,
  CacheRunRequest,
  CacheRunResult,
  CacheRunState,
} from '../types/cache';

// ---------------------------------------------------------------------------
// API functions
// ---------------------------------------------------------------------------

export async function fetchCacheStatus(target: string): Promise<CacheStatusResponse> {
  const response = await fetch(`/api/cache/status?target=${encodeURIComponent(target)}`);
  if (!response.ok) {
    throw new Error(`Failed to fetch cache status: ${response.status}`);
  }
  return response.json();
}

export async function fetchCacheList(target: string): Promise<CacheListResponse> {
  const response = await fetch(`/api/cache/list?target=${encodeURIComponent(target)}`);
  if (!response.ok) {
    throw new Error(`Failed to fetch cache list: ${response.status}`);
  }
  return response.json();
}

export async function addCacheQuery(request: CacheAddRequest): Promise<CacheAddResponse> {
  const response = await fetch('/api/cache/add', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Failed to add cache: ${response.status}`);
  }
  return response.json();
}

export async function deleteCacheQuery(cacheId: string, target: string): Promise<{ success: boolean }> {
  const response = await fetch(
    `/api/cache/${encodeURIComponent(cacheId)}?target=${encodeURIComponent(target)}`,
    { method: 'DELETE' },
  );
  if (!response.ok) {
    throw new Error(`Failed to delete cache: ${response.status}`);
  }
  return response.json();
}

export async function removeCacheTarget(target: string): Promise<{ success: boolean }> {
  const response = await fetch(
    `/api/cache/remove?target=${encodeURIComponent(target)}`,
    { method: 'DELETE' },
  );
  if (!response.ok) {
    throw new Error(`Failed to remove cache: ${response.status}`);
  }
  return response.json();
}

export async function dropAllCacheQueries(target: string): Promise<{ success: boolean }> {
  const response = await fetch(
    `/api/cache/drop-all?target=${encodeURIComponent(target)}`,
    { method: 'DELETE' },
  );
  if (!response.ok) {
    throw new Error(`Failed to drop all caches: ${response.status}`);
  }
  return response.json();
}

// ---------------------------------------------------------------------------
// Deploy + Cache in one shot (for inline use from Top/Registry/Scan)
// ---------------------------------------------------------------------------

class _PermanentError extends Error {}

/**
 * Deploys cache if needed, then creates a cache for the query.
 * Consumes the deploy SSE stream internally. Throws on failure.
 */
export async function deployAndCache(
  target: string,
  query: string,
): Promise<{ cached: boolean; deployed: boolean }> {
  // 1. Check status
  const status = await fetchCacheStatus(target);

  // 2. Deploy if needed
  let freshDeploy = false;
  if (!status.deployed) {
    await _awaitDeploy(target);
    freshDeploy = true;
  }

  // 3. Dry-run check + create with retry
  // After fresh deploy, ReadySet needs time to accept connections.
  // After existing deploy, retry handles transient connection errors.
  const maxRetries = freshDeploy ? 8 : 3;
  const delayMs = freshDeploy ? 2500 : 1000;
  let lastError: Error | null = null;

  for (let attempt = 0; attempt < maxRetries; attempt++) {
    if (attempt > 0) await new Promise((r) => setTimeout(r, delayMs));
    try {
      const check = await addCacheQuery({ query, target, dry_run: true });
      // Distinguish "not supported" (permanent) from connection/server errors (transient)
      if (!check.success && check.error) {
        // Server/connection error — retriable
        throw new Error(check.error);
      }
      if (check.success && !check.supported) {
        // Query genuinely not supported — don't retry
        throw new _PermanentError(check.detail || 'Query cannot be cached by ReadySet');
      }
      const result = await addCacheQuery({ query, target, dry_run: false });
      if (!result.success) {
        if (result.error) throw new Error(result.error);
        throw new Error(result.detail || 'Failed to create cache');
      }
      return { cached: true, deployed: freshDeploy };
    } catch (e) {
      if (e instanceof _PermanentError) throw e;
      lastError = e instanceof Error ? e : new Error(String(e));
    }
  }

  throw lastError!;
}

async function _awaitDeploy(target: string, signal?: AbortSignal): Promise<void> {
  const response = await fetch('/api/cache/deploy', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ target, mode: 'docker' }),
    signal,
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Deploy failed: ${text}`);
  }

  if (!response.body) throw new Error('No response body from deploy');

  // Consume SSE stream until complete or error
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let currentEvent = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() || '';

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed) { currentEvent = ''; continue; }
      if (trimmed.startsWith('event:')) {
        currentEvent = trimmed.substring(6).trim();
      } else if (trimmed.startsWith('data:')) {
        const dataStr = trimmed.substring(5).trim();
        try {
          const data = JSON.parse(dataStr);
          if (currentEvent === 'complete') return;
          if (currentEvent === 'error') {
            throw new Error(data.message || 'Deploy failed');
          }
        } catch (e) {
          if (e instanceof SyntaxError) continue;
          throw e;
        }
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Deploy hook (SSE streaming — for cache page with progress UI)
// ---------------------------------------------------------------------------

interface UseCacheDeployReturn {
  deploy: (request: CacheDeployRequest) => Promise<void>;
  state: CacheDeployState;
  progress: ProgressEvent | undefined;
  result: CacheStatusResponse | undefined;
  error: string | undefined;
  reset: () => void;
  cancel: () => void;
}

export function useCacheDeploy(): UseCacheDeployReturn {
  const queryClient = useQueryClient();
  const [state, setState] = useState<CacheDeployState>('idle');
  const [progress, setProgress] = useState<ProgressEvent | undefined>(undefined);
  const [result, setResult] = useState<CacheStatusResponse | undefined>(undefined);
  const [error, setError] = useState<string | undefined>(undefined);

  const abortControllerRef = useRef<AbortController | null>(null);
  useTargetSwitchLock('cache-deploy', state === 'deploying');

  const reset = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setState('idle');
    setProgress(undefined);
    setResult(undefined);
    setError(undefined);
  }, []);

  const cancel = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setState('idle');
  }, []);

  const deploy = useCallback(async (request: CacheDeployRequest) => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }

    const controller = new AbortController();
    abortControllerRef.current = controller;

    setState('deploying');
    setProgress(undefined);
    setResult(undefined);
    setError(undefined);

    try {
      const response = await fetch('/api/cache/deploy', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request),
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
                case 'complete': {
                  const statusData = data as CacheStatusResponse;
                  setResult(statusData);
                  // Seed cache-status query so the page transitions immediately,
                  // even for non-Docker deploys where get_status() can't detect
                  // the deployment (no local container to check).
                  queryClient.setQueryData(
                    ['cache-status', request.target],
                    statusData,
                  );
                  setState('complete');
                  break;
                }
                case 'error':
                  setError(data.message);
                  setState('error');
                  break;
                default:
                  break;
              }
            } catch {
              console.error('[Cache Deploy SSE] Failed to parse JSON:', dataStr);
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
  }, [queryClient]);

  return { deploy, state, progress, result, error, reset, cancel };
}


// ---------------------------------------------------------------------------
// useCacheRun — run a query against origin + cache and compare latency
// ---------------------------------------------------------------------------

interface UseCacheRunReturn {
  run: (request: CacheRunRequest) => Promise<void>;
  state: CacheRunState;
  progress: ProgressEvent | undefined;
  result: CacheRunResult | undefined;
  error: string | undefined;
  reset: () => void;
  cancel: () => void;
}

export function useCacheRun(): UseCacheRunReturn {
  const [state, setState] = useState<CacheRunState>('idle');
  const [progress, setProgress] = useState<ProgressEvent | undefined>(undefined);
  const [result, setResult] = useState<CacheRunResult | undefined>(undefined);
  const [error, setError] = useState<string | undefined>(undefined);

  const abortControllerRef = useRef<AbortController | null>(null);
  useTargetSwitchLock('cache-run', state === 'running');

  const reset = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setState('idle');
    setProgress(undefined);
    setResult(undefined);
    setError(undefined);
  }, []);

  const cancel = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setState('idle');
  }, []);

  const run = useCallback(async (request: CacheRunRequest) => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }

    const controller = new AbortController();
    abortControllerRef.current = controller;

    setState('running');
    setProgress(undefined);
    setResult(undefined);
    setError(undefined);

    try {
      const response = await fetch('/api/cache/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request),
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
                  setResult(data as CacheRunResult);
                  setState('complete');
                  break;
                case 'error':
                  setError(data.message);
                  setState('error');
                  break;
                default:
                  break;
              }
            } catch {
              console.error('[Cache Run SSE] Failed to parse JSON:', dataStr);
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

  return { run, state, progress, result, error, reset, cancel };
}
