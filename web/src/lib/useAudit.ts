import { useCallback, useSyncExternalStore } from 'react'
import { useTargetSwitchLock } from './targetSwitchLock'
import { api } from './client'
import {
  beginAuditSession,
  completeAuditSession,
  finishAuditSession,
  getActiveAuditSession,
  updateAuditSession,
} from './auditSession'
import {
  cancelBackgroundRun,
  getBackgroundRuns,
  isHealthCheckKind,
  startAuditCaptureRun,
  startAuditRun,
} from './backgroundRuns'
import { consumeSseResponse } from './sseReader'
import { throwIfNotOk } from './httpError'
import { resumeFleetAuditSession } from './useFleet'
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
} from '../types/audit'

// ---------------------------------------------------------------------------
// API functions
// ---------------------------------------------------------------------------

export async function fetchAuditRuns(
  target?: string
): Promise<AuditRunListResponse> {
  const { data, response } = await api.GET('/api/audit/runs', {
    params: { query: target ? { target } : {} },
  })
  await throwIfNotOk(response, 'Failed to fetch audit runs')
  if (!data) throw new Error('Missing response body')
  return data
}

// A saved run's detail payload is either an AuditReport (metrics audit) or a
// WorkloadRun (duration capture). Capture payloads carry a `queries` array and
// a `run_id`. Newer capture payloads may also carry merged audit fields.
export function isWorkloadRun(payload: unknown): payload is WorkloadRun {
  if (!payload || typeof payload !== 'object') return false
  const obj = payload as Record<string, unknown>
  return 'queries' in obj && 'run_id' in obj
}

export async function fetchRunDetail(
  runId: string
): Promise<AuditReport | WorkloadRun> {
  const { data, response } = await api.GET('/api/audit/runs/{run_id}', {
    params: { path: { run_id: runId } },
  })
  await throwIfNotOk(response, 'Failed to fetch audit run')
  if (!data) throw new Error('Missing response body')
  return data as AuditReport | WorkloadRun
}

// Capture-prerequisite check (GET /api/audit/requirements). Reports whether
// the engine's statement statistics (pg_stat_statements / performance_schema)
// are available, with setup instructions when they are not. Connection
// failures surface as query_stats "error". Untyped by the generated client
// until gen:api runs.
export interface AuditRequirements {
  target: string
  engine: string
  query_stats: 'ok' | 'missing' | 'error' | string
  detail: string
  remediation?: string | null
  docker_available: boolean
}

export class AuditRequirementsError extends Error {
  code: string
  target?: string
  passwordEnv?: string

  constructor({
    code,
    message,
    target,
    passwordEnv,
  }: {
    code: string
    message: string
    target?: string
    passwordEnv?: string
  }) {
    super(message)
    this.name = 'AuditRequirementsError'
    this.code = code
    this.target = target
    this.passwordEnv = passwordEnv
  }
}

export async function fetchAuditRequirements(
  target: string
): Promise<AuditRequirements> {
  const response = await fetch(
    `/api/audit/requirements?target=${encodeURIComponent(target)}`
  )
  if (!response.ok) {
    const raw = await response.text().catch(() => '')
    let body: unknown
    try {
      body = JSON.parse(raw)
    } catch {
      body = undefined
    }
    const outer =
      body && typeof body === 'object'
        ? (body as Record<string, unknown>)
        : undefined
    const detail =
      outer?.detail && typeof outer.detail === 'object'
        ? (outer.detail as Record<string, unknown>)
        : outer
    const code =
      typeof detail?.code === 'string' ? detail.code : `HTTP_${response.status}`
    const lockedTarget =
      typeof detail?.target === 'string' ? detail.target : target
    const passwordEnv =
      typeof detail?.password_env === 'string'
        ? detail.password_env
        : undefined
    const message =
      code === 'TARGET_PASSWORD_REQUIRED'
        ? `Enter the password for '${lockedTarget}' again.`
        : typeof detail?.message === 'string'
          ? detail.message
          : raw || `Failed to check capture requirements (${response.status})`
    throw new AuditRequirementsError({
      code,
      message,
      target: lockedTarget,
      passwordEnv,
    })
  }
  return (await response.json()) as AuditRequirements
}

// ---------------------------------------------------------------------------
// Background-run subscription
//
// Audits run detached in the RDST server's run registry, so the work survives
// a reload or a closed tab. These hooks hold their own subscription to the
// run's replayable event stream, independent of the thin one the Jobs chip
// keeps. The registry promotes each event's `type` to the SSE `event:` name,
// so the frame name is the discriminator that has to be put back.
// ---------------------------------------------------------------------------

async function followRun(
  runId: string,
  controller: AbortController,
  onFrame: (event: string, payload: Record<string, unknown>) => void
): Promise<void> {
  const response = await fetch(`/api/runs/${runId}/events?after_seq=0`, {
    signal: controller.signal,
  })
  if (!response.ok) {
    throw new Error(`HTTP error ${response.status}: ${await response.text()}`)
  }
  await consumeSseResponse(response, (event, data) =>
    onFrame(event, (data ?? {}) as Record<string, unknown>)
  )
}

// ---------------------------------------------------------------------------
// Audit run hook
// ---------------------------------------------------------------------------

interface UseAuditRunReturn {
  run: (target: string, options?: { insights?: boolean }) => Promise<void>
  state: AuditRunState
  statusMessage: string | undefined
  phase: string | undefined
  report: AuditReport | undefined
  snapshotId: string | undefined
  error: string | undefined
  reset: () => void
  cancel: () => void
}

function createExternalStore<T>(initial: T) {
  let snapshot = initial
  const listeners = new Set<() => void>()
  return {
    getSnapshot: () => snapshot,
    subscribe: (listener: () => void) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    set(update: Partial<T> | ((current: T) => Partial<T>)) {
      const patch = typeof update === 'function' ? update(snapshot) : update
      snapshot = { ...snapshot, ...patch }
      listeners.forEach((listener) => listener())
    },
  }
}

interface AuditRunSnapshot {
  state: AuditRunState
  statusMessage: string | undefined
  phase: string | undefined
  report: AuditReport | undefined
  snapshotId: string | undefined
  error: string | undefined
}

const EMPTY_AUDIT_RUN: AuditRunSnapshot = {
  state: 'idle',
  statusMessage: undefined,
  phase: undefined,
  report: undefined,
  snapshotId: undefined,
  error: undefined,
}
const auditRunStore = createExternalStore<AuditRunSnapshot>(EMPTY_AUDIT_RUN)
let auditRunController: AbortController | null = null
let auditRunSessionId: number | null = null
let auditRunId: string | null = null

function cancelAuditRun() {
  auditRunController?.abort()
  auditRunController = null
  if (auditRunId !== null) void cancelBackgroundRun(auditRunId)
  auditRunId = null
  auditRunStore.set({
    state: 'idle',
    statusMessage: undefined,
    phase: undefined,
  })
  if (auditRunSessionId !== null) finishAuditSession(auditRunSessionId)
  auditRunSessionId = null
}

async function followAuditRun(runId: string, sessionId: number): Promise<void> {
  const controller = new AbortController()
  auditRunController = controller
  auditRunId = runId
  updateAuditSession(sessionId, { runId })
  let terminal = false

  try {
    await followRun(runId, controller, (name, payload) => {
      if (name === 'run_end') {
        // The registry's terminal record. A run that ended without its own
        // completion event must not leave the target-switch lock engaged.
        if (terminal) return
        terminal = true
        if (payload.status === 'cancelled') {
          auditRunStore.set({
            state: 'idle',
            statusMessage: undefined,
            phase: undefined,
          })
        } else {
          auditRunStore.set((current) => ({
            error: current.error || 'Audit ended before completing',
            state: 'error',
          }))
        }
        finishAuditSession(sessionId)
        return
      }

      const event = { ...payload, type: name } as unknown as AuditEvent
      switch (event.type) {
        case 'status':
          auditRunStore.set({
            statusMessage: event.message,
            phase: event.phase,
          })
          updateAuditSession(sessionId, {
            statusMessage: event.message,
            phase: event.phase,
          })
          break
        case 'target_start':
        case 'metrics_collected':
        case 'llm_insights':
        case 'diff':
          break
        case 'target_error':
          auditRunStore.set({ error: event.error })
          break
        case 'snapshot_saved':
          auditRunStore.set({ snapshotId: event.snapshot_id })
          break
        case 'target_complete':
          auditRunStore.set({ report: event.result as AuditReport })
          break
        case 'complete':
          terminal = true
          if (event.success) {
            auditRunStore.set({ state: 'complete', phase: 'storage' })
          } else {
            auditRunStore.set((current) => ({
              error: current.error || 'Audit failed',
              state: 'error',
            }))
          }
          if (event.success && event.snapshot_id) {
            completeAuditSession(sessionId, event.snapshot_id)
          } else {
            finishAuditSession(sessionId)
          }
          break
        case 'error':
          terminal = true
          auditRunStore.set({ error: event.message, state: 'error' })
          finishAuditSession(sessionId)
          break
        default: {
          // Exhaustiveness guard: adding a variant to AuditEvent
          // without handling it here fails tsc.
          const _exhaustive: never = event
          void _exhaustive
          break
        }
      }
    })
  } catch (err: unknown) {
    if (err instanceof Error && err.name === 'AbortError') return
    auditRunStore.set({
      error: err instanceof Error ? err.message : 'An error occurred',
      state: 'error',
    })
    finishAuditSession(sessionId)
  } finally {
    if (auditRunController === controller) auditRunController = null
    if (auditRunSessionId === sessionId) auditRunSessionId = null
    if (auditRunId === runId) auditRunId = null
  }
}

export function useAuditRun(): UseAuditRunReturn {
  const snapshot = useSyncExternalStore(
    auditRunStore.subscribe,
    auditRunStore.getSnapshot,
    auditRunStore.getSnapshot
  )
  useTargetSwitchLock('audit', snapshot.state === 'running')

  const reset = useCallback(() => {
    cancelAuditRun()
    auditRunStore.set(EMPTY_AUDIT_RUN)
  }, [])

  const cancel = useCallback(cancelAuditRun, [])

  const run = useCallback(
    async (target: string, options?: { insights?: boolean }) => {
      const sessionId = beginAuditSession({
        kind: 'snapshot',
        targetLabel: target,
        targetNames: [target],
        durationSeconds: 0,
        startedAt: Date.now(),
        phase: 'config',
        statusMessage: 'Starting health check...',
        cancel: cancelAuditRun,
      })
      if (sessionId === null) return
      auditRunSessionId = sessionId

      auditRunStore.set({
        state: 'running',
        statusMessage: 'Starting health check...',
        phase: 'config',
        report: undefined,
        snapshotId: undefined,
        error: undefined,
      })

      // A `reused` response hands back the audit already in flight, which is
      // the same thing this hook wants to follow.
      const started = await startAuditRun(target, {
        insights: options?.insights ?? true,
      })
      if (!started) {
        auditRunStore.set({
          error: 'Health check could not start',
          state: 'error',
        })
        finishAuditSession(sessionId)
        auditRunSessionId = null
        return
      }
      await followAuditRun(started.runId, sessionId)
    },
    []
  )

  return { run, ...snapshot, reset, cancel }
}

// ---------------------------------------------------------------------------
// Workload capture hook (long-lived SSE streaming)
// ---------------------------------------------------------------------------

export interface CaptureProgress {
  elapsedSeconds: number
  totalSeconds: number | undefined
  uniqueQueries: number
  totalExecutions: number
  cacheHitRatio: number | null | undefined
  activeConnections: number
  tps: number
}

export interface CaptureResult {
  runId: string
  summary: WorkloadSummary | undefined
  analysis: WorkloadAnalysis | null | undefined
}

interface UseAuditCaptureReturn {
  run: (
    target: string,
    options?: { duration?: number; analysis?: boolean }
  ) => Promise<void>
  cancel: () => void
  reset: () => void
  state: CaptureState
  statusMessage: string | undefined
  phase: string | undefined
  analysisWarning: string | undefined
  readysetNotice: string | undefined
  progress: CaptureProgress | undefined
  result: CaptureResult | undefined
  error: string | undefined
}

const EMPTY_PROGRESS: CaptureProgress = {
  elapsedSeconds: 0,
  totalSeconds: undefined,
  uniqueQueries: 0,
  totalExecutions: 0,
  cacheHitRatio: undefined,
  activeConnections: 0,
  tps: 0,
}

interface AuditCaptureSnapshot {
  state: CaptureState
  statusMessage: string | undefined
  phase: string | undefined
  analysisWarning: string | undefined
  readysetNotice: string | undefined
  progress: CaptureProgress | undefined
  result: CaptureResult | undefined
  error: string | undefined
}

const EMPTY_AUDIT_CAPTURE: AuditCaptureSnapshot = {
  state: 'idle',
  statusMessage: undefined,
  phase: undefined,
  analysisWarning: undefined,
  readysetNotice: undefined,
  progress: undefined,
  result: undefined,
  error: undefined,
}
const auditCaptureStore =
  createExternalStore<AuditCaptureSnapshot>(EMPTY_AUDIT_CAPTURE)
let auditCaptureController: AbortController | null = null
let auditCaptureSessionId: number | null = null
let auditCaptureRunId: string | null = null

function cancelAuditCapture() {
  // Cancelling the background run is what releases the database connection
  // the capture holds for its whole window.
  auditCaptureController?.abort()
  auditCaptureController = null
  if (auditCaptureRunId !== null) void cancelBackgroundRun(auditCaptureRunId)
  auditCaptureRunId = null
  auditCaptureStore.set({
    state: 'idle',
    statusMessage: undefined,
    phase: undefined,
    progress: undefined,
  })
  if (auditCaptureSessionId !== null) finishAuditSession(auditCaptureSessionId)
  auditCaptureSessionId = null
}

async function followAuditCapture(
  runId: string,
  sessionId: number
): Promise<void> {
  const controller = new AbortController()
  auditCaptureController = controller
  auditCaptureRunId = runId
  updateAuditSession(sessionId, { runId })
  let terminal = false

  try {
    await followRun(runId, controller, (name, payload) => {
      if (name === 'run_end') {
        // The registry's terminal record. A run that ended without its own
        // completion event must not leave the target-switch lock engaged.
        if (terminal) return
        terminal = true
        if (payload.status === 'cancelled') {
          auditCaptureStore.set({
            state: 'idle',
            statusMessage: undefined,
            phase: undefined,
            progress: undefined,
          })
        } else {
          auditCaptureStore.set((current) => ({
            error: current.error || 'Capture ended before completing',
            state: 'error',
          }))
        }
        finishAuditSession(sessionId)
        return
      }

      const event = { ...payload, type: name } as unknown as WorkloadEvent
      switch (event.type) {
        case 'status':
          auditCaptureStore.set({
            statusMessage: event.message,
            phase: event.phase,
          })
          updateAuditSession(sessionId, {
            statusMessage: event.message,
            phase: event.phase,
          })
          // Graceful degradation: a failed analysis arrives as a status
          // with phase=analysis. Surface it as a muted warning.
          if (
            event.phase === 'analysis' &&
            /^Analysis failed/i.test(event.message)
          ) {
            auditCaptureStore.set({ analysisWarning: event.message })
          }
          if (
            event.phase === 'readyset' &&
            /docker.*not available|comparison skipped|benchmark skipped/i.test(
              event.message
            )
          ) {
            auditCaptureStore.set({
              readysetNotice:
                'ReadySet comparison skipped - Docker is not available',
            })
          }
          break
        case 'connected':
          auditCaptureStore.set({ phase: 'snapshot_start' })
          updateAuditSession(sessionId, { phase: 'snapshot_start' })
          break
        case 'snapshot':
          break
        case 'capture_progress':
          auditCaptureStore.set((current) => ({
            progress: {
              elapsedSeconds: event.elapsed_seconds,
              totalSeconds:
                event.total_seconds ?? current.progress?.totalSeconds,
              uniqueQueries: event.unique_queries ?? 0,
              totalExecutions: event.total_executions ?? 0,
              cacheHitRatio: event.cache_hit_ratio,
              activeConnections: event.active_connections ?? 0,
              tps: event.tps ?? 0,
            },
            phase: 'capture',
          }))
          updateAuditSession(sessionId, { phase: 'capture' })
          break
        case 'capture_complete':
          auditCaptureStore.set((current) => ({
            state: 'analyzing',
            phase: 'snapshot_end',
            progress: {
              ...(current.progress ?? EMPTY_PROGRESS),
              elapsedSeconds: event.duration_seconds,
              totalSeconds: event.duration_seconds,
              uniqueQueries: event.unique_queries,
              totalExecutions: event.total_executions,
            },
          }))
          updateAuditSession(sessionId, { phase: 'snapshot_end' })
          break
        case 'analysis_progress':
          auditCaptureStore.set({
            statusMessage: event.message,
            phase: 'analysis',
          })
          updateAuditSession(sessionId, {
            statusMessage: event.message,
            phase: 'analysis',
          })
          break
        case 'queries_saved':
          auditCaptureStore.set({ phase: 'storage' })
          updateAuditSession(sessionId, { phase: 'storage' })
          break
        case 'complete':
          terminal = true
          if (event.success) {
            auditCaptureStore.set({
              result: {
                runId: event.run_id,
                summary: (event.summary as WorkloadSummary | null) ?? undefined,
                analysis:
                  (event.analysis as WorkloadAnalysis | null) ?? undefined,
              },
              state: 'complete',
              phase: 'storage',
            })
          } else {
            auditCaptureStore.set((current) => ({
              error: current.error || 'Capture failed',
              state: 'error',
            }))
          }
          if (event.success && event.run_id) {
            completeAuditSession(sessionId, event.run_id)
          } else {
            finishAuditSession(sessionId)
          }
          break
        case 'error':
          terminal = true
          auditCaptureStore.set({ error: event.message, state: 'error' })
          finishAuditSession(sessionId)
          break
        default: {
          // Exhaustiveness guard: adding a variant to WorkloadEvent
          // without handling it here fails tsc.
          const _exhaustive: never = event
          void _exhaustive
          break
        }
      }
    })
  } catch (err: unknown) {
    if (err instanceof Error && err.name === 'AbortError') return
    auditCaptureStore.set({
      error: err instanceof Error ? err.message : 'An error occurred',
      state: 'error',
    })
    finishAuditSession(sessionId)
  } finally {
    if (auditCaptureController === controller) auditCaptureController = null
    if (auditCaptureSessionId === sessionId) auditCaptureSessionId = null
    if (auditCaptureRunId === runId) auditCaptureRunId = null
  }
}

export function useAuditCapture(): UseAuditCaptureReturn {
  const snapshot = useSyncExternalStore(
    auditCaptureStore.subscribe,
    auditCaptureStore.getSnapshot,
    auditCaptureStore.getSnapshot
  )
  useTargetSwitchLock(
    'audit',
    snapshot.state === 'capturing' || snapshot.state === 'analyzing'
  )

  const reset = useCallback(() => {
    cancelAuditCapture()
    auditCaptureStore.set(EMPTY_AUDIT_CAPTURE)
  }, [])

  const cancel = useCallback(cancelAuditCapture, [])

  const run = useCallback(
    async (
      target: string,
      options?: { duration?: number; analysis?: boolean }
    ) => {
      const duration = options?.duration ?? 60
      const sessionId = beginAuditSession({
        kind: 'capture',
        targetLabel: target,
        targetNames: [target],
        durationSeconds: duration,
        startedAt: Date.now(),
        phase: 'config',
        statusMessage: 'Starting capture...',
        cancel: cancelAuditCapture,
      })
      if (sessionId === null) return
      auditCaptureSessionId = sessionId

      auditCaptureStore.set({
        state: 'capturing',
        statusMessage: 'Starting capture...',
        phase: 'config',
        analysisWarning: undefined,
        readysetNotice: undefined,
        progress: { ...EMPTY_PROGRESS, totalSeconds: duration },
        result: undefined,
        error: undefined,
      })

      // A `reused` response hands back the capture already in flight, which
      // is the same thing this hook wants to follow.
      const started = await startAuditCaptureRun(target, {
        duration,
        analysis: options?.analysis ?? true,
      })
      if (!started) {
        auditCaptureStore.set({
          error: 'Capture could not start',
          state: 'error',
        })
        finishAuditSession(sessionId)
        auditCaptureSessionId = null
        return
      }
      await followAuditCapture(started.runId, sessionId)
    },
    []
  )

  return { run, cancel, reset, ...snapshot }
}

/**
 * Re-seed the health-check session from a background run this browser left in
 * flight, so a reload restores the banner, the sidebar spinner, and the
 * one-at-a-time block. Call once, right after background runs are reattached.
 */
export function resumeAuditSessions(): void {
  if (getActiveAuditSession()) return
  const run = getBackgroundRuns().find(
    (candidate) =>
      isHealthCheckKind(candidate.kind) &&
      (candidate.status === 'running' || candidate.status === 'reconnecting')
  )
  if (!run) return
  if (run.kind === 'fleet_audit') {
    resumeFleetAuditSession(run)
    return
  }

  const capture = run.kind === 'audit_capture'
  const sessionId = beginAuditSession({
    kind: capture ? 'capture' : 'snapshot',
    targetLabel: run.target,
    targetNames: [run.target],
    durationSeconds: 0,
    startedAt: Date.now(),
    phase: run.stage,
    statusMessage: run.message,
    runId: run.runId,
    cancel: capture ? cancelAuditCapture : cancelAuditRun,
  })
  if (sessionId === null) return

  if (capture) {
    auditCaptureSessionId = sessionId
    auditCaptureStore.set({
      state: 'capturing',
      statusMessage: run.message,
      phase: run.stage,
    })
    void followAuditCapture(run.runId, sessionId)
  } else {
    auditRunSessionId = sessionId
    auditRunStore.set({
      state: 'running',
      statusMessage: run.message,
      phase: run.stage,
    })
    void followAuditRun(run.runId, sessionId)
  }
}
