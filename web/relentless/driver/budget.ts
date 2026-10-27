/** The longest a single `rt wait --seconds` lets time pass. */
export const MAX_WAIT_SECONDS = 300

/** Time a run keeps after its last wait to file what it found and finish. */
const FINISH_RESERVE_SECONDS = 180

/**
 * How long `rt wait --seconds` waits: what was asked, within the per-call
 * cap, and never into the minutes the run needs to finish before its limit.
 */
export function waitSeconds(
  requested: number,
  deadline?: number,
  now = Date.now()
) {
  const spare =
    deadline === undefined
      ? Infinity
      : (deadline - now) / 1000 - FINISH_RESERVE_SECONDS
  return Math.max(0, Math.floor(Math.min(requested, MAX_WAIT_SECONDS, spare)))
}

/** Whole minutes until the run's limit, or undefined when it has none. */
export function minutesLeft(deadline?: number, now = Date.now()) {
  return deadline === undefined
    ? undefined
    : Math.max(0, Math.floor((deadline - now) / 60_000))
}
