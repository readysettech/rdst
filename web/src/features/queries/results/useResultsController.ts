import { useNavigate } from '@tanstack/react-router'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { hasParameters } from '../../../components/top'
import { useTarget } from '../../../hooks/useTarget'
import {
  analysisRunKey,
  useAnalysisRunKeyForHash,
} from '../../../lib/analysisRuns'
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
  // Attaching by identity is what lets a run outlive the view that started it:
  // closing the analyze drawer leaves the run in flight, and reopening it — or
  // opening `/results` for the same query — picks it back up. The registry hash
  // leads, because a reopened view cannot always rebuild the request the run
  // was started with: parameter values substituted into the SQL belong to the
  // view that collected them. The request key covers runs with no hash yet.
  const hashRunKey = useAnalysisRunKeyForHash(hash)
  const requestRunKey = query ? analysisRunKey({ query, target, fast }) : null
  const live = useAnalyze(hashRunKey ?? requestRunKey)
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
  const parameterSubmissionPending = useRef<string | null>(null)

  const [isInteractiveOpen, setIsInteractiveOpen] = useState(false)
  const [hasExistingChat, setHasExistingChat] = useState(false)
  const [showParamDialog, setShowParamDialog] = useState(false)
  const [showAnalyzeConsent, setShowAnalyzeConsent] = useState(false)
  const [skipAnalyzeConsent, setSkipAnalyzeConsent] = useState(false)
  // Consent as this view holds it. The stored flag only records the "don't ask
  // again" answer, so a consent given for this run alone lives here.
  const [analyzeConsented, setAnalyzeConsented] = useState(hasAnalyzeConsent)
  // A pre-run step the user backed out of. Nothing re-opens it until the query
  // changes or the user asks for the run again.
  const [preRunDismissed, setPreRunDismissed] = useState(false)
  // SQL this session produced by filling the parameter form. Its slots are
  // already concrete, so it is never scanned for placeholders again: a value
  // is data, and re-reading it as SQL is what used to turn `alice@example.com`
  // into a parameter named `@example` and dead-end the run.
  const [substitutedQuery, setSubstitutedQuery] = useState<string | null>(null)
  const queryHasParams = useMemo(
    () => query !== substitutedQuery && hasParameters(query),
    [query, substitutedQuery]
  )

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

  /**
   * The steps left once consent is settled: the values the placeholders need,
   * then the run itself. One step is on screen at a time, and the last of them
   * measures without asking again.
   */
  const runOnceConsented = useCallback(() => {
    if (!query || !passwordLock.isResolved || passwordLock.isLocked) return
    setPreRunDismissed(false)
    setShowAnalyzeConsent(false)
    if (queryHasParams) {
      setShowParamDialog(true)
      return
    }
    void startAnalysis()
  }, [
    passwordLock.isLocked,
    passwordLock.isResolved,
    query,
    queryHasParams,
    startAnalysis,
  ])

  /**
   * Ask for a measurement. Consent for executing the user's query comes first
   * and replaces the rest of the sequence until it is answered, so a run is
   * never confirmed twice.
   */
  const requestAnalysis = useCallback(() => {
    if (!query || !passwordLock.isResolved || passwordLock.isLocked) return
    if (!analyzeConsented) {
      setPreRunDismissed(false)
      setShowParamDialog(false)
      setShowAnalyzeConsent(true)
      return
    }
    runOnceConsented()
  }, [
    analyzeConsented,
    passwordLock.isLocked,
    passwordLock.isResolved,
    query,
    runOnceConsented,
  ])

  const confirmAnalysis = useCallback(() => {
    if (skipAnalyzeConsent) {
      try {
        window.localStorage.setItem(ANALYZE_CONSENT_KEY, 'accepted')
      } catch {
        // Storage may be unavailable; consent still applies to this run.
      }
    }
    setAnalyzeConsented(true)
    runOnceConsented()
  }, [runOnceConsented, skipAnalyzeConsent])

  const cancelAnalysis = useCallback(() => {
    setShowAnalyzeConsent(false)
    setSkipAnalyzeConsent(false)
    setPreRunDismissed(true)
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

  /**
   * Measure a corrected version of the query. The SQL is the subject of this
   * view, so an edit opens the results on the new query and the run starts
   * from idle against it. [B-21]
   */
  const editQuery = useCallback(
    (edited: string) => {
      live.reset()
      setSubstitutedQuery(null)
      openSearch(
        { query: edited, target, fast, returnSearch, origin, hash },
        { replace: true }
      )
    },
    [fast, hash, live.reset, openSearch, origin, returnSearch, target]
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

  useEffect(() => {
    setPreRunDismissed(false)
  }, [query])

  // Reading a stored result touches no database, so no pre-run step applies
  // while one is open. Otherwise landing here asks for a measurement, and the
  // step machine decides which question that means asking first.
  useEffect(() => {
    if (analysis.state !== 'idle' || stored.isActive) return
    // Submitting parameter values updates the URL asynchronously. During the
    // intervening render `query` still contains placeholders, so reopening the
    // prompt here would strand the submitted query before it can run.
    if (parameterSubmissionPending.current) return
    if (showAnalyzeConsent || showParamDialog || preRunDismissed) return
    requestAnalysis()
  }, [
    analysis.state,
    preRunDismissed,
    requestAnalysis,
    showAnalyzeConsent,
    showParamDialog,
    stored.isActive,
  ])

  useEffect(() => {
    if (parameterSubmissionPending.current !== query) return
    parameterSubmissionPending.current = null
    void startAnalysis()
  }, [query, startAnalysis])

  useEffect(() => {
    if (analysis.state === 'complete' && analysis.results?.query_hash) {
      checkConversationStatus(analysis.results.query_hash)
    }
  }, [analysis.results?.query_hash, analysis.state, checkConversationStatus])

  const submitParameters = useCallback(
    (filledQuery: string) => {
      parameterSubmissionPending.current = filledQuery
      setShowParamDialog(false)
      setSubstitutedQuery(filledQuery)
      openSearch(
        { query: filledQuery, target, fast, returnSearch, origin },
        { replace: true }
      )
    },
    [fast, openSearch, origin, returnSearch, target]
  )

  const cancelParameters = useCallback(() => {
    setShowParamDialog(false)
    setPreRunDismissed(true)
    goBack()
  }, [goBack])

  // Stepping back out of the prompt alone, for Escape: the surface it sits in
  // stays, and the "Parameter values required" card is the way back in. [B-17]
  const dismissParameters = useCallback(() => setShowParamDialog(false), [])

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
      /** The SQL on screen carries values this session filled in. */
      isSubstituted: substitutedQuery !== null && substitutedQuery === query,
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
      editQuery,
      reRunStored,
      openStoredAnalysis,
      cacheQuery,
      setUpCaching,
      recover,
      openParameters: () => setShowParamDialog(true),
      submitParameters,
      cancelParameters,
      dismissParameters,
      confirmAnalysis,
      cancelAnalysis,
      setSkipAnalyzeConsent,
      openInteractive: () => setIsInteractiveOpen(true),
      closeInteractive,
    },
  }
}

export type ResultsController = ReturnType<typeof useResultsController>
