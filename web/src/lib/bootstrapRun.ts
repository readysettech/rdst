import { useSyncExternalStore } from 'react'

import type { components } from './api.generated'
import { api } from './client'
import { normalizeSseError } from './errorContract'
import { consumeSseResponse } from './sseReader'

// The add-database bootstrap run: module-scope so the stream and its state
// survive route changes; components subscribe via useBootstrapRunState.
// Progress is resumable end to end: frames carry seq, reconnects replay
// via ?after_seq=, and in-flight {runId, lastSeq} persist so a reload
// reattaches. Terminal runs are dropped from storage — presence implies
// resumable.

export type BootstrapRunStatus =
  | 'idle'
  | 'running'
  | 'needs_key'
  | 'done'
  | 'failed'
  | 'cancelled'
  | 'interrupted'

export interface BootstrapRunState {
  runId: string | null
  target: string | null
  stage: string
  status: BootstrapRunStatus
  message: string
  lastSeq: number
}

type StageFrame = components['schemas']['BootstrapStageEvent'] & { seq?: number }
type NeedsKeyFrame = components['schemas']['BootstrapNeedsKeyEvent'] & {
  seq?: number
}

const STORAGE_KEY = 'rdst_bootstrap_run'
const MAX_RECONNECT_ATTEMPTS = 5
const TERMINAL: BootstrapRunStatus[] = ['done', 'failed', 'cancelled', 'interrupted']

const STAGE_LABELS: Record<string, string> = {
  connection_test: 'Testing connection',
  structure: 'Fetching structure',
  profile: 'Profiling columns',
  annotate: 'Writing AI descriptions',
  deploy: 'Deploying Readyset',
}

const idleState: BootstrapRunState = {
  runId: null,
  target: null,
  stage: '',
  status: 'idle',
  message: '',
  lastSeq: 0,
}

let state: BootstrapRunState = idleState
let reconnectBaseMs = 1000
const listeners = new Set<() => void>()

function emit() {
  for (const listener of listeners) listener()
}

function update(partial: Partial<BootstrapRunState>) {
  state = { ...state, ...partial }
  try {
    if (state.runId && !isTerminal(state.status)) {
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({
          runId: state.runId,
          target: state.target,
          lastSeq: state.lastSeq,
        })
      )
    } else {
      localStorage.removeItem(STORAGE_KEY)
    }
  } catch {
    // Private mode or storage unavailable; reattach-on-reload degrades.
  }
  emit()
}

function isTerminal(status: BootstrapRunStatus): boolean {
  return TERMINAL.includes(status)
}

/**
 * Kick off the bootstrap for a freshly added target. Never throws and never
 * blocks the caller's flow: a kickoff failure surfaces on the chip instead.
 */
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
      update({
        runId: data.run_id,
        target,
        stage: 'connection_test',
        status: 'running',
        message: 'Starting...',
        lastSeq: 0,
      })
      void streamRun(data.run_id)
    } catch {
      update({
        ...idleState,
        target,
        status: 'failed',
        message: 'Setup could not start',
      })
    }
  })()
}

/** Resume a stored in-flight run after a reload. No-op when idle. */
export function reattachBootstrapRun(): void {
  if (state.runId) return
  let stored: { runId?: string; target?: string; lastSeq?: number } | null = null
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    stored = raw ? JSON.parse(raw) : null
  } catch {
    stored = null
  }
  if (!stored?.runId) return
  const runId = stored.runId
  const target = stored.target ?? null
  const lastSeq = stored.lastSeq ?? 0
  void (async () => {
    // The stored lastSeq may already cover the frame that set the run's
    // current state (a parked needs_key run emits nothing new to replay),
    // so ask the server for the authoritative status before streaming.
    let status: BootstrapRunStatus = 'running'
    try {
      const { data } = await api.GET('/api/bootstrap/runs/{run_id}', {
        params: { path: { run_id: runId } },
      })
      if (!data) {
        // The server no longer knows this run; nothing to resume.
        update({ ...idleState })
        return
      }
      status = data.status as BootstrapRunStatus
    } catch {
      // Status probe unreachable; optimistically stream and let it settle.
    }
    update({
      runId,
      target,
      stage: '',
      status,
      message: 'Reconnecting...',
      lastSeq,
    })
    if (!isTerminal(status)) void streamRun(runId)
  })()
}

export function dismissBootstrapRun(): void {
  update(idleState)
}

async function streamRun(runId: string): Promise<void> {
  let attempts = 0
  while (state.runId === runId && !isTerminal(state.status)) {
    try {
      const response = await fetch(
        `/api/bootstrap/runs/${runId}/events?after_seq=${state.lastSeq}`
      )
      if (response.status === 404) {
        // The server no longer knows this run (restart + GC'd log).
        update({ status: 'interrupted', message: 'Run no longer available' })
        return
      }
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      attempts = 0
      await consumeSseResponse(response, (event, data) => {
        if (state.runId === runId) applyFrame(event, data)
      })
      if (state.runId !== runId || isTerminal(state.status)) return
      // Stream closed without run_end: server went away mid-run; retry.
      throw new Error('stream ended early')
    } catch {
      attempts += 1
      if (attempts > MAX_RECONNECT_ATTEMPTS) {
        update({ status: 'interrupted', message: 'Lost connection to the run' })
        return
      }
      await new Promise((resolve) =>
        setTimeout(resolve, reconnectBaseMs * attempts)
      )
    }
  }
}

function applyFrame(event: string, data: unknown): void {
  const seq = (data as { seq?: number }).seq ?? state.lastSeq
  const lastSeq = Math.max(state.lastSeq, seq)
  switch (event) {
    case 'bootstrap_stage': {
      const frame = data as StageFrame
      update({
        stage: frame.stage,
        message: frame.message || STAGE_LABELS[frame.stage] || state.message,
        lastSeq,
        // A stage event after the key gate means the run moved on.
        status: state.status === 'needs_key' ? 'running' : state.status,
      })
      break
    }
    case 'needs_key':
      update({
        status: 'needs_key',
        message: (data as NeedsKeyFrame).message,
        lastSeq,
      })
      break
    case 'error':
      update({ message: normalizeSseError(data).message, lastSeq })
      break
    case 'run_end': {
      const status = (data as { status?: string }).status as BootstrapRunStatus
      update({ status: isTerminal(status) ? status : 'done', lastSeq })
      break
    }
    default:
      update({ lastSeq })
  }
}

function getSnapshot(): BootstrapRunState {
  return state
}

/** Imperative read of the current run state (tests, non-React callers). */
export function getBootstrapRunState(): BootstrapRunState {
  return state
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function useBootstrapRunState(): BootstrapRunState {
  return useSyncExternalStore(subscribe, getSnapshot, () => idleState)
}

export function __resetBootstrapRunForTests(
  options: { reconnectBaseMs?: number } = {}
): void {
  state = idleState
  reconnectBaseMs = options.reconnectBaseMs ?? 1000
  try {
    localStorage.removeItem(STORAGE_KEY)
  } catch {
    // ignore
  }
  emit()
}
