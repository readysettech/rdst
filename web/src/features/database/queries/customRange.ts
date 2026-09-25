// A custom time frame typed as from/to dates and times (the pickers' local wall clock).
// At least five minutes, inside the stored history: from its first reading to now.
// Pure, so the clamping rules are unit-tested.
import { formatClock, formatDayDate } from '@/features/monitor-kit/format'

/** The shortest custom time frame, in seconds. */
export const MIN_CUSTOM_SECONDS = 300

const pad = (value: number) => String(value).padStart(2, '0')

/** Unix seconds as a datetime-local value ('2026-09-30T14:05'), in local time. */
export function toInputValue(ts: number): string {
  const date = new Date(ts * 1000)
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

/** A datetime-local value as Unix seconds (local time), or null when it isn't one. */
export function fromInputValue(text: string): number | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(
    text.trim()
  )
  if (!match) return null
  const [, y, mo, d, h, mi, s] = match.map(Number)
  const date = new Date(y, mo - 1, d, h, mi, s || 0)
  if (
    date.getFullYear() !== y ||
    date.getMonth() !== mo - 1 ||
    date.getDate() !== d
  )
    return null
  return Math.floor(date.getTime() / 1000)
}

export type CustomRangeResult =
  | { ok: true; window: [number, number]; note: string | null }
  | { ok: false; error: string }

const stamp = (ts: number) => `${formatDayDate(ts)}, ${formatClock(ts)}`

/**
 * The window for a typed from/to. It's refused when it's shorter than five minutes or
 * outside the history altogether; otherwise it's clamped to the history, widening back
 * to five minutes when the clamp cut it shorter.
 */
export function resolveCustomRange(
  fromText: string,
  toText: string,
  { first, now }: { first: number | null | undefined; now: number }
): CustomRangeResult {
  const from = fromInputValue(fromText)
  const to = fromInputValue(toText)
  if (from == null || to == null)
    return { ok: false, error: 'Pick a start and an end, date and time.' }
  if (to <= from)
    return { ok: false, error: 'The end has to be after the start.' }
  if (to - from < MIN_CUSTOM_SECONDS)
    return { ok: false, error: 'Pick at least 5 minutes.' }
  const earliest = first ?? now - MIN_CUSTOM_SECONDS
  if (now - earliest < MIN_CUSTOM_SECONDS)
    return {
      ok: false,
      error: 'There are under 5 minutes of readings so far.',
    }
  if (from >= now)
    return { ok: false, error: "That's after the latest reading." }
  if (to <= earliest)
    return {
      ok: false,
      error: `That's before the first reading, ${stamp(earliest)}.`,
    }
  // Minute inputs can't hit a first reading mid-minute; that minute doesn't count.
  const clippedStart = from < earliest - 59
  const clippedEnd = to > now + 59
  let start = Math.max(from, earliest)
  let end = Math.min(to, now)
  if (end - start < MIN_CUSTOM_SECONDS) {
    if (clippedEnd) start = Math.max(earliest, end - MIN_CUSTOM_SECONDS)
    else end = Math.min(now, start + MIN_CUSTOM_SECONDS)
  }
  const note =
    clippedStart && clippedEnd
      ? 'Trimmed to the stored history.'
      : clippedStart
        ? `Starts at the first reading, ${stamp(start)}.`
        : clippedEnd
          ? 'Ends at the latest reading.'
          : null
  return { ok: true, window: [start, end], note }
}
