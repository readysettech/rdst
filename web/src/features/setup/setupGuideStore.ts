import { useSyncExternalStore } from 'react'

/**
 * The one persisted fact the setup guide keeps about itself. Everything else
 * it shows is derived from `/api/setup-progress`, so this is the only bit of
 * "user did this" state in the feature:
 *
 *  - `dismissed` — the block was hidden. Permanent across restarts, and
 *    recoverable only from the sidebar footer's setup guide entry. It never
 *    re-surfaces on its own.
 */
const DISMISSED_KEY = 'rdst-setup-guide-dismissed'

export interface SetupGuideState {
  dismissed: boolean
}

function readFlag(key: string): boolean {
  try {
    return localStorage.getItem(key) === '1'
  } catch {
    return false
  }
}

function writeFlag(key: string, value: boolean): void {
  try {
    if (value) localStorage.setItem(key, '1')
    else localStorage.removeItem(key)
  } catch {
    // A blocked storage means the preference does not survive a restart;
    // it must never break the chrome.
  }
}

function readState(): SetupGuideState {
  return { dismissed: readFlag(DISMISSED_KEY) }
}

let state = readState()
const listeners = new Set<() => void>()

function setState(next: SetupGuideState): void {
  state = next
  for (const listener of listeners) listener()
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function getSetupGuideState(): SetupGuideState {
  return state
}

export function useSetupGuideState(): SetupGuideState {
  return useSyncExternalStore(subscribe, getSetupGuideState, getSetupGuideState)
}

export function dismissSetupGuide(): void {
  writeFlag(DISMISSED_KEY, true)
  setState({ dismissed: true })
}

/** Recovery path from the sidebar: undo the dismissal. */
export function requestSetupGuide(): void {
  writeFlag(DISMISSED_KEY, false)
  setState({ dismissed: false })
}

/** Test seam: re-read the (cleared) storage. */
export function __resetSetupGuideStoreForTests(): void {
  setState(readState())
}
