import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from './client';
import type {
  GuardCheckResponse,
  GuardDetail,
  GuardListResponse,
  GuardWriteResponse,
} from '../types/guards';

// ---------------------------------------------------------------------------
// Query keys
// ---------------------------------------------------------------------------

const GUARDS_KEY = ['guards'] as const;
const guardDetailKey = (name: string) => ['guards', name] as const;

// ---------------------------------------------------------------------------
// API functions
// ---------------------------------------------------------------------------

// openapi-fetch parses the response body itself, so on a non-2xx status the
// parsed payload is returned as `error` (not readable again from `response`).
// FastAPI errors travel as {detail: string | {message, ...}}; prefer that
// message so callers surface the backend's reason (409 exists, 422 invalid
// name, 502 LLM failure, 423 locked target) over a bare status.
function throwIfNotOk(response: Response, error: unknown, ctx: string): void {
  if (response.ok) return;
  const detail = extractDetail(error);
  throw new Error(detail || `${ctx}: ${response.status}`);
}

function extractDetail(error: unknown): string {
  if (!error || typeof error !== 'object') {
    return typeof error === 'string' ? error : '';
  }
  const detail = (error as { detail?: unknown }).detail;
  if (typeof detail === 'string') return detail;
  if (detail && typeof detail === 'object') {
    const message = (detail as { message?: unknown }).message;
    if (typeof message === 'string') return message;
    return JSON.stringify(detail);
  }
  const message = (error as { message?: unknown }).message;
  return typeof message === 'string' ? message : '';
}

export async function fetchGuards(): Promise<GuardListResponse> {
  const { data, error, response } = await api.GET('/api/guards');
  throwIfNotOk(response, error, 'Failed to fetch guards');
  if (!data) throw new Error('Missing response body');
  return data;
}

export async function fetchGuard(name: string): Promise<GuardDetail> {
  const { data, error, response } = await api.GET('/api/guards/{name}', {
    params: { path: { name } },
  });
  throwIfNotOk(response, error, 'Failed to fetch guard');
  if (!data) throw new Error('Missing response body');
  return data;
}

export async function createGuard(detail: GuardDetail): Promise<GuardWriteResponse> {
  const { data, error, response } = await api.POST('/api/guards', { body: detail });
  throwIfNotOk(response, error, 'Failed to create guard');
  if (!data) throw new Error('Missing response body');
  return data;
}

export async function updateGuard(
  name: string,
  detail: GuardDetail,
): Promise<GuardWriteResponse> {
  const { data, error, response } = await api.PUT('/api/guards/{name}', {
    params: { path: { name } },
    body: detail,
  });
  throwIfNotOk(response, error, 'Failed to update guard');
  if (!data) throw new Error('Missing response body');
  return data;
}

export async function deleteGuard(name: string): Promise<GuardWriteResponse> {
  const { data, error, response } = await api.DELETE('/api/guards/{name}', {
    params: { path: { name } },
  });
  throwIfNotOk(response, error, 'Failed to delete guard');
  if (!data) throw new Error('Missing response body');
  return data;
}

// Derive previews a guard from a natural-language intent via an LLM. The
// result is not persisted; callers review it, then save through createGuard.
export async function deriveGuard(
  name: string,
  intent: string,
  schemaContext?: string,
): Promise<GuardDetail> {
  const { data, error, response } = await api.POST('/api/guards/derive', {
    body: { name, intent, schema_context: schemaContext ?? null },
  });
  throwIfNotOk(response, error, 'Failed to derive guard');
  if (!data) throw new Error('Missing response body');
  return data;
}

// Checks a SQL statement against a guard. Without a target, EXPLAIN-based
// checks (cost_limit / max_estimated_rows) are skipped by the backend.
export async function checkGuardSql(
  name: string,
  sql: string,
  target?: string,
): Promise<GuardCheckResponse> {
  const { data, error, response } = await api.POST('/api/guards/{name}/check', {
    params: { path: { name } },
    body: { sql, target: target ?? null },
  });
  throwIfNotOk(response, error, 'Failed to check guard');
  if (!data) throw new Error('Missing response body');
  return data;
}

// ---------------------------------------------------------------------------
// Query hooks
// ---------------------------------------------------------------------------

export function useGuardsList() {
  return useQuery({
    queryKey: GUARDS_KEY,
    queryFn: fetchGuards,
    staleTime: 30_000,
  });
}

export function useGuardDetail(name: string | null) {
  return useQuery({
    queryKey: name ? guardDetailKey(name) : ['guards', '__none__'],
    queryFn: () => fetchGuard(name!),
    enabled: !!name,
  });
}

// ---------------------------------------------------------------------------
// Mutation hooks
// ---------------------------------------------------------------------------

export function useCreateGuard() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (detail: GuardDetail) => createGuard(detail),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: GUARDS_KEY });
    },
  });
}

export function useUpdateGuard() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ name, detail }: { name: string; detail: GuardDetail }) =>
      updateGuard(name, detail),
    onSuccess: (_data, { name }) => {
      queryClient.invalidateQueries({ queryKey: GUARDS_KEY });
      queryClient.invalidateQueries({ queryKey: guardDetailKey(name) });
    },
  });
}

export function useDeleteGuard() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => deleteGuard(name),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: GUARDS_KEY });
    },
  });
}
