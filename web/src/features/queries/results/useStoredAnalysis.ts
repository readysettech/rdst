import { useQuery } from '@tanstack/react-query'
import { useEffect, useRef } from 'react'
import {
  type StoredAnalysisResults,
  storedAnalysisResults,
} from '../../../lib/analysisPayload'
import { trackEvent } from '../../../lib/analytics'
import {
  type AnalysisHistoryEntry,
  fetchAnalysisHistory,
  fetchStoredAnalysis,
  type StoredAnalysis,
} from '../../../lib/api'
import { analysisAgeBucket } from './storedAnalysis'

export const storedAnalysisQueryKey = (hash: string, analysisId: string) =>
  ['storedAnalysis', hash, analysisId] as const

export const analysisHistoryQueryKey = (hash: string) =>
  ['analysisHistory', hash] as const

export interface StoredAnalysisState extends StoredAnalysisResults {
  /** True while this results view is showing a stored record, not a live run. */
  isActive: boolean
  isLoading: boolean
  /** Present when the record could not be read; the id may no longer exist. */
  error: string | null
  record: StoredAnalysis | null
  history: AnalysisHistoryEntry[]
}

const EMPTY_HISTORY: AnalysisHistoryEntry[] = []

/**
 * A query's stored analyses, newest first, for surfaces that list them without
 * opening one. It shares the key and freshness `useStoredAnalysis` uses, so
 * listing the runs and reading one are a single cache entry.
 */
export function useAnalysisHistory(hash?: string, enabled = true) {
  const history = useQuery({
    queryKey: analysisHistoryQueryKey(hash ?? ''),
    queryFn: () => fetchAnalysisHistory(hash as string),
    enabled: enabled && Boolean(hash),
    staleTime: 60 * 1000,
    retry: false,
  })

  return {
    entries: history.data?.analyses ?? EMPTY_HISTORY,
    isLoading: history.isPending && history.fetchStatus === 'fetching',
  }
}

/**
 * Read one persisted analysis and its siblings so the results view can show a
 * run the user already paid for. Opening a stored record issues no LLM calls
 * and starts no database work — it is two cached GETs.
 */
export function useStoredAnalysis({
  hash,
  analysisId,
}: {
  hash?: string
  analysisId?: string
}): StoredAnalysisState {
  const isActive = Boolean(hash && analysisId)

  const record = useQuery({
    queryKey: storedAnalysisQueryKey(hash ?? '', analysisId ?? ''),
    queryFn: () => fetchStoredAnalysis(hash as string, analysisId as string),
    enabled: isActive,
    staleTime: Number.POSITIVE_INFINITY,
    retry: false,
  })

  // A stored analysis never changes, so the history list only needs fetching
  // while a stored record is open, and only once per query.
  const history = useQuery({
    queryKey: analysisHistoryQueryKey(hash ?? ''),
    queryFn: () => fetchAnalysisHistory(hash as string),
    enabled: isActive,
    staleTime: 60 * 1000,
    retry: false,
  })

  const viewed = useRef<string | null>(null)
  const createdAt = record.data?.created_at
  useEffect(() => {
    if (!isActive || !createdAt) return
    const key = `${hash}:${analysisId}`
    if (viewed.current === key) return
    viewed.current = key
    trackEvent('analysis_viewed_stored', {
      age_bucket: analysisAgeBucket(createdAt),
    })
  }, [analysisId, createdAt, hash, isActive])

  const replayed: StoredAnalysisResults = record.data
    ? storedAnalysisResults(record.data)
    : { hasBody: false }

  return {
    ...replayed,
    isActive,
    isLoading: isActive && record.isLoading,
    error:
      record.error instanceof Error
        ? record.error.message
        : record.error
          ? 'Could not open this analysis.'
          : null,
    record: record.data ?? null,
    history: history.data?.analyses ?? EMPTY_HISTORY,
  }
}
