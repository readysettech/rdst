/**
 * Hook for Configure target management with SSE streaming for connection tests
 */

import { useState, useCallback, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { api } from './client';
import type {
  ConfigureTarget,
  ConfigureTargetDetail,
  ConfigureFormData,
  ConfigureState,
  ConfigureConnectionStatus,
} from '../types/configure';

async function throwIfNotOk(response: Response, ctx: string): Promise<void> {
  if (response.ok) return;
  const body = await response.text().catch(() => '');
  throw new Error(body || `${ctx}: ${response.status}`);
}

interface UseConfigureReturn {
  // Actions
  listTargets: () => Promise<void>;
  getTarget: (name: string) => Promise<ConfigureTargetDetail | null>;
  addTarget: (data: ConfigureFormData) => Promise<void>;
  updateTarget: (name: string, data: ConfigureFormData) => Promise<void>;
  removeTarget: (name: string) => Promise<void>;
  setDefaultTarget: (name: string) => Promise<void>;
  testConnection: (name: string) => void;
  stopTest: () => void;

  // State
  state: ConfigureState;
  targets: ConfigureTarget[];
  defaultTarget: string | null;
  connectionTestResult: ConfigureConnectionStatus | null;
  error: string | null;
  loading: boolean;
}

export function useConfigure(): UseConfigureReturn {
  const queryClient = useQueryClient();
  const [state, setState] = useState<ConfigureState>('idle');
  const [targets, setTargets] = useState<ConfigureTarget[]>([]);
  const [defaultTarget, setDefaultTargetState] = useState<string | null>(null);
  const [connectionTestResult, setConnectionTestResult] = useState<ConfigureConnectionStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const abortControllerRef = useRef<AbortController | null>(null);

  // Invalidate the status query so TargetDropdown updates
  const invalidateStatus = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['status'] });
  }, [queryClient]);

  // List all targets
  const listTargets = useCallback(async () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }

    const controller = new AbortController();
    abortControllerRef.current = controller;

    setState('loading');
    setLoading(true);
    setError(null);

    try {
      const result = await api.GET('/api/configure/targets', { signal: controller.signal });
      await throwIfNotOk(result.response, 'Failed to list targets');
      const data = result.data;
      if (!data) throw new Error('Missing response body');
      // Union narrow: ErrorResponse has `message`; TargetListResponse has `targets`.
      if (!('targets' in data)) {
        throw new Error(data.message);
      }
      setTargets(data.targets);
      setDefaultTargetState(data.default_target ?? null);
      setState('success');
    } catch (err: unknown) {
      if (err instanceof Error && err.name === 'AbortError') {
        return;
      }
      const errorMessage = err instanceof Error ? err.message : 'Failed to list targets';
      setError(errorMessage);
      setState('error');
    } finally {
      setLoading(false);
      abortControllerRef.current = null;
    }
  }, []);

  const getTarget = useCallback(async (name: string) => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }

    const controller = new AbortController();
    abortControllerRef.current = controller;

    setState('loading');
    setLoading(true);
    setError(null);

    try {
      const result = await api.GET('/api/configure/targets/{name}', {
        params: { path: { name } },
        signal: controller.signal,
      });
      await throwIfNotOk(result.response, 'Failed to load target');
      const data = result.data;
      if (!data) throw new Error('Missing response body');
      if (!('target_name' in data)) {
        throw new Error(data.message);
      }
      const detail = data as typeof data & { password_env?: string | null };

      setState('success');
      return {
        name: detail.target_name,
        engine: detail.engine,
        host: detail.host,
        port: detail.port,
        database: detail.database,
        user: detail.user,
        password_env: detail.password_env ?? undefined,
        tls: detail.tls ?? false,
        read_only: detail.read_only ?? false,
        has_password: detail.has_password,
        is_default: detail.is_default,
      };
    } catch (err: unknown) {
      if (err instanceof Error && err.name === 'AbortError') {
        return null;
      }
      const errorMessage = err instanceof Error ? err.message : 'Failed to load target';
      setError(errorMessage);
      setState('error');
      return null;
    } finally {
      setLoading(false);
      abortControllerRef.current = null;
    }
  }, []);

  // Add new target
  const addTarget = useCallback(async (data: ConfigureFormData) => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }

    const controller = new AbortController();
    abortControllerRef.current = controller;

    setState('loading');
    setLoading(true);
    setError(null);

    try {
      const result = await api.POST('/api/configure/targets', {
        body: {
          name: data.name,
          target: {
            engine: data.engine,
            host: data.host,
            port: data.port,
            database: data.database,
            user: data.user,
            password_env: data.password_env,
            tls: data.tls ?? false,
            read_only: data.read_only ?? false,
          },
        },
        signal: controller.signal,
      });
      await throwIfNotOk(result.response, 'Failed to add target');
      if (!result.data) throw new Error('Missing response body');
      if (!result.data.success) {
        throw new Error(result.data.message || 'Failed to add target');
      }

      setState('success');
      // Refresh target list and invalidate status for header dropdown
      await listTargets();
      invalidateStatus();
    } catch (err: unknown) {
      if (err instanceof Error && err.name === 'AbortError') {
        return;
      }
      const errorMessage = err instanceof Error ? err.message : 'Failed to add target';
      setError(errorMessage);
      setState('error');
    } finally {
      setLoading(false);
      abortControllerRef.current = null;
    }
  }, [listTargets, invalidateStatus]);

  // Update existing target
  const updateTarget = useCallback(async (name: string, data: ConfigureFormData) => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }

    const controller = new AbortController();
    abortControllerRef.current = controller;

    setState('loading');
    setLoading(true);
    setError(null);

    try {
      const result = await api.PUT('/api/configure/targets/{name}', {
        params: { path: { name } },
        body: {
          target: {
            engine: data.engine,
            host: data.host,
            port: data.port,
            database: data.database,
            user: data.user,
            password_env: data.password_env,
            tls: data.tls,
            read_only: data.read_only,
          },
        },
        signal: controller.signal,
      });
      await throwIfNotOk(result.response, 'Failed to update target');
      if (!result.data) throw new Error('Missing response body');
      if (!result.data.success) {
        throw new Error(result.data.message || 'Failed to update target');
      }

      setState('success');
      // Refresh target list
      await listTargets();
    } catch (err: unknown) {
      if (err instanceof Error && err.name === 'AbortError') {
        return;
      }
      const errorMessage = err instanceof Error ? err.message : 'Failed to update target';
      setError(errorMessage);
      setState('error');
    } finally {
      setLoading(false);
      abortControllerRef.current = null;
    }
  }, [listTargets]);

  // Remove target
  const removeTarget = useCallback(async (name: string) => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }

    const controller = new AbortController();
    abortControllerRef.current = controller;

    setState('loading');
    setLoading(true);
    setError(null);

    try {
      const result = await api.DELETE('/api/configure/targets/{name}', {
        params: { path: { name } },
        signal: controller.signal,
      });
      await throwIfNotOk(result.response, 'Failed to remove target');
      if (!result.data) throw new Error('Missing response body');
      if (!result.data.success) {
        throw new Error(result.data.message || 'Failed to remove target');
      }

      setState('success');
      // Refresh target list
      await listTargets();
      invalidateStatus();
    } catch (err: unknown) {
      if (err instanceof Error && err.name === 'AbortError') {
        return;
      }
      const errorMessage = err instanceof Error ? err.message : 'Failed to remove target';
      setError(errorMessage);
      setState('error');
    } finally {
      setLoading(false);
      abortControllerRef.current = null;
    }
  }, [listTargets, invalidateStatus]);

  // Set default target
  const setDefaultTarget = useCallback(async (name: string) => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }

    const controller = new AbortController();
    abortControllerRef.current = controller;

    setState('loading');
    setLoading(true);
    setError(null);

    try {
      const result = await api.PUT('/api/configure/default', {
        body: { name },
        signal: controller.signal,
      });
      await throwIfNotOk(result.response, 'Failed to set default target');
      if (!result.data) throw new Error('Missing response body');
      if (!result.data.success) {
        throw new Error(result.data.message || 'Failed to set default target');
      }

      setState('success');
      setDefaultTargetState(name);
      // Refresh target list to update is_default flags
      await listTargets();
    } catch (err: unknown) {
      if (err instanceof Error && err.name === 'AbortError') {
        return;
      }
      const errorMessage = err instanceof Error ? err.message : 'Failed to set default target';
      setError(errorMessage);
      setState('error');
    } finally {
      setLoading(false);
      abortControllerRef.current = null;
    }
  }, [listTargets]);

  // Test connection (SSE streaming)
  const testConnection = useCallback((name: string) => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }

    const controller = new AbortController();
    abortControllerRef.current = controller;

    setState('loading');
    setLoading(true);
    setConnectionTestResult(null);
    setError(null);

    const streamSSE = async () => {
      try {
        const response = await fetch(`/api/configure/targets/${name}/test`, {
          method: 'POST',
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
            setState((prev) => (prev === 'loading' ? 'success' : prev));
            setLoading(false);
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

                  case 'connection_test': {
                    const isSuccess = data.status === 'success';
                    setConnectionTestResult({
                      target: data.target_name,
                      connected: isSuccess,
                      error: isSuccess ? undefined : data.message,
                      engine: data.server_version,
                    });
                    break;
                  }

                  case 'success':
                    setState('success');
                    setLoading(false);
                    break;

                  case 'error':
                    setError(data.message || 'Connection test failed');
                    setState('error');
                    setLoading(false);
                    break;

                  case 'unknown':
                    console.warn('[Configure SSE] Unknown event payload (ignored):', data);
                    break;

                  default:
                    console.warn('[Configure SSE] Unknown event type (ignored):', currentEvent, data);
                    break;
                }
              } catch (parseErr) {
                console.error('Failed to parse SSE data:', parseErr);
              }
            }
          }
        }
      } catch (err: unknown) {
        if (err instanceof Error && err.name === 'AbortError') {
          return;
        }
        const errorMessage = err instanceof Error ? err.message : 'Connection test failed';
        setError(errorMessage);
        setState('error');
        setLoading(false);
      } finally {
        abortControllerRef.current = null;
      }
    };

    streamSSE();
  }, []);

  // Stop connection test
  const stopTest = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    if (state === 'loading') {
      setState('idle');
      setLoading(false);
    }
  }, [state]);

  return {
    // Actions
    listTargets,
    getTarget,
    addTarget,
    updateTarget,
    removeTarget,
    setDefaultTarget,
    testConnection,
    stopTest,

    // State
    state,
    targets,
    defaultTarget,
    connectionTestResult,
    error,
    loading,
  };
}
