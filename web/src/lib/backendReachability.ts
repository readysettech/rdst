// Whether RDST's backend can be reached. The desktop app reports its background service's
// state; in a browser, an /api call that can't reach the backend says so. While it is down,
// React Query stays offline, so pages keep the data they had instead of refetching into
// empty or error states, and refetch once it is back.
import { onlineManager, replaceEqualDeep } from '@tanstack/react-query'
import { useSyncExternalStore } from 'react'
import { type DesktopBackendState, getDesktopBackend } from './desktop'

/** The desktop app's proxy marks its own answers with this when the backend is down. */
export const UNREACHABLE_HEADER = 'x-rdst-backend'
// A light request that tells whether the backend answers again.
const PROBE_URL = '/api/settings/preferences'
export const PROBE_INTERVAL_MS = 3_000
const GATEWAY_STATUSES = new Set([502, 503, 504])

let desktopState: DesktopBackendState = 'running'
let unreachable = false
let probe: ReturnType<typeof setInterval> | null = null
const listeners = new Set<() => void>()

/** What the banner shows: running, restarting, or failed (keeps stopping). */
export function backendState(): DesktopBackendState {
  if (desktopState !== 'running') return desktopState
  return unreachable ? 'restarting' : 'running'
}

export function backendDown(): boolean {
  return backendState() !== 'running'
}

export function subscribeBackendState(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

function update(change: () => void): void {
  const before = backendState()
  change()
  if (backendState() !== before) for (const listener of listeners) listener()
}

function setUnreachable(value: boolean): void {
  update(() => {
    unreachable = value
  })
  if (value && !probe) {
    probe = setInterval(() => {
      void globalThis.fetch(PROBE_URL).catch(() => undefined)
    }, PROBE_INTERVAL_MS)
  } else if (!value && probe) {
    clearInterval(probe)
    probe = null
  }
}

export function setDesktopBackendState(state: DesktopBackendState): void {
  update(() => {
    desktopState = state
  })
  // The desktop app has just seen it answer; no need to wait for a probe.
  if (state === 'running') setUnreachable(false)
}

/**
 * A gateway error the backend itself didn't send: the desktop proxy's own marker, or a
 * 502/503/504 that isn't one of the backend's JSON errors.
 */
export function isUnreachableResponse(response: Response): boolean {
  if (response.headers.get(UNREACHABLE_HEADER) === 'unreachable') return true
  if (!GATEWAY_STATUSES.has(response.status)) return false
  return !(response.headers.get('content-type') ?? '').includes(
    'application/json'
  )
}

/** The error fetch throws when nothing answers (Chromium, Node and Firefox wordings). */
export function isUnreachableError(error: unknown): boolean {
  return (
    error instanceof TypeError &&
    /failed to fetch|fetch failed|networkerror/i.test(error.message)
  )
}

function isApiRequest(input: RequestInfo | URL): boolean {
  const raw =
    typeof input === 'string'
      ? input
      : input instanceof URL
        ? input.href
        : input.url
  try {
    const url = new URL(raw, window.location.href)
    return (
      url.origin === window.location.origin && url.pathname.startsWith('/api/')
    )
  } catch {
    return false
  }
}

/** Wraps fetch so every /api call reports whether the backend answered. */
export function observeApiFetch(fetchImpl: typeof fetch): typeof fetch {
  return async (input, init) => {
    if (!isApiRequest(input)) return fetchImpl(input, init)
    try {
      const response = await fetchImpl(input, init)
      setUnreachable(isUnreachableResponse(response))
      return response
    } catch (error) {
      if (isUnreachableError(error)) setUnreachable(true)
      throw error
    }
  }
}

/** One retry for a real failure; while the backend is down, wait for it instead. */
export function retryQuery(failureCount: number): boolean {
  return failureCount < 1 || backendDown()
}

/** While the backend is down, a result (often an empty fallback) keeps the data on screen. */
export function keepDataWhileDown(oldData: unknown, newData: unknown): unknown {
  if (backendDown() && oldData !== undefined) return oldData
  return replaceEqualDeep(oldData, newData)
}

/**
 * Watch the backend for the whole app and keep React Query offline while it is down. The
 * query client takes retryQuery and keepDataWhileDown as defaults. Returns a cleanup.
 */
export function installBackendReachability(): () => void {
  const originalFetch = window.fetch
  window.fetch = observeApiFetch(originalFetch.bind(window))
  // Only the backend counts: it's local, so the machine being offline doesn't stop it answering.
  onlineManager.setEventListener((setOnline) => {
    const refresh = () => setOnline(!backendDown())
    const unsubscribe = subscribeBackendState(refresh)
    refresh()
    return unsubscribe
  })
  const desktop = getDesktopBackend()
  void desktop
    ?.getState()
    .then(setDesktopBackendState)
    .catch(() => undefined)
  const stopDesktop = desktop?.onStateChange(setDesktopBackendState)
  return () => {
    window.fetch = originalFetch
    stopDesktop?.()
    onlineManager.setEventListener((setOnline) => {
      setOnline(true)
      return undefined
    })
    update(() => {
      desktopState = 'running'
    })
    setUnreachable(false)
  }
}

export function useBackendState(): DesktopBackendState {
  return useSyncExternalStore(
    subscribeBackendState,
    backendState,
    () => 'running'
  )
}
