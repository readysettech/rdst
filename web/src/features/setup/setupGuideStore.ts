import { useSyncExternalStore } from 'react'

/**
 * The two persisted facts the setup guide keeps about itself. Everything else
 * it shows is derived from `/api/setup-progress`, so these are the only bits
 * of "user did this" state in the feature:
 *
 *  - `dismissed` — the guide was sent away. Permanent across restarts, and
 *    recoverable only from the sidebar footer's setup guide entry. It never
 *    re-surfaces on its own.
 *  - `autoExpanded` — the one automatic expansion has already happened.
 */
const DISMISSED_KEY = 'rdst-setup-guide-dismissed'
const AUTO_EXPANDED_KEY = 'rdst-setup-guide-auto-expanded'

export interface SetupGuideState {
  dismissed: boolean
  autoExpanded: boolean
  /**
   * Bumped when the sidebar entry asks for the guide. The pill owns its own
   * open state, so a counter (rather than a boolean) lets a second request
   * re-open a guide the user closed in between.
   */
  openRequest: number
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

function readState(openRequest = 0): SetupGuideState {
  return {
    dismissed: readFlag(DISMISSED_KEY),
    autoExpanded: readFlag(AUTO_EXPANDED_KEY),
    openRequest,
  }
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
  setState({ ...state, dismissed: true })
}

export function markSetupGuideAutoExpanded(): void {
  writeFlag(AUTO_EXPANDED_KEY, true)
  setState({ ...state, autoExpanded: true })
}

/** Recovery path from the sidebar: undo the dismissal and open the panel. */
export function requestSetupGuide(): void {
  writeFlag(DISMISSED_KEY, false)
  setState({
    ...state,
    dismissed: false,
    openRequest: state.openRequest + 1,
  })
}

/** Test seam: re-read the (cleared) storage and drop the open request. */
export function __resetSetupGuideStoreForTests(): void {
  setState(readState())
}
