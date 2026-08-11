import { useNavigate } from '@tanstack/react-router'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { hasParameters } from '../../../components/top'
import { useTarget } from '../../../hooks/useTarget'
import { useAnalyze } from '../../../lib/sse'
import { useCacheAction } from '../../../lib/useCacheAction'
import { useTargetConnectivityGate } from '../../../lib/useTargetConnectivityGate'
import { useTargetPasswordLock } from '../../../lib/useTargetPasswordLock'
import {
  parseQueryLibrarySearch,
  type QueryLibrarySearch,
} from '../library/queryLibraryState'
import type { ResultsSearch } from './types'

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

export function useResultsController(search: ResultsSearch) {
  const navigate = useNavigate()
  const { query, target, fast = false, params, returnSearch } = search
  const storedParams = useMemo(() => parseStoredParameters(params), [params])
  const queryLibraryReturnSearch = useMemo(
    () => parseReturnSearch(returnSearch),
    [returnSearch]
  )
  const analysis = useAnalyze()
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

  const goToQueries = useCallback(() => {
    if (queryLibraryReturnSearch) {
      navigate({ to: '/queries', search: queryLibraryReturnSearch })
      return
    }
    navigate({ to: '/queries' })
  }, [navigate, queryLibraryReturnSearch])

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
        goToQueries()
      }
    },
    [goToQueries, navigate]
  )

  const startAnalysis = useCallback(async () => {
    if (!query || !passwordLock.isResolved || passwordLock.isLocked) return
    if (analysisRequestPending.current) return
    analysisRequestPending.current = true
    try {
      if (!(await connectivity.ensureReachable())) return
      analysis.analyze({ query, target, fast })
    } finally {
      analysisRequestPending.current = false
    }
  }, [
    analysis.analyze,
    connectivity.ensureReachable,
    fast,
    passwordLock.isLocked,
    passwordLock.isResolved,
    query,
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
    goToQueries()
  }, [goToQueries])

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
    if (
      passwordLock.isResolved &&
      !passwordLock.isLocked &&
      queryHasParams &&
      !paramDialogShown
    ) {
      setShowParamDialog(true)
      setParamDialogShown(true)
    }
  }, [
    paramDialogShown,
    passwordLock.isLocked,
    passwordLock.isResolved,
    queryHasParams,
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
      !passwordLock.isLocked
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
  ])

  useEffect(() => {
    if (analysis.state === 'complete' && analysis.results?.query_hash) {
      checkConversationStatus(analysis.results.query_hash)
    }
  }, [analysis.results?.query_hash, analysis.state, checkConversationStatus])

  const submitParameters = useCallback(
    (substitutedQuery: string) => {
      setShowParamDialog(false)
      navigate({
        to: '/results',
        search: {
          query: substitutedQuery,
          target,
          fast,
          returnSearch,
        },
        replace: true,
      })
    },
    [fast, navigate, returnSearch, target]
  )

  const cancelParameters = useCallback(() => {
    setShowParamDialog(false)
    goToQueries()
  }, [goToQueries])

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
    analysis,
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
      goToQueries,
      runAgain: requestAnalysis,
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
