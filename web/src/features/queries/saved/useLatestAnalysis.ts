import { useEffect, useState } from 'react'
import {
  fetchLatestAnalysis,
  type QueryAnalysisSummary,
} from '../../../lib/api'

/**
 * Latest stored analysis summary for one query hash. Fetched on demand from
 * the expanded query detail; the summary is enrichment, so a missing record
 * or a fetch failure simply yields null.
 */
export function useLatestAnalysis(hash: string, enabled: boolean) {
  const [summary, setSummary] = useState<QueryAnalysisSummary | null>(null)

  useEffect(() => {
    if (!enabled || !hash) {
      setSummary(null)
      return
    }
    let cancelled = false
    fetchLatestAnalysis(hash)
      .then((result) => {
        if (!cancelled) setSummary(result.found ? result.analysis : null)
      })
      .catch(() => {
        if (!cancelled) setSummary(null)
      })
    return () => {
      cancelled = true
    }
  }, [enabled, hash])

  return summary
}
