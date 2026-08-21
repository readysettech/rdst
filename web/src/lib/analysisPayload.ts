/**
 * Shape reconciliation for a finished analyze payload.
 *
 * A completed run reaches the results view two ways: live, over SSE, and
 * replayed from `library.db` by the stored-analysis viewer. Both arrive as the
 * same `complete` payload, so both go through the normalization here rather
 * than through two copies that can drift.
 */

import type {
  CompleteEvent,
  ReadysetCacheability,
  RewriteTesting,
  StoredAnalysis,
} from './api'

/** Reconcile the shapes a rewrite-testing payload arrives in into one. */
export function normalizeRewriteTesting(
  candidate: unknown
): RewriteTesting | undefined {
  if (!candidate || typeof candidate !== 'object') {
    return undefined
  }

  const testing = candidate as RewriteTesting & {
    success?: boolean
    rewrite_results?: unknown
    best_rewrite?: unknown
  }

  if (typeof testing.tested === 'boolean') {
    return testing
  }

  if (testing.skipped_reason || testing.success === false) {
    return { ...testing, tested: false }
  }

  if (testing.success === true) {
    const rewriteResults = Array.isArray(testing.rewrite_results)
      ? testing.rewrite_results
      : []
    return {
      ...testing,
      tested: rewriteResults.length > 0 || Boolean(testing.best_rewrite),
      rewrite_results: rewriteResults as RewriteTesting['rewrite_results'],
    }
  }

  return undefined
}

/** A stored analysis rendered as the results view's own inputs. */
export interface StoredAnalysisResults {
  /** False when the record predates full result storage. */
  hasBody: boolean
  results?: CompleteEvent
  rewriteTesting?: RewriteTesting
  readysetCacheability?: ReadysetCacheability
}

/**
 * Replay a stored analysis into the inputs `AnalysisResults` already takes.
 * The stored `display_payload` is the `complete` event's own fields, so the
 * live and stored paths hand the presentation identical data.
 */
export function storedAnalysisResults(
  record: StoredAnalysis
): StoredAnalysisResults {
  const payload = record.analysis?.display_payload
  if (!payload || Object.keys(payload).length === 0) {
    return { hasBody: false }
  }

  return {
    hasBody: true,
    results: {
      ...payload,
      type: 'complete',
      success: true,
      query_hash: record.hash,
      analysis_id: payload.analysis_id ?? record.analysis_id,
    },
    rewriteTesting:
      normalizeRewriteTesting(payload.rewrite_testing) ??
      normalizeRewriteTesting(payload.formatted?.rewrite_testing),
    readysetCacheability: payload.readyset_cacheability ?? undefined,
  }
}
