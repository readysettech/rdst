import { useState, useCallback, useRef } from 'react';
import { api } from './client';
import type {
  AuditEvent,
  FleetConnectivityEvent,
  FleetDiffResponse,
  FleetEvent,
  FleetImportCompleteEvent,
  FleetImportProgressEvent,
  FleetSnapshotListResponse,
  FleetStreamState,
  FleetTargets,
} from '../types/fleet';
import type { components } from './api.generated';
import type { AuditReport } from '../types/audit';

// ---------------------------------------------------------------------------
// API functions
// ---------------------------------------------------------------------------

export async function fetchFleetTargets(group?: string): Promise<FleetTargets> {
  const { data, response } = await api.GET('/api/fleet/targets', {
    params: { query: group ? { group } : {} },
  });
  if (!response.ok) {
    const body = await response.text().catch(() => '');
    throw new Error(body || `Failed to fetch fleet targets: ${response.status}`);
  }
  if (!data) throw new Error('Missing response body');
  // Members travel as free-form dicts in the OpenAPI schema; FleetMember
  // narrows them to the shape FleetService.list_fleet actually emits.
  return data as unknown as FleetTargets;
}

export async function fetchFleetSnapshots(): Promise<FleetSnapshotListResponse> {
  const { data, response } = await api.GET('/api/fleet/snapshots');
  if (!response.ok) {
    const body = await response.text().catch(() => '');
    throw new Error(body || `Failed to fetch fleet snapshots: ${response.status}`);
  }
  if (!data) throw new Error('Missing response body');
  return data;
}

// Snapshot detail travels as a free-form dict in the OpenAPI schema; these
// interfaces narrow the fields the fleet page reads, mirroring the payload
// FleetService writes for a saved fleet audit.
export interface FleetSnapshotResult {
  target_name: string;
  sizing?: { verdict?: string | null } | null;
  cache_opportunity?: { score?: number | null } | null;
}

export interface FleetSnapshotDetail {
  snapshot_id: string;
  name: string;
  created_at: string;
  targets_audited: number;
  results?: FleetSnapshotResult[];
}

export interface FleetTargetVerdict {
  verdict?: string;
  cacheScore?: number;
}

/** Map a snapshot's per-target results to their sizing verdict and cache score. */
export function mapSnapshotVerdicts(
  detail: FleetSnapshotDetail | undefined,
): Record<string, FleetTargetVerdict> {
  const map: Record<string, FleetTargetVerdict> = {};
  for (const result of detail?.results ?? []) {
    if (!result.target_name) continue;
    map[result.target_name] = {
      verdict: result.sizing?.verdict ?? undefined,
      cacheScore: result.cache_opportunity?.score ?? undefined,
    };
  }
  return map;
}

export async function fetchFleetSnapshotDetail(snapshotId: string): Promise<FleetSnapshotDetail> {
  const { data, response } = await api.GET('/api/fleet/snapshots/{snapshot_id}', {
    params: { path: { snapshot_id: snapshotId } },
  });
  if (!response.ok) {
    const body = await response.text().catch(() => '');
    throw new Error(body || `Failed to fetch snapshot: ${response.status}`);
  }
  if (!data) throw new Error('Missing response body');
  return data as unknown as FleetSnapshotDetail;
}

export async function deleteFleetSnapshot(snapshotId: string): Promise<{ success: boolean }> {
  const { data, response } = await api.DELETE('/api/fleet/snapshots/{snapshot_id}', {
    params: { path: { snapshot_id: snapshotId } },
  });
  if (!response.ok) {
    const body = await response.text().catch(() => '');
    throw new Error(body || `Failed to delete snapshot: ${response.status}`);
  }
  if (!data) throw new Error('Missing response body');
  return { success: data.success };
}

export async function fetchFleetDiff(
  baseline: string,
  current: string,
): Promise<FleetDiffResponse> {
  const { data, response } = await api.GET('/api/fleet/diff', {
    params: { query: { baseline, current } },
  });
  if (!response.ok) {
    const body = await response.text().catch(() => '');
    throw new Error(body || `Failed to diff snapshots: ${response.status}`);
  }
  if (!data) throw new Error('Missing response body');
  return data;
}

// ---------------------------------------------------------------------------
// Shared SSE consumption
// ---------------------------------------------------------------------------

async function consumeFleetStream<E = FleetEvent>(
  response: Response,
  onEvent: (event: E) => void,
): Promise<void> {
  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`HTTP error ${response.status}: ${errorText}`);
  }
  if (!response.body) throw new Error('No response body');

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

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
        onEvent(JSON.parse(dataStr) as E);
      } catch (e) {
        if (!(e instanceof SyntaxError)) throw e;
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Connectivity check hook (SSE)
// ---------------------------------------------------------------------------

interface UseFleetStatusReturn {
  check: (group?: string) => Promise<void>;
  state: FleetStreamState;
  results: Record<string, FleetConnectivityEvent>;
  error: string | undefined;
  reset: () => void;
}

export function useFleetStatus(): UseFleetStatusReturn {
  const [state, setState] = useState<FleetStreamState>('idle');
  const [results, setResults] = useState<Record<string, FleetConnectivityEvent>>({});
  const [error, setError] = useState<string | undefined>(undefined);
  const abortControllerRef = useRef<AbortController | null>(null);

  const reset = useCallback(() => {
    abortControllerRef.current?.abort();
    abortControllerRef.current = null;
    setState('idle');
    setResults({});
    setError(undefined);
  }, []);

  const check = useCallback(async (group?: string) => {
    abortControllerRef.current?.abort();
    const controller = new AbortController();
    abortControllerRef.current = controller;

    setState('running');
    setResults({});
    setError(undefined);

    try {
      const params = group ? `?group=${encodeURIComponent(group)}` : '';
      const response = await fetch(`/api/fleet/status${params}`, {
        signal: controller.signal,
      });
      await consumeFleetStream(response, (event) => {
        if (event.type === 'connectivity') {
          setResults((prev) => ({ ...prev, [event.target_name]: event }));
        } else if (event.type === 'error') {
          setError(event.message);
        }
      });
      setState((prev) => (prev === 'running' ? 'complete' : prev));
    } catch (err: unknown) {
      if (err instanceof Error && err.name === 'AbortError') return;
      setError(err instanceof Error ? err.message : 'An error occurred');
      setState('error');
    } finally {
      abortControllerRef.current = null;
    }
  }, []);

  return { check, state, results, error, reset };
}

// ---------------------------------------------------------------------------
// Import / discover hooks (SSE)
//
// CSV import and AWS discover both POST a JSON body and stream the same
// FleetEvent union: per-instance `import_progress`, a terminal
// `import_complete`, and `error`. Discover additionally emits `discover`
// (instances found). They share one internal hook parameterized by URL.
// ---------------------------------------------------------------------------

interface UseFleetStreamReturn {
  run: (body: unknown) => Promise<FleetImportCompleteEvent | undefined>;
  state: FleetStreamState;
  progress: FleetImportProgressEvent[];
  result: FleetImportCompleteEvent | undefined;
  errors: string[];
  instancesFound: number | undefined;
  reset: () => void;
}

function useFleetPostStream(url: string): UseFleetStreamReturn {
  const [state, setState] = useState<FleetStreamState>('idle');
  const [progress, setProgress] = useState<FleetImportProgressEvent[]>([]);
  const [result, setResult] = useState<FleetImportCompleteEvent | undefined>(undefined);
  const [errors, setErrors] = useState<string[]>([]);
  const [instancesFound, setInstancesFound] = useState<number | undefined>(undefined);
  const abortControllerRef = useRef<AbortController | null>(null);

  const reset = useCallback(() => {
    abortControllerRef.current?.abort();
    abortControllerRef.current = null;
    setState('idle');
    setProgress([]);
    setResult(undefined);
    setErrors([]);
    setInstancesFound(undefined);
  }, []);

  const run = useCallback(
    async (body: unknown) => {
      abortControllerRef.current?.abort();
      const controller = new AbortController();
      abortControllerRef.current = controller;

      setState('running');
      setProgress([]);
      setResult(undefined);
      setErrors([]);
      setInstancesFound(undefined);

      let completion: FleetImportCompleteEvent | undefined;
      try {
        const response = await fetch(url, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
          signal: controller.signal,
        });
        await consumeFleetStream(response, (event) => {
          switch (event.type) {
            case 'discover':
              setInstancesFound(event.instances_found);
              break;
            case 'import_progress':
              setProgress((prev) => [...prev, event]);
              break;
            case 'import_complete':
              completion = event;
              setResult(event);
              setState(event.success ? 'complete' : 'error');
              break;
            case 'error':
              setErrors((prev) => [...prev, event.message]);
              setState('error');
              break;
            default:
              break;
          }
        });
        setState((prev) => (prev === 'running' ? 'complete' : prev));
      } catch (err: unknown) {
        if (err instanceof Error && err.name === 'AbortError') return undefined;
        setErrors((prev) => [...prev, err instanceof Error ? err.message : 'An error occurred']);
        setState('error');
      } finally {
        abortControllerRef.current = null;
      }
      return completion;
    },
    [url],
  );

  return { run, state, progress, result, errors, instancesFound, reset };
}

export interface FleetImportRequest {
  csv_file: string;
  password_env?: string;
  group?: string;
  tags?: string[];
  dry_run?: boolean;
}

interface UseFleetImportReturn {
  runImport: (request: FleetImportRequest) => Promise<void>;
  state: FleetStreamState;
  progress: FleetImportProgressEvent[];
  result: FleetImportCompleteEvent | undefined;
  errors: string[];
  reset: () => void;
}

export function useFleetImport(): UseFleetImportReturn {
  const { run, state, progress, result, errors, reset } = useFleetPostStream('/api/fleet/import');
  const runImport = useCallback(
    async (request: FleetImportRequest) => {
      await run(request);
    },
    [run],
  );
  return { runImport, state, progress, result, errors, reset };
}

export type FleetDiscoverRequest = components['schemas']['FleetDiscoverRequest'];

interface UseFleetDiscoverReturn {
  runDiscover: (request: FleetDiscoverRequest) => Promise<FleetImportCompleteEvent | undefined>;
  state: FleetStreamState;
  progress: FleetImportProgressEvent[];
  result: FleetImportCompleteEvent | undefined;
  errors: string[];
  instancesFound: number | undefined;
  reset: () => void;
}

export function useFleetDiscover(): UseFleetDiscoverReturn {
  const { run, state, progress, result, errors, instancesFound, reset } =
    useFleetPostStream('/api/fleet/discover');
  const runDiscover = useCallback((request: FleetDiscoverRequest) => run(request), [run]);
  return { runDiscover, state, progress, result, errors, instancesFound, reset };
}

// ---------------------------------------------------------------------------
// Fleet audit hook (SSE)
// ---------------------------------------------------------------------------

export interface FleetAuditRequest {
  group?: string;
  tag?: string;
  insights?: boolean;
  save?: boolean;
  save_name?: string;
}

export type FleetAuditTargetStatus = 'running' | 'done' | 'error';

export interface FleetAuditTargetState {
  status: FleetAuditTargetStatus;
  verdict?: string;
  cacheScore?: number;
  error?: string;
}

export interface FleetAuditSummary {
  targets_audited?: number;
  successes?: number;
  failures?: number;
  fleet_insights?: Record<string, unknown> | null;
}

interface UseFleetAuditReturn {
  runAudit: (request?: FleetAuditRequest) => Promise<void>;
  state: FleetStreamState;
  targets: Record<string, FleetAuditTargetState>;
  statusMessage: string | undefined;
  summary: FleetAuditSummary | undefined;
  snapshotId: string | undefined;
  error: string | undefined;
  running: boolean;
  reset: () => void;
}

export function useFleetAudit(): UseFleetAuditReturn {
  const [state, setState] = useState<FleetStreamState>('idle');
  const [targets, setTargets] = useState<Record<string, FleetAuditTargetState>>({});
  const [statusMessage, setStatusMessage] = useState<string | undefined>(undefined);
  const [summary, setSummary] = useState<FleetAuditSummary | undefined>(undefined);
  const [snapshotId, setSnapshotId] = useState<string | undefined>(undefined);
  const [error, setError] = useState<string | undefined>(undefined);
  const abortControllerRef = useRef<AbortController | null>(null);

  const reset = useCallback(() => {
    abortControllerRef.current?.abort();
    abortControllerRef.current = null;
    setState('idle');
    setTargets({});
    setStatusMessage(undefined);
    setSummary(undefined);
    setSnapshotId(undefined);
    setError(undefined);
  }, []);

  const runAudit = useCallback(async (request?: FleetAuditRequest) => {
    abortControllerRef.current?.abort();
    const controller = new AbortController();
    abortControllerRef.current = controller;

    setState('running');
    setTargets({});
    setStatusMessage(undefined);
    setSummary(undefined);
    setSnapshotId(undefined);
    setError(undefined);

    try {
      const response = await fetch('/api/fleet/audit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request ?? {}),
        signal: controller.signal,
      });
      await consumeFleetStream<AuditEvent>(response, (event) => {
        switch (event.type) {
          case 'status':
            setStatusMessage(event.message);
            break;
          case 'target_start':
            setTargets((prev) => ({
              ...prev,
              [event.target_name]: { status: 'running' },
            }));
            break;
          case 'target_complete': {
            const result = event.result as AuditReport;
            setTargets((prev) => ({
              ...prev,
              [event.target_name]: {
                status: 'done',
                verdict: result.sizing?.verdict ?? undefined,
                cacheScore: result.cache_opportunity?.score,
              },
            }));
            break;
          }
          case 'target_error':
            setTargets((prev) => ({
              ...prev,
              [event.target_name]: { status: 'error', error: event.error },
            }));
            break;
          case 'snapshot_saved':
            setSnapshotId(event.snapshot_id);
            break;
          case 'complete':
            setSummary((event.summary ?? undefined) as FleetAuditSummary | undefined);
            if (event.snapshot_id) setSnapshotId(event.snapshot_id);
            setState(event.success ? 'complete' : 'error');
            break;
          case 'error':
            setError(event.message);
            setState('error');
            break;
          default:
            break;
        }
      });
      setState((prev) => (prev === 'running' ? 'complete' : prev));
    } catch (err: unknown) {
      if (err instanceof Error && err.name === 'AbortError') return;
      setError(err instanceof Error ? err.message : 'An error occurred');
      setState('error');
    } finally {
      abortControllerRef.current = null;
    }
  }, []);

  return {
    runAudit,
    state,
    targets,
    statusMessage,
    summary,
    snapshotId,
    error,
    running: state === 'running',
    reset,
  };
}
