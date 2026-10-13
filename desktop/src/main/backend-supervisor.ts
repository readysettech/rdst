import type { BackendHandle, StartupPhase } from './backend.js'

// Waits before the second, third and fourth tries. A crash on start is usually something
// that a retry won't fix, so the attempts stop after these and the window says so.
export const RETRY_DELAYS_MS = [2_000, 5_000, 15_000]

export type BackendStartState =
  | { phase: 'starting'; attempt: number }
  | { phase: 'migrating'; attempt: number; store?: string }
  | { phase: 'retrying'; attempt: number; retryInMs: number; error: string }
  | { phase: 'ready'; attempt: number }
  | { phase: 'failed'; attempt: number; error: string }

export interface SuperviseOptions {
  start: (hooks: {
    onPhase: (phase: StartupPhase, store?: string) => void
  }) => Promise<BackendHandle>
  onState: (state: BackendStartState) => void
  retryDelaysMs?: number[]
  sleep?: (ms: number) => Promise<void>
  /** True once the app is quitting; no further attempt starts. */
  cancelled?: () => boolean
}

export class BackendStartCancelled extends Error {
  constructor() {
    super('RDST is quitting')
  }
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

/**
 * Start the backend, retrying with backoff when an attempt fails. A start that reports a
 * data migration is left to finish however long it takes (startBackend doesn't time it
 * out); only a process that exits, or stops making progress outside a migration, counts
 * as a failed attempt.
 */
export async function superviseBackendStart(
  options: SuperviseOptions
): Promise<BackendHandle> {
  const delays = options.retryDelaysMs ?? RETRY_DELAYS_MS
  const sleep =
    options.sleep ??
    ((ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms)))
  for (let attempt = 1; ; attempt += 1) {
    if (options.cancelled?.()) throw new BackendStartCancelled()
    options.onState({ phase: 'starting', attempt })
    try {
      const handle = await options.start({
        onPhase: (phase, store) => {
          options.onState(
            phase === 'migrating'
              ? { phase, attempt, store }
              : { phase, attempt }
          )
        },
      })
      options.onState({ phase: 'ready', attempt })
      return handle
    } catch (error) {
      if (options.cancelled?.()) throw new BackendStartCancelled()
      const retryInMs = delays[attempt - 1]
      if (retryInMs === undefined) {
        options.onState({ phase: 'failed', attempt, error: message(error) })
        throw error
      }
      options.onState({
        phase: 'retrying',
        attempt,
        retryInMs,
        error: message(error),
      })
      await sleep(retryInMs)
    }
  }
}

/** The tray tooltip for a start that isn't done yet. */
export function startupTooltip(state: BackendStartState): string {
  switch (state.phase) {
    case 'migrating':
      return 'RDST - upgrading your data, this can take a minute'
    case 'retrying':
      return 'RDST - restarting its background service'
    case 'failed':
      return "RDST - couldn't start its background service"
    default:
      return 'RDST - starting'
  }
}

// Waits before each restart of a backend that stopped after starting, then every
// RESTART_EVERY_MS for as long as it keeps failing.
export const RESTART_DELAYS_MS = [2_000, 5_000, 15_000]
export const RESTART_EVERY_MS = 30_000
// A backend that ran this long before stopping started cleanly; the backoff begins again.
export const STABLE_RUN_MS = 60_000

/** What the renderer and tray show about a backend that has started once. */
export type BackendRunState = 'running' | 'restarting' | 'failed'

/** The wait before restart number `attempt` (1-based). */
export function restartDelayMs(
  attempt: number,
  delays: readonly number[] = RESTART_DELAYS_MS,
  everyMs: number = RESTART_EVERY_MS
): number {
  return delays[attempt - 1] ?? everyMs
}

export interface KeepRunningOptions {
  /** One start attempt; rejects when the backend doesn't become ready. */
  start: () => Promise<BackendHandle>
  /** Stops a backend that finished starting after keeping it running ended. */
  stop: (handle: BackendHandle) => Promise<void>
  onExit: (exit: { code: number | null; signal: NodeJS.Signals | null }) => void
  onState: (state: BackendRunState) => void
  onRestarted: (handle: BackendHandle) => void
  /** True once the app is quitting; an exit then is expected. */
  quitting: () => boolean
  delaysMs?: readonly number[]
  everyMs?: number
  stableRunMs?: number
  sleep?: (ms: number) => Promise<void>
  now?: () => number
}

/**
 * Restart the backend whenever it exits outside a quit, with backoff. The state is
 * 'restarting' until the backoff steps run out, then 'failed' while it keeps trying.
 */
export function keepBackendRunning(
  initial: BackendHandle,
  options: KeepRunningOptions
): { stop: () => void } {
  const delays = options.delaysMs ?? RESTART_DELAYS_MS
  const sleep =
    options.sleep ??
    ((ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms)))
  const now = options.now ?? Date.now
  const stableRunMs = options.stableRunMs ?? STABLE_RUN_MS
  let stopped = false
  let attempts = 0
  const done = () => stopped || options.quitting()
  const stateAfter = (failedAttempts: number): BackendRunState =>
    failedAttempts >= delays.length ? 'failed' : 'restarting'

  async function restart(): Promise<void> {
    options.onState(stateAfter(attempts))
    for (;;) {
      attempts += 1
      await sleep(restartDelayMs(attempts, delays, options.everyMs))
      if (done()) return
      let handle: BackendHandle
      try {
        handle = await options.start()
      } catch {
        if (done()) return
        options.onState(stateAfter(attempts))
        continue
      }
      if (done()) {
        await options.stop(handle)
        return
      }
      watch(handle)
      options.onState('running')
      options.onRestarted(handle)
      return
    }
  }

  function watch(handle: BackendHandle): void {
    const startedAt = now()
    const exited = (code: number | null, signal: NodeJS.Signals | null) => {
      if (done()) return
      if (now() - startedAt >= stableRunMs) attempts = 0
      options.onExit({ code, signal })
      void restart()
    }
    const { exitCode, signalCode } = handle.process
    if (exitCode !== null || signalCode !== null)
      queueMicrotask(() => exited(exitCode, signalCode))
    else handle.process.once('exit', exited)
  }

  watch(initial)
  return {
    stop: () => {
      stopped = true
    },
  }
}

/** The tray tooltip while a backend that had started is down. */
export function runStateTooltip(state: BackendRunState): string | null {
  if (state === 'restarting') return 'RDST - restarting its background service'
  if (state === 'failed')
    return 'RDST - its background service keeps stopping, trying again'
  return null
}
