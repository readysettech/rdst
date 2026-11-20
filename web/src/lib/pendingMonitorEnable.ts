/**
 * "Turn monitoring on once I'm signed in", kept across a sign-in that leaves the page.
 *
 * Google sign-in in a browser replaces the page, so the enable panel's in-memory intent is
 * gone when the user comes back. The panel stores it here and finishes the job when it
 * reopens signed in.
 */

const KEY = 'rdst_enable_after_signin'
// Long enough for a sign-in round trip, short enough that a stale click doesn't act later.
const MAX_AGE_MS = 30 * 60 * 1000

export interface PendingEnable {
  target: string
  automatic: boolean
  measure: boolean
}

interface Stored extends PendingEnable {
  at: number
}

export function rememberEnableAfterSignIn(pending: PendingEnable): void {
  try {
    const stored: Stored = { ...pending, at: Date.now() }
    localStorage.setItem(KEY, JSON.stringify(stored))
  } catch {
    /* without storage the user turns monitoring on with one more click */
  }
}

export function forgetEnableAfterSignIn(): void {
  try {
    localStorage.removeItem(KEY)
  } catch {
    /* nothing stored */
  }
}

/** The pending intent for this target, if it's fresh; reading it clears it. */
export function takeEnableAfterSignIn(target: string): PendingEnable | null {
  let raw: string | null = null
  try {
    raw = localStorage.getItem(KEY)
  } catch {
    return null
  }
  if (!raw) return null
  let stored: Partial<Stored>
  try {
    stored = JSON.parse(raw) as Partial<Stored>
  } catch {
    forgetEnableAfterSignIn()
    return null
  }
  if (stored.target !== target) return null
  forgetEnableAfterSignIn()
  if (typeof stored.at !== 'number' || Date.now() - stored.at > MAX_AGE_MS)
    return null
  return {
    target,
    automatic: stored.automatic !== false,
    measure: stored.measure !== false,
  }
}
