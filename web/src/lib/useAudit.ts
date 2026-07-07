import { useState, useCallback, useRef } from 'react';
import { useTargetSwitchLock } from './targetSwitchLock';
import { api } from './client';
import type {
  AuditEvent,
  AuditReport,
  AuditRunListResponse,
  AuditRunState,
  CaptureState,
  WorkloadAnalysis,
  WorkloadEvent,
  WorkloadRun,
  WorkloadSummary,
} from '../types/audit';

// ---------------------------------------------------------------------------
// API functions
// ---------------------------------------------------------------------------

async function throwIfNotOk(response: Response, ctx: string): Promise<void> {
  if (response.ok) return;
  const body = await response.text().catch(() => '');
  throw new Error(body || `${ctx}: ${response.status}`);
}

export async function fetchAuditRuns(target?: string): Promise<AuditRunListResponse> {
  const { data, response } = await api.GET('/api/audit/runs', {
    params: { query: target ? { target } : {} },
  });
  await throwIfNotOk(response, 'Failed to fetch audit runs');
  if (!data) throw new Error('Missing response body');
  return data;
}

// A saved run's detail payload is either an AuditReport (metrics audit) or a
// WorkloadRun (duration capture). Capture payloads carry a `queries` array and
// a `run_id`, and lack the `metrics` block of a metrics audit.
export function isWorkloadRun(payload: unknown): payload is WorkloadRun {
  if (!payload || typeof payload !== 'object') return false;
  const obj = payload as Record<string, unknown>;
  return 'queries' in obj && 'run_id' in obj && !('metrics' in obj);
}

export async function fetchRunDetail(runId: string): Promise<AuditReport | WorkloadRun> {
  const { data, response } = await api.GET('/api/audit/runs/{run_id}', {
    params: { path: { run_id: runId } },
  });
  await throwIfNotOk(response, 'Failed to fetch audit run');
  if (!data) throw new Error('Missing response body');
  return data as AuditReport | WorkloadRun;
}

// ---------------------------------------------------------------------------
// Audit run hook (SSE streaming)
// ---------------------------------------------------------------------------

interface UseAuditRunReturn {
  run: (target: string, options?: { insights?: boolean }) => Promise<void>;
  state: AuditRunState;
  statusMessage: string | undefined;
  report: AuditReport | undefined;
  snapshotId: string | undefined;
  error: string | undefined;
  reset: () => void;
  cancel: () => void;
}

export function useAuditRun(): UseAuditRunReturn {
  const [state, setState] = useState<AuditRunState>('idle');
  const [statusMessage, setStatusMessage] = useState<string | undefined>(undefined);
  const [report, setReport] = useState<AuditReport | undefined>(undefined);
  const [snapshotId, setSnapshotId] = useState<string | undefined>(undefined);
  const [error, setError] = useState<string | undefined>(undefined);

  const abortControllerRef = useRef<AbortController | null>(null);
  useTargetSwitchLock('audit', state === 'running');

  const reset = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setState('idle');
    setStatusMessage(undefined);
    setReport(undefined);
    setSnapshotId(undefined);
    setError(undefined);
  }, []);

  const cancel = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setState('idle');
    setStatusMessage(undefined);
  }, []);

  const run = useCallback(async (target: string, options?: { insights?: boolean }) => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }

    const controller = new AbortController();
    abortControllerRef.current = controller;

    setState('running');
    setStatusMessage('Starting audit...');
    setReport(undefined);
    setSnapshotId(undefined);
    setError(undefined);

    try {
      const response = await fetch('/api/audit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target, insights: options?.insights ?? true }),
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
      let terminal = false;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed || !trimmed.startsWith('data:')) continue;

          const dataStr = trimmed.substring(5).trim();
          try {
            const event = JSON.parse(dataStr) as AuditEvent;

            switch (event.type) {
              case 'status':
                setStatusMessage(event.message);
                break;
              case 'target_start':
              case 'metrics_collected':
              case 'llm_insights':
              case 'diff':
                break;
              case 'target_error':
                setError(event.error);
                break;
              case 'snapshot_saved':
                setSnapshotId(event.snapshot_id);
                break;
              case 'target_complete':
                setReport(event.result as AuditReport);
                break;
              case 'complete':
                terminal = true;
                if (event.success) {
                  setState('complete');
                } else {
                  setError((prev) => prev || 'Audit failed');
                  setState('error');
                }
                break;
              case 'error':
                terminal = true;
                setError(event.message);
                setState('error');
                break;
              default: {
                // Exhaustiveness guard: adding a variant to AuditEvent
                // without handling it here fails tsc.
                const _exhaustive: never = event;
                void _exhaustive;
                break;
              }
            }
          } catch (e) {
            if (e instanceof SyntaxError) continue;
            throw e;
          }
        }
      }

      // A stream that ends without a terminal event (dropped connection)
      // must not leave the run stuck as running with the target-switch
      // lock engaged.
      if (!terminal) {
        setError((prev) => prev || 'Audit stream ended before completing');
        setState('error');
      }
    } catch (err: unknown) {
      if (err instanceof Error && err.name === 'AbortError') {
        return;
      }
      setError(err instanceof Error ? err.message : 'An error occurred');
      setState('error');
    } finally {
      abortControllerRef.current = null;
    }
  }, []);

  return { run, state, statusMessage, report, snapshotId, error, reset, cancel };
}

// ---------------------------------------------------------------------------
// Workload capture hook (long-lived SSE streaming)
// ---------------------------------------------------------------------------

export interface CaptureProgress {
  elapsedSeconds: number;
  totalSeconds: number | undefined;
  uniqueQueries: number;
  totalExecutions: number;
  cacheHitRatio: number | null | undefined;
  activeConnections: number;
  tps: number;
}

export interface CaptureResult {
  runId: string;
  summary: WorkloadSummary | undefined;
  analysis: WorkloadAnalysis | null | undefined;
}

interface UseAuditCaptureReturn {
  run: (target: string, options?: { duration?: number; analysis?: boolean }) => Promise<void>;
  cancel: () => void;
  reset: () => void;
  state: CaptureState;
  statusMessage: string | undefined;
  analysisWarning: string | undefined;
  progress: CaptureProgress | undefined;
  result: CaptureResult | undefined;
  error: string | undefined;
}

const EMPTY_PROGRESS: CaptureProgress = {
  elapsedSeconds: 0,
  totalSeconds: undefined,
  uniqueQueries: 0,
  totalExecutions: 0,
  cacheHitRatio: undefined,
  activeConnections: 0,
  tps: 0,
};

export function useAuditCapture(): UseAuditCaptureReturn {
  const [state, setState] = useState<CaptureState>('idle');
  const [statusMessage, setStatusMessage] = useState<string | undefined>(undefined);
  const [analysisWarning, setAnalysisWarning] = useState<string | undefined>(undefined);
  const [progress, setProgress] = useState<CaptureProgress | undefined>(undefined);
  const [result, setResult] = useState<CaptureResult | undefined>(undefined);
  const [error, setError] = useState<string | undefined>(undefined);

  const abortControllerRef = useRef<AbortController | null>(null);
  useTargetSwitchLock('audit', state === 'capturing' || state === 'analyzing');

  const reset = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setState('idle');
    setStatusMessage(undefined);
    setAnalysisWarning(undefined);
    setProgress(undefined);
    setResult(undefined);
    setError(undefined);
  }, []);

  const cancel = useCallback(() => {
    // Aborting the fetch cancels the capture server-side and releases the
    // database connection.
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setState('idle');
    setStatusMessage(undefined);
    setProgress(undefined);
  }, []);

  const run = useCallback(
    async (target: string, options?: { duration?: number; analysis?: boolean }) => {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }

      const controller = new AbortController();
      abortControllerRef.current = controller;

      setState('capturing');
      setStatusMessage('Starting capture...');
      setAnalysisWarning(undefined);
      setProgress({ ...EMPTY_PROGRESS, totalSeconds: options?.duration });
      setResult(undefined);
      setError(undefined);

      try {
        const response = await fetch('/api/audit/capture', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            target,
            duration: options?.duration ?? 60,
            analysis: options?.analysis ?? true,
          }),
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
        let terminal = false;

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() || '';

          for (const line of lines) {
            const trimmed = line.trim();
            if (!trimmed || !trimmed.startsWith('data:')) continue;

            const dataStr = trimmed.substring(5).trim();
            let event: WorkloadEvent;
            try {
              event = JSON.parse(dataStr) as WorkloadEvent;
            } catch (e) {
              if (e instanceof SyntaxError) continue;
              throw e;
            }

            switch (event.type) {
              case 'status':
                setStatusMessage(event.message);
                // Graceful degradation: a failed analysis arrives as a status
                // with phase=analysis. Surface it as a muted warning.
                if (event.phase === 'analysis' && /^Analysis failed/i.test(event.message)) {
                  setAnalysisWarning(event.message);
                }
                break;
              case 'connected':
                break;
              case 'snapshot':
                break;
              case 'capture_progress':
                setProgress((prev) => ({
                  elapsedSeconds: event.elapsed_seconds,
                  totalSeconds: event.total_seconds ?? prev?.totalSeconds,
                  uniqueQueries: event.unique_queries ?? 0,
                  totalExecutions: event.total_executions ?? 0,
                  cacheHitRatio: event.cache_hit_ratio,
                  activeConnections: event.active_connections ?? 0,
                  tps: event.tps ?? 0,
                }));
                break;
              case 'capture_complete':
                setState('analyzing');
                setProgress((prev) => ({
                  ...(prev ?? EMPTY_PROGRESS),
                  elapsedSeconds: event.duration_seconds,
                  totalSeconds: event.duration_seconds,
                  uniqueQueries: event.unique_queries,
                  totalExecutions: event.total_executions,
                }));
                break;
              case 'analysis_progress':
                setStatusMessage(event.message);
                break;
              case 'queries_saved':
                break;
              case 'complete':
                terminal = true;
                if (event.success) {
                  setResult({
                    runId: event.run_id,
                    summary: (event.summary as WorkloadSummary | null) ?? undefined,
                    analysis: (event.analysis as WorkloadAnalysis | null) ?? undefined,
                  });
                  setState('complete');
                } else {
                  setError((prev) => prev || 'Capture failed');
                  setState('error');
                }
                break;
              case 'error':
                terminal = true;
                setError(event.message);
                setState('error');
                break;
              default: {
                // Exhaustiveness guard: adding a variant to WorkloadEvent
                // without handling it here fails tsc.
                const _exhaustive: never = event;
                void _exhaustive;
                break;
              }
            }
          }
        }

        // A stream that ends without a terminal event (dropped connection)
        // must not leave the capture stuck with the target-switch lock
        // engaged.
        if (!terminal) {
          setError((prev) => prev || 'Capture stream ended before completing');
          setState('error');
        }
      } catch (err: unknown) {
        if (err instanceof Error && err.name === 'AbortError') {
          return;
        }
        setError(err instanceof Error ? err.message : 'An error occurred');
        setState('error');
      } finally {
        abortControllerRef.current = null;
      }
    },
    [],
  );

  return {
    run,
    cancel,
    reset,
    state,
    statusMessage,
    analysisWarning,
    progress,
    result,
    error,
  };
}
