/**
 * Shared provenance note for observed query evidence (run counts, average
 * and max latency, accumulated DB time). These readouts come from the
 * database's cumulative workload statistics, so every surface that shows
 * them attaches this one disclosure rather than restating it.
 */
export const OBSERVED_EVIDENCE_PROVENANCE =
  'Cumulative database statistics since the database last reset its counters, not a recent rate.'
