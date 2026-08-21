/**
 * The last origin-vs-Readyset comparison, read from the registry row.
 *
 * The outcome is stored server-side, so it is reportable on any browser rather
 * than only on the one that ran the comparison. Every field is optional: a
 * server that has not recorded one yet, or recorded only part of one, must
 * produce a row that says nothing rather than a row that says "0x faster".
 */

import type { QueryCompareOutcome } from '../../../lib/api'
import { formatMs } from '../../../lib/formatters'
import type { ResultTone } from '../results/resultsSelectors'
import { relativeAge } from '../results/storedAnalysis'

export type CompareSummary = {
  /** The outcome in the words a person would use, e.g. "3.2x faster". */
  headline: string
  tone: ResultTone
  /** When it was measured, relative; null when the row records no time. */
  when: string | null
  /** The measurement behind the headline, when there is one to show. */
  detail: string | null
}

function positive(value?: number | null) {
  return typeof value === 'number' && Number.isFinite(value) && value > 0
    ? value
    : null
}

const FAILED_STATUSES = new Set(['failed', 'error', 'errored'])

export function compareOutcomeSummary(
  outcome?: QueryCompareOutcome | null,
  nowMs = Date.now()
): CompareSummary | null {
  if (!outcome) return null

  const readyset = positive(outcome.readyset_ms)
  const origin = positive(outcome.origin_ms)
  const when = outcome.at ? relativeAge(outcome.at, nowMs) : null
  const status = outcome.status?.trim().toLowerCase()
  const note = outcome.detail?.trim() || null

  if (readyset && origin) {
    const ratio = origin / readyset
    const measurement = `Readyset ${formatMs(readyset)} vs origin ${formatMs(origin)}`
    if (ratio >= 1.05) {
      return {
        headline: `${ratio.toFixed(1)}x faster`,
        tone: 'positive',
        when,
        detail: measurement,
      }
    }
    if (ratio <= 0.95) {
      return {
        headline: `${(1 / ratio).toFixed(1)}x slower`,
        tone: 'negative',
        when,
        detail: measurement,
      }
    }
    return {
      headline: 'About the same',
      tone: 'informative',
      when,
      detail: measurement,
    }
  }

  if (status && FAILED_STATUSES.has(status)) {
    return {
      headline: 'Comparison failed',
      tone: 'negative',
      when,
      detail: note,
    }
  }
  if (!status && !note) return null
  return { headline: 'Compared', tone: 'informative', when, detail: note }
}
