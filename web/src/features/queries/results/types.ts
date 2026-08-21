// The surface a user navigated to /results from. Drives the honest "Back to
// ___" label and destination, and doubles as the `origin` property on the
// `analysis_started`/`back_to_origin` analytics events (B1/E1).
export const RESULTS_ORIGINS = [
  'home',
  'ask',
  'slow-queries',
  'scan',
  'query-library',
] as const

export type ResultsOrigin = (typeof RESULTS_ORIGINS)[number]

export function isResultsOrigin(value: unknown): value is ResultsOrigin {
  return (
    typeof value === 'string' &&
    (RESULTS_ORIGINS as readonly string[]).includes(value)
  )
}

export interface ResultsSearch {
  query: string
  target?: string
  fast?: boolean
  params?: string
  returnSearch?: string
  origin?: ResultsOrigin
  /** Registry hash of the query, needed to reach its stored analyses. */
  hash?: string
  /**
   * Opens a stored analysis read-only instead of starting a run (A3). This is
   * the persisted record's own id, not the `analysis_id` the SSE complete
   * event carries.
   */
  analysisId?: string
}
