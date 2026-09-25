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
  start: (hooks: { onPhase: (phase: StartupPhase, store?: string) => void }) => Promise<BackendHandle>
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
export async function superviseBackendStart(options: SuperviseOptions): Promise<BackendHandle> {
  const delays = options.retryDelaysMs ?? RETRY_DELAYS_MS
  const sleep = options.sleep ?? ((ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms)))
  for (let attempt = 1; ; attempt += 1) {
    if (options.cancelled?.()) throw new BackendStartCancelled()
    options.onState({ phase: 'starting', attempt })
    try {
      const handle = await options.start({
        onPhase: (phase, store) => {
          options.onState(phase === 'migrating' ? { phase, attempt, store } : { phase, attempt })
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
      options.onState({ phase: 'retrying', attempt, retryInMs, error: message(error) })
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
