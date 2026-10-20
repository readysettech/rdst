// When to invite a user to the Feedback & help chat. The invitation never
// interrupts the first minutes of a visit and backs off for weeks after it
// shows, so it reads as an occasional question and not a nag. State lives in
// localStorage; a store that will not persist degrades to "never show", the
// quiet failure.

const STATE_KEY = 'rdst.feedback.nudge'

const DAY_MS = 24 * 60 * 60 * 1000
export const SESSION_DELAY_MS = 3 * 60 * 1000
const SNOOZE_DAYS = { shown: 14, dismissed: 30, sent: 60 } as const

type NudgeState = { snoozedUntil?: number; never?: boolean }
export type SnoozeReason = keyof typeof SNOOZE_DAYS

function read(): NudgeState | null {
  try {
    const raw = localStorage.getItem(STATE_KEY)
    return raw ? (JSON.parse(raw) as NudgeState) : {}
  } catch {
    return null
  }
}

function write(state: NudgeState): void {
  try {
    localStorage.setItem(STATE_KEY, JSON.stringify(state))
  } catch {
    // A store that will not persist just means the nudge stays away.
  }
}

export function nudgeDue(now = Date.now()): boolean {
  const state = read()
  if (!state || state.never) return false
  return (state.snoozedUntil ?? 0) <= now
}

/** Hold the nudge back; `shown` applies even if the user ignores it. */
export function snoozeNudge(reason: SnoozeReason, now = Date.now()): void {
  write({ ...read(), snoozedUntil: now + SNOOZE_DAYS[reason] * DAY_MS })
}

export function silenceNudge(): void {
  write({ never: true })
}
