import { useSyncExternalStore } from 'react'
import type { CacheRunResult, CacheTestRunRequest } from '../types/cache'
import { api } from './client'
import { normalizeSseError } from './errorContract'
import { consumeSseResponse } from './sseReader'

export type BackgroundRunKind = 'bootstrap' | 'schema_annotation' | 'cache_test'
export type BackgroundRunStatus =
  | 'running'
  | 'reconnecting'
  | 'needs_key'
  | 'done'
  | 'partial'
  | 'failed'
  | 'cancelled'

export interface BackgroundRunState {
  runId: string
  kind: BackgroundRunKind
  target: string
  stage: string
  status: BackgroundRunStatus
  message: string
  lastSeq: number
  current: number | null
  total: number | null
  hasWarnings: boolean
  queryHash?: string
  queryLabel?: string
  result?: CacheRunResult
  hidden?: boolean
}

type StoredRun = BackgroundRunState

const STORAGE_KEY = 'rdst_background_runs'
const LEGACY_STORAGE_KEY = 'rdst_bootstrap_run'
const TERMINAL: BackgroundRunStatus[] = [
  'done',
  'partial',
  'failed',
  'cancelled',
]

const BOOTSTRAP_STAGE_LABELS: Record<string, string> = {
  connection_test: 'Testing connection',
  structure: 'Fetching structure',
  profile: 'Profiling columns',
  annotate: 'Writing AI descriptions',
  deploy: 'Deploying Readyset',
}

const runs = new Map<string, BackgroundRunState>()
const EMPTY_RUNS: BackgroundRunState[] = []
let snapshot: BackgroundRunState[] = []
let reconnectBaseMs = 1000
let reconnectMaxMs = 30_000
let reattachStarted = false
let streamGeneration = 0
const listeners = new Set<() => void>()
const streaming = new Map<string, symbol>()
const streamControllers = new Map<string, AbortController>()
const probing = new Set<string>()
const reconnectStatuses = new Map<string, BackgroundRunStatus>()

function isTerminal(status: BackgroundRunStatus): boolean {
  return TERMINAL.includes(status)
}

function publish(): void {
  snapshot = [...runs.values()]
  persist()
  for (const listener of listeners) listener()
}

function persist(): void {
  try {
    if (snapshot.length > 0) {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(snapshot))
    } else {
      localStorage.removeItem(STORAGE_KEY)
    }
    localStorage.removeItem(LEGACY_STORAGE_KEY)
  } catch {
    // Private mode or storage unavailable; reload reattachment degrades.
  }
}

function updateRun(runId: string, partial: Partial<BackgroundRunState>): void {
  const current = runs.get(runId)
  if (!current) return
  runs.set(runId, { ...current, ...partial })
  publish()
}

function removeRun(runId: string): void {
  if (!runs.delete(runId)) return
  stopStream(runId)
  reconnectStatuses.delete(runId)
  publish()
}

function markReconnecting(runId: string): void {
  const run = runs.get(runId)
  if (!run || isTerminal(run.status) || run.status === 'needs_key') return
  if (run.status === 'reconnecting') return
  reconnectStatuses.set(runId, run.status)
  updateRun(runId, { status: 'reconnecting' })
}

function restoreConnectedState(runId: string): void {
  const run = runs.get(runId)
  const previousStatus = reconnectStatuses.get(runId)
  reconnectStatuses.delete(runId)
  if (!run || run.status !== 'reconnecting') return
  updateRun(runId, { status: previousStatus ?? 'running' })
}

function attachRun(
  runId: string,
  kind: BackgroundRunKind,
  target: string,
  message: string,
  metadata: { queryHash?: string; queryLabel?: string } = {}
): BackgroundRunState {
  const existing = runs.get(runId)
  if (existing) return existing
  const run: BackgroundRunState = {
    runId,
    kind,
    target,
    stage:
      kind === 'bootstrap'
        ? 'connection_test'
        : kind === 'cache_test'
          ? 'connecting'
          : 'annotate',
    status: 'running',
    message,
    lastSeq: 0,
    current: null,
    total: null,
    hasWarnings: false,
    ...metadata,
  }
  runs.set(runId, run)
  publish()
  void streamRun(runId)
  return run
}

function kickoffFailed(
  kind: BackgroundRunKind,
  target: string,
  message: string,
  metadata: { queryHash?: string; queryLabel?: string } = {}
): void {
  const runId = `${kind}_${target}_start_failed_${Date.now()}`
  runs.set(runId, {
    runId,
    kind,
    target,
    stage: '',
    status: 'failed',
    message,
    lastSeq: 0,
    current: null,
    total: null,
    hasWarnings: false,
    ...metadata,
  })
  publish()
}

/** Start automatic database setup. Failures surface in the sidebar. */
export function startBootstrapRun(
  target: string,
  options: { deploy?: boolean; deployMode?: string } = {}
): void {
  void (async () => {
    try {
      const { data, error } = await api.POST('/api/bootstrap', {
        body: {
          target,
          deploy: options.deploy ?? true,
          deploy_mode: options.deployMode ?? 'docker',
        },
      })
      if (error || !data) throw new Error('bootstrap start rejected')
      attachRun(data.run_id, 'bootstrap', target, 'Starting...')
    } catch {
      kickoffFailed('bootstrap', target, 'Setup could not start')
    }
  })()
}

/** Start or attach to manual AI schema annotation. */
export async function startSchemaAnnotationRun(
  target: string,
  tableName?: string
): Promise<string | null> {
  try {
    const { data, error } = await api.POST(
      '/api/semantic-layer/annotation-runs',
      {
        body: { target, table_name: tableName },
      }
    )
    if (error || !data) {
      throw new Error(normalizeSseError(error).message)
    }
    attachRun(
      data.run_id,
      'schema_annotation',
      target,
      'Starting annotation...'
    )
    return data.run_id
  } catch (error) {
    kickoffFailed(
      'schema_annotation',
      target,
      error instanceof Error ? error.message : 'Annotation could not start'
    )
    return null
  }
}

/** Start an origin-vs-cache comparison that survives route changes. */
export async function startCacheTestRun(
  request: CacheTestRunRequest
): Promise<string | null> {
  const metadata = {
    queryHash: request.query_hash ?? undefined,
    queryLabel: request.label ?? undefined,
  }
  try {
    const { data, error } = await api.POST('/api/cache/test-runs', {
      body: request,
    })
    if (error || !data) {
      throw new Error(normalizeSseError(error).message)
    }
    attachRun(
      data.run_id,
      'cache_test',
      request.target ?? '',
      'Connecting...',
      metadata
    )
    return data.run_id
  } catch (error) {
    kickoffFailed(
      'cache_test',
      request.target ?? '',
      error instanceof Error ? error.message : 'Cache test could not start',
      metadata
    )
    return null
  }
}

/** Restore every in-flight run saved by this browser. */
export function reattachBackgroundRuns(): void {
  if (reattachStarted) return
  reattachStarted = true

  for (const stored of readStoredRuns()) {
    if (!stored.runId || runs.has(stored.runId)) continue
    runs.set(stored.runId, stored)
    publish()
    if (!isTerminal(stored.status)) void probeAndStream(stored.runId)
  }
}

async function probeAndStream(runId: string): Promise<void> {
  if (probing.has(runId)) return
  const run = runs.get(runId)
  if (!run || isTerminal(run.status)) return
  probing.add(runId)
  try {
    const { data, response } = await api.GET('/api/runs/{run_id}', {
      params: { path: { run_id: runId } },
    })
    if (!data) {
      if (response.status === 404) {
        removeRun(runId)
        return
      }
      throw new Error(`Run status failed with HTTP ${response.status}`)
    }
    const current = runs.get(runId)
    if (!current || isTerminal(current.status)) return
    const backendStatus = data.status as BackgroundRunStatus
    const replayPending = data.last_seq > current.lastSeq
    reconnectStatuses.delete(runId)
    updateRun(runId, {
      kind: data.kind as BackgroundRunKind,
      target: data.target,
      queryHash:
        typeof data.metadata?.query_hash === 'string'
          ? data.metadata.query_hash
          : current.queryHash,
      queryLabel:
        typeof data.metadata?.label === 'string'
          ? data.metadata.label
          : current.queryLabel,
      // Keep the stream attachable until missed terminal frames (including the
      // useful failure message) have replayed.
      status:
        replayPending && isTerminal(backendStatus)
          ? current.status
          : backendStatus,
    })
    if (replayPending || !isTerminal(backendStatus)) {
      void streamRun(runId)
    } else {
      stopStream(runId)
    }
  } catch {
    markReconnecting(runId)
    void streamRun(runId)
  } finally {
    probing.delete(runId)
  }
}

function readStoredRuns(): StoredRun[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) {
      const parsed = JSON.parse(raw)
      if (!Array.isArray(parsed)) return []
      return parsed.map((value: unknown) => {
        const stored = value as StoredRun
        return (value as { status?: string }).status === 'interrupted'
          ? {
              ...stored,
              status: 'reconnecting',
              message: 'Running...',
            }
          : stored
      })
    }
    const legacyRaw = localStorage.getItem(LEGACY_STORAGE_KEY)
    if (!legacyRaw) return []
    const legacy = JSON.parse(legacyRaw) as {
      runId?: string
      target?: string
      lastSeq?: number
    }
    if (!legacy.runId) return []
    return [
      {
        runId: legacy.runId,
        kind: 'bootstrap',
        target: legacy.target ?? '',
        stage: '',
        status: 'running',
        message: 'Reconnecting...',
        lastSeq: legacy.lastSeq ?? 0,
        current: null,
        total: null,
        hasWarnings: false,
      },
    ]
  } catch {
    return []
  }
}

function stopStream(runId: string): void {
  streaming.delete(runId)
  streamControllers.get(runId)?.abort()
  streamControllers.delete(runId)
}

async function streamRun(runId: string): Promise<void> {
  if (streaming.has(runId)) return
  const token = Symbol(runId)
  streaming.set(runId, token)
  const generation = streamGeneration
  let attempts = 0
  try {
    while (generation === streamGeneration && streaming.get(runId) === token) {
      const run = runs.get(runId)
      if (!run || isTerminal(run.status)) return
      try {
        const controller = new AbortController()
        streamControllers.set(runId, controller)
        const response = await fetch(
          `/api/runs/${runId}/events?after_seq=${run.lastSeq}`,
          { signal: controller.signal }
        )
        if (streaming.get(runId) !== token) return
        if (response.status === 404) {
          removeRun(runId)
          return
        }
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        attempts = 0
        restoreConnectedState(runId)
        await consumeSseResponse(response, (event, data) => {
          applyFrame(runId, event, data)
        })
        const latest = runs.get(runId)
        if (!latest || isTerminal(latest.status)) return
        throw new Error('stream ended early')
      } catch {
        attempts += 1
        markReconnecting(runId)
        await new Promise((resolve) =>
          setTimeout(
            resolve,
            Math.min(reconnectBaseMs * attempts, reconnectMaxMs)
          )
        )
      }
    }
  } finally {
    if (streaming.get(runId) === token) {
      streaming.delete(runId)
      streamControllers.delete(runId)
    }
  }
}

function applyFrame(runId: string, event: string, data: unknown): void {
  let run = runs.get(runId)
  if (!run) return
  if (run.status === 'reconnecting') {
    restoreConnectedState(runId)
    run = runs.get(runId)
    if (!run) return
  }
  const payload = data as Record<string, unknown>
  const seq = typeof payload.seq === 'number' ? payload.seq : run.lastSeq
  const base = { lastSeq: Math.max(run.lastSeq, seq) }

  switch (event) {
    case 'progress':
      updateRun(runId, {
        ...base,
        stage: String(payload.stage ?? run.stage),
        message: String(payload.message ?? run.message),
        current:
          typeof payload.percent === 'number' ? payload.percent : run.current,
        total: 100,
      })
      break
    case 'bootstrap_stage': {
      const stage = String(payload.stage ?? '')
      const stageStatus = String(payload.status ?? '')
      const annotationWarning = stage === 'annotate' && stageStatus === 'failed'
      updateRun(runId, {
        ...base,
        stage,
        hasWarnings: run.hasWarnings || annotationWarning,
        status: run.status === 'needs_key' ? 'running' : run.status,
        message:
          run.hasWarnings && !annotationWarning
            ? run.message
            : String(payload.message ?? '') ||
              BOOTSTRAP_STAGE_LABELS[stage] ||
              run.message,
      })
      break
    }
    case 'needs_key':
      updateRun(runId, {
        ...base,
        status: 'needs_key',
        message: String(payload.message ?? 'Add an AI key to finish setup'),
      })
      break
    case 'annotate_started':
      updateRun(runId, {
        ...base,
        stage: 'annotate',
        message: String(payload.message ?? 'Starting annotation...'),
        current: Number(payload.completed_tables ?? 0),
        total: Number(payload.tables ?? 0),
      })
      break
    case 'annotate_progress':
      updateRun(runId, {
        ...base,
        message: String(payload.message ?? 'Annotating schema...'),
        current: Number(payload.table_index ?? run.current ?? 0),
        total: Number(payload.total_tables ?? run.total ?? 0),
      })
      break
    case 'annotate_table_complete':
      updateRun(runId, {
        ...base,
        message: payload.table
          ? `Annotated ${String(payload.table)}`
          : run.message,
        current: Number(payload.table_index ?? run.current ?? 0),
        total: Number(payload.total_tables ?? run.total ?? 0),
      })
      break
    case 'annotate_complete': {
      const tablesFailed = Number(payload.tables_failed ?? 0)
      updateRun(runId, {
        ...base,
        hasWarnings:
          run.hasWarnings || tablesFailed > 0 || payload.success === false,
        message: String(payload.message ?? 'Annotation complete'),
      })
      break
    }
    case 'cache_run_complete':
      updateRun(runId, {
        ...base,
        result: payload as unknown as CacheRunResult,
        message: 'Performance test complete',
        current: 100,
        total: 100,
      })
      break
    case 'error':
    case 'annotate_error':
      updateRun(runId, {
        ...base,
        message: normalizeSseError(data).message,
      })
      break
    case 'run_end': {
      const status = String(payload.status ?? 'done') as BackgroundRunStatus
      updateRun(runId, {
        ...base,
        status:
          run.hasWarnings && status === 'done'
            ? 'partial'
            : isTerminal(status)
              ? status
              : 'done',
      })
      break
    }
    default:
      updateRun(runId, base)
  }
}

export async function cancelBackgroundRun(runId: string): Promise<void> {
  try {
    const { data, error } = await api.DELETE('/api/runs/{run_id}', {
      params: { path: { run_id: runId } },
    })
    if (error || !data?.cancelled) {
      updateRun(runId, { message: normalizeSseError(error).message })
    }
  } catch {
    updateRun(runId, { message: 'Could not cancel this run' })
  }
}

export function dismissBackgroundRun(runId: string): void {
  removeRun(runId)
}

/** Hide an acknowledged job notification while retaining any linked result. */
export function acknowledgeBackgroundRun(runId: string): void {
  updateRun(runId, { hidden: true })
}

export function getBackgroundRuns(): BackgroundRunState[] {
  return snapshot
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function useBackgroundRuns(): BackgroundRunState[] {
  return useSyncExternalStore(subscribe, getBackgroundRuns, () => EMPTY_RUNS)
}

export function useBackgroundRun(
  kind: BackgroundRunKind,
  target: string | null
): BackgroundRunState | undefined {
  const allRuns = useBackgroundRuns()
  if (!target) return undefined
  return [...allRuns]
    .reverse()
    .find((run) => run.kind === kind && run.target === target)
}

/**
 * Forget every run: abort live streams, drop in-memory state, and clear the
 * persisted records. Used when local data is reset, so a wiped install does
 * not resurface chips for jobs whose data no longer exists.
 */
export function clearAllBackgroundRuns(): void {
  streamGeneration += 1
  for (const controller of streamControllers.values()) controller.abort()
  runs.clear()
  snapshot = []
  streaming.clear()
  streamControllers.clear()
  probing.clear()
  reconnectStatuses.clear()
  try {
    localStorage.removeItem(STORAGE_KEY)
    localStorage.removeItem(LEGACY_STORAGE_KEY)
  } catch {
    // ignore
  }
  for (const listener of listeners) listener()
}

export function __resetBackgroundRunsForTests(
  options: { reconnectBaseMs?: number; reconnectMaxMs?: number } = {}
): void {
  clearAllBackgroundRuns()
  reattachStarted = false
  reconnectBaseMs = options.reconnectBaseMs ?? 1000
  reconnectMaxMs = options.reconnectMaxMs ?? 30_000
}
