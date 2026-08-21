import { useNavigate } from '@tanstack/react-router'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { hasParameters } from '../../../components/top'
import { useTarget } from '../../../hooks/useTarget'
import { analysisRunKey } from '../../../lib/analysisRuns'
import type { AnalysisRerunReason } from '../../../lib/analytics'
import { trackEvent } from '../../../lib/analytics'
import type {
  AnalysisState,
  CompleteEvent,
  ProgressEvent,
  ReadysetCacheability,
  RewriteTesting,
} from '../../../lib/api'
import type { ApiErrorEnvelope } from '../../../lib/errorContract'
import { useAnalyze } from '../../../lib/sse'
import { useCacheAction } from '../../../lib/useCacheAction'
import { useTargetConnectivityGate } from '../../../lib/useTargetConnectivityGate'
import { useTargetPasswordLock } from '../../../lib/useTargetPasswordLock'
import {
  parseQueryLibrarySearch,
  type QueryLibrarySearch,
} from '../library/queryLibraryState'
import { analysisAgeBucket } from './storedAnalysis'
import type { ResultsOrigin, ResultsSearch } from './types'
import { useStoredAnalysis } from './useStoredAnalysis'

// Legacy/direct links carry neither `origin` nor `returnSearch` — they have
// always landed on the Query Library, so that stays the fallback origin.
const DEFAULT_ORIGIN: ResultsOrigin = 'query-library'

const ORIGIN_BACK_LABEL: Record<ResultsOrigin, string> = {
  home: 'Back to Home',
  ask: 'Back to Ask',
  'slow-queries': 'Back to Slow queries',
  scan: 'Back to Code scan',
  'query-library': 'Back to queries',
}

const ANALYZE_CONSENT_KEY = 'rdst.explain-analyze-consent'

function hasAnalyzeConsent() {
  try {
    return window.localStorage.getItem(ANALYZE_CONSENT_KEY) === 'accepted'
  } catch {
    return false
  }
}

function parseStoredParameters(params?: string) {
  if (!params) return undefined
  try {
    return JSON.parse(params) as Record<string, string | number>
  } catch {
    return undefined
  }
}

function parseReturnSearch(value?: string): QueryLibrarySearch | undefined {
  if (!value) return undefined
  try {
    const parsed = JSON.parse(value)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return undefined
    }
    return parseQueryLibrarySearch(parsed as Record<string, unknown>)
  } catch {
    return undefined
  }
}

/**
 * What the results presentation needs from a finished or in-flight analysis.
 * A live run and a replayed stored record both satisfy it, which is what lets
 * `/results` show either without forking the rendering tree.
 */
type ResultsAnalysisView = {
  state: AnalysisState
  progress: ProgressEvent | undefined
  results: CompleteEvent | undefined
  rewriteTesting: RewriteTesting | undefined
  readysetCacheability: ReadysetCacheability | undefined
  error: string | undefined
  errorEnvelope: ApiErrorEnvelope | undefined
}

/**
 * How a results view moves. `/results` navigates; the analyze drawer rewrites
 * its own `?analyze=` state over the Query Library. One controller, two
 * shells — the presentation below it is identical.
 */
export interface ResultsShell {
  /** Show different results in the same shell. */
  openSearch: (next: ResultsSearch, options?: { replace?: boolean }) => void
  /** Leave the results view for wherever the user came from. */
  goBack: () => void
}

export interface ResultsControllerOptions {
  /** What the sidebar job for a run this view starts is called. */
  jobLabel?: string
}

export function useResultsController(
  search: ResultsSearch,
  shell?: ResultsShell,
  options: ResultsControllerOptions = {}
) {
  const navigate = useNavigate()
  const {
    query,
    target,
    fast = false,
    params,
    returnSearch,
    origin,
    hash,
    analysisId,
  } = search
  const resolvedOrigin = origin ?? DEFAULT_ORIGIN
  const backLabel = ORIGIN_BACK_LABEL[resolvedOrigin]
  const storedParams = useMemo(() => parseStoredParameters(params), [params])
  const queryLibraryReturnSearch = useMemo(
    () => parseReturnSearch(returnSearch),
    [returnSearch]
  )
  // Attaching by request identity is what lets a run outlive the view that
  // started it: closing the analyze drawer leaves the run in flight, and
  // reopening it — or opening `/results` for the same query — picks it back up.
  const runKey = query ? analysisRunKey({ query, target, fast }) : null
  const live = useAnalyze(runKey)
  const stored = useStoredAnalysis({ hash, analysisId })
  const storedView: ResultsAnalysisView = {
    state: stored.hasBody ? 'complete' : 'idle',
    progress: undefined,
    results: stored.results,
    rewriteTesting: stored.rewriteTesting,
    readysetCacheability: stored.readysetCacheability,
    error: undefined,
    errorEnvelope: undefined,
  }
  const analysis: ResultsAnalysisView = stored.isActive ? storedView : live
  const { setTarget } = useTarget()
  const passwordLock = useTargetPasswordLock(target)
  const connectivity = useTargetConnectivityGate(
    passwordLock.targetName ?? target
  )
  const cache = useCacheAction({ target: target || null })
  const analysisRequestPending = useRef(false)

  const [isInteractiveOpen, setIsInteractiveOpen] = useState(false)
  const [hasExistingChat, setHasExistingChat] = useState(false)
  const [showParamDialog, setShowParamDialog] = useState(false)
  const [paramDialogShown, setParamDialogShown] = useState(false)
  const [showAnalyzeConsent, setShowAnalyzeConsent] = useState(false)
  const [skipAnalyzeConsent, setSkipAnalyzeConsent] = useState(false)
  const queryHasParams = useMemo(() => hasParameters(query), [query])

  const openSearch = useCallback(
    (next: ResultsSearch, options?: { replace?: boolean }) => {
      if (shell) {
        shell.openSearch(next, options)
        return
      }
      navigate({ to: '/results', search: next, replace: options?.replace })
    },
    [navigate, shell]
  )

  const goBack = useCallback(() => {
    if (shell) {
      shell.goBack()
      return
    }
    if (resolvedOrigin === 'home') {
      navigate({ to: '/' })
      return
    }
    if (resolvedOrigin === 'ask') {
      navigate({ to: '/ask' })
      return
    }
    if (resolvedOrigin === 'scan') {
      navigate({ to: '/scan' })
      return
    }
    // 'slow-queries' lives inside the Query Library's live-capture panel, and
    // 'query-library' (the default) is the Query Library itself.
    if (queryLibraryReturnSearch) {
      navigate({ to: '/queries', search: queryLibraryReturnSearch })
      return
    }
    navigate({ to: '/queries' })
  }, [navigate, queryLibraryReturnSearch, resolvedOrigin, shell])

  const cacheQuery = useCallback(() => {
    if (query && target) cache.cacheQuery(query, query)
  }, [cache.cacheQuery, query, target])

  const setUpCaching = useCallback(() => {
    if (target) setTarget(target)
    navigate({
      to: '/queries',
      search: {
        hash: analysis.results?.query_hash ?? undefined,
      },
    })
  }, [analysis.results?.query_hash, navigate, setTarget, target])

  const recover = useCallback(
    (to: string) => {
      if (to === '/cache') {
        navigate({ to: '/cache' })
      } else if (to === '/configure') {
        navigate({ to: '/configure' })
      } else {
        goBack()
      }
    },
    [goBack, navigate]
  )

  const startAnalysis = useCallback(async () => {
    if (!query || !passwordLock.isResolved || passwordLock.isLocked) return
    if (analysisRequestPending.current) return
    analysisRequestPending.current = true
    try {
      if (!(await connectivity.ensureReachable())) return
      trackEvent('analysis_started', { origin: resolvedOrigin })
      live.analyze(
        { query, target, fast },
        { queryHash: hash, queryLabel: options.jobLabel }
      )
    } finally {
      analysisRequestPending.current = false
    }
  }, [
    live.analyze,
    connectivity.ensureReachable,
    fast,
    hash,
    options.jobLabel,
    passwordLock.isLocked,
    passwordLock.isResolved,
    query,
    resolvedOrigin,
    target,
  ])

  const requestAnalysis = useCallback(() => {
    if (!query || !passwordLock.isResolved || passwordLock.isLocked) return
    if (hasAnalyzeConsent()) {
      void startAnalysis()
      return
    }
    setShowAnalyzeConsent(true)
  }, [passwordLock.isLocked, passwordLock.isResolved, query, startAnalysis])

  const confirmAnalysis = useCallback(() => {
    if (skipAnalyzeConsent) {
      try {
        window.localStorage.setItem(ANALYZE_CONSENT_KEY, 'accepted')
      } catch {
        // Storage may be unavailable; consent still applies to this run.
      }
    }
    setShowAnalyzeConsent(false)
    if (query && passwordLock.isResolved && !passwordLock.isLocked) {
      void startAnalysis()
    }
  }, [
    passwordLock.isLocked,
    passwordLock.isResolved,
    query,
    skipAnalyzeConsent,
    startAnalysis,
  ])

  const cancelAnalysis = useCallback(() => {
    setShowAnalyzeConsent(false)
    setSkipAnalyzeConsent(false)
    goBack()
  }, [goBack])

  /**
   * Leave the stored record and measure the query again. Dropping `analysisId`
   * from the URL is the whole switch: the same page becomes a live run, and
   * the stored analysis stays in the history rather than being replaced.
   */
  const reRunStored = useCallback(
    (reason: AnalysisRerunReason) => {
      trackEvent('analysis_rerun', { reason })
      openSearch({
        query,
        target,
        fast,
        params,
        returnSearch,
        origin,
        hash,
        analysisId: undefined,
      })
    },
    [fast, hash, openSearch, origin, params, query, returnSearch, target]
  )

  /** The live view's own re-measure, always a deliberate choice by the user. */
  const runAgainDeliberately = useCallback(() => {
    trackEvent('analysis_rerun', { reason: 'manual' })
    requestAnalysis()
  }, [requestAnalysis])

  const openStoredAnalysis = useCallback(
    (id: string) => {
      openSearch({
        query,
        target,
        fast,
        params,
        returnSearch,
        origin,
        hash,
        analysisId: id,
      })
    },
    [fast, hash, openSearch, origin, params, query, returnSearch, target]
  )

  const checkConversationStatus = useCallback(async (queryHash: string) => {
    try {
      const response = await fetch(`/api/interactive/${queryHash}/status`)
      if (!response.ok) return
      const data = (await response.json()) as {
        exists?: boolean
        total_exchanges?: number
      }
      setHasExistingChat(
        Boolean(data.exists && (data.total_exchanges ?? 0) > 0)
      )
    } catch {
      setHasExistingChat(false)
    }
  }, [])

  // Reading a stored result touches no database, so neither the parameter
  // prompt nor the auto-run applies while one is open.
  useEffect(() => {
    if (
      passwordLock.isResolved &&
      !passwordLock.isLocked &&
      queryHasParams &&
      !paramDialogShown &&
      !stored.isActive
    ) {
      setShowParamDialog(true)
      setParamDialogShown(true)
    }
  }, [
    paramDialogShown,
    passwordLock.isLocked,
    passwordLock.isResolved,
    queryHasParams,
    stored.isActive,
  ])

  useEffect(() => {
    setParamDialogShown(false)
  }, [query])

  useEffect(() => {
    if (
      analysis.state === 'idle' &&
      query &&
      passwordLock.isResolved &&
      !queryHasParams &&
      !passwordLock.isLocked &&
      !stored.isActive
    ) {
      requestAnalysis()
    }
  }, [
    analysis.state,
    passwordLock.isLocked,
    passwordLock.isResolved,
    query,
    queryHasParams,
    requestAnalysis,
    stored.isActive,
  ])

  useEffect(() => {
    if (analysis.state === 'complete' && analysis.results?.query_hash) {
      checkConversationStatus(analysis.results.query_hash)
    }
  }, [analysis.results?.query_hash, analysis.state, checkConversationStatus])

  const submitParameters = useCallback(
    (substitutedQuery: string) => {
      setShowParamDialog(false)
      openSearch(
        { query: substitutedQuery, target, fast, returnSearch, origin },
        { replace: true }
      )
    },
    [fast, openSearch, origin, returnSearch, target]
  )

  const cancelParameters = useCallback(() => {
    setShowParamDialog(false)
    goBack()
  }, [goBack])

  const closeInteractive = useCallback(() => {
    setIsInteractiveOpen(false)
    if (analysis.results?.query_hash) {
      checkConversationStatus(analysis.results.query_hash)
    }
  }, [analysis.results?.query_hash, checkConversationStatus])

  return {
    query: {
      sql: query,
      target,
      fast,
    },
    origin: resolvedOrigin,
    backLabel,
    analysis,
    stored: {
      isActive: stored.isActive,
      isLoading: stored.isLoading,
      hasBody: stored.hasBody,
      error: stored.error,
      record: stored.record,
      history: stored.history,
      ageBucket: stored.record
        ? analysisAgeBucket(stored.record.created_at)
        : null,
    },
    passwordLock,
    connectivity,
    cache: {
      isPending: cache.isPending,
    },
    parameters: {
      hasParameters: queryHasParams,
      isOpen: showParamDialog,
      initialValues: storedParams,
    },
    consent: {
      isOpen: showAnalyzeConsent,
      skipFuturePrompts: skipAnalyzeConsent,
    },
    chat: {
      isOpen: isInteractiveOpen,
      hasExisting: hasExistingChat,
      results: {
        analysis_id: analysis.results?.analysis_id || 'unknown',
        target: target || 'unknown',
        query_sql: query,
        explain_results: analysis.results?.explain_results,
        llm_analysis: analysis.results?.llm_analysis,
      },
    },
    actions: {
      goBack,
      runAgain: requestAnalysis,
      runAgainDeliberately,
      reRunStored,
      openStoredAnalysis,
      cacheQuery,
      setUpCaching,
      recover,
      openParameters: () => setShowParamDialog(true),
      submitParameters,
      cancelParameters,
      confirmAnalysis,
      cancelAnalysis,
      setSkipAnalyzeConsent,
      openInteractive: () => setIsInteractiveOpen(true),
      closeInteractive,
    },
  }
}

export type ResultsController = ReturnType<typeof useResultsController>
