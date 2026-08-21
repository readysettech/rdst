/**
 * Plain-language age reporting for a stored analysis.
 *
 * The viewer's whole point is that a result the user already paid for stays
 * readable. Age is the one thing that can make it wrong, so it is stated in
 * words next to the Re-run action rather than left to a badge.
 */

import type { AnalysisAgeBucket } from '../../../lib/analytics'

const HOUR_MS = 3_600_000
const DAY_MS = 86_400_000

/** Bucketed age for `analysis_viewed_stored`. Never carries a timestamp. */
export function analysisAgeBucket(
  isoString: string,
  nowMs = Date.now()
): AnalysisAgeBucket {
  const ageMs = Math.max(0, nowMs - Date.parse(isoString))
  if (ageMs < HOUR_MS) return '<1h'
  if (ageMs < DAY_MS) return '1h-24h'
  if (ageMs < 7 * DAY_MS) return '1d-7d'
  return '7d+'
}

/**
 * How long ago something happened, in the words a person would use: "just
 * now", "2 hours ago", or a plain date once relative time stops helping.
 */
export function relativeAge(isoString: string, nowMs = Date.now()): string {
  const parsed = Date.parse(isoString)
  if (Number.isNaN(parsed)) return 'an unknown time ago'

  const ageMs = Math.max(0, nowMs - parsed)
  const minutes = Math.floor(ageMs / 60_000)
  if (minutes < 1) return 'just now'
  if (minutes < 60)
    return `${minutes} ${minutes === 1 ? 'minute' : 'minutes'} ago`

  const hours = Math.floor(ageMs / HOUR_MS)
  if (hours < 24) return `${hours} ${hours === 1 ? 'hour' : 'hours'} ago`

  const days = Math.floor(ageMs / DAY_MS)
  if (days < 7) return `${days} ${days === 1 ? 'day' : 'days'} ago`

  return `on ${new Date(parsed).toLocaleDateString()}`
}

/**
 * Why this result may no longer describe the database, in one sentence. Null
 * while the analysis is recent enough that nothing needs saying.
 */
export function stalenessNote(
  isoString: string,
  nowMs = Date.now()
): string | null {
  const bucket = analysisAgeBucket(isoString, nowMs)
  if (bucket === '<1h' || bucket === '1h-24h') return null
  if (bucket === '1d-7d') {
    return 'Your data and query plans may have changed since this ran.'
  }
  return 'This is more than a week old, so the database has likely moved on since.'
}

/** Compact "Analyzed 2 hours ago" line for a query card footer. */
export function analyzedAgoLabel(
  isoString: string,
  nowMs = Date.now()
): string {
  return `Analyzed ${relativeAge(isoString, nowMs)}`
}
