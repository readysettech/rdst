// Route-ignored page module (TanStack skips `-`-prefixed files). The page
// component lives here — not in the route config — so the code-splitter can
// relocate its CodeMirror/SQL imports out of the eager entry chunk. The route
// file imports `ResultsPage` only for its `component:` wrapper; the tests import
// it from here. See evidence/gates-final.md §Defect D-1.

import { Button } from '@rs/ui-new/button'
import { CopyButton } from '@rs/ui-new/copy-button'
import { Icon } from '@rs/ui-new/icon'
import { m } from '@rs/ui-new/motion'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AnalysisResults,
  InteractivePanel,
  SQLDisplay,
  TargetLockNotice,
} from '../components'
import { TrialRegistrationDialog } from '../components/TrialRegistrationDialog'
import { hasParameters, ParameterDialog } from '../components/top'
import { useTarget } from '../hooks/useTarget'
import { useAnalyze } from '../lib/sse'
import { invalidateTrialRelatedQueries } from '../lib/trialQueries'
import { useTargetPasswordLock } from '../lib/useTargetPasswordLock'
import type { ResultsSearch } from './results'

interface ResultsPageProps {
  search: ResultsSearch
}

export function ResultsPage({ search }: ResultsPageProps) {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { query, target, fast, params: paramsJson } = search
  const storedParams = useMemo(() => {
    if (!paramsJson) return undefined
    try {
      return JSON.parse(paramsJson) as Record<string, string | number>
    } catch {
      return undefined
    }
  }, [paramsJson])
  const {
    analyze,
    state,
    progress,
    results,
    rewriteTesting,
    readysetCacheability,
    error,
    errorEnvelope,
  } = useAnalyze()
  const { setTarget } = useTarget()
  const passwordLock = useTargetPasswordLock(target)
  const [isInteractiveOpen, setIsInteractiveOpen] = useState(false)
  const [showTrialDialog, setShowTrialDialog] = useState(false)

  // Hand the diagnosed query directly to Comparisons, focused on its row.
  // Switch the selected target first because the query list is target-scoped.
  const handleSetUpCaching = useCallback(() => {
    if (target) setTarget(target)
    navigate({
      to: '/cache',
      search: { hash: results?.query_hash ?? undefined },
    })
  }, [navigate, results?.query_hash, target, setTarget])

  // Route an error-state recovery action to a known destination (type-safe
  // navigation; the shared contract only ever hands back these routes).
  const handleRecover = useCallback(
    (to: string) => {
      if (to === '/cache') {
        navigate({ to: '/cache', search: { hash: undefined } })
      } else if (to === '/configure') {
        navigate({ to: '/configure' })
      } else {
        navigate({ to: '/analyze' })
      }
    },
    [navigate]
  )

  const handleRetryAnalyze = useCallback(() => {
    if (query) {
      analyze({ query, target, fast })
    }
  }, [analyze, query, target, fast])
  const [hasExistingChat, setHasExistingChat] = useState(false)

  // Check if query has parameters that need substitution
  const queryHasParams = useMemo(() => hasParameters(query), [query])
  const [showParamDialog, setShowParamDialog] = useState(false)
  const [paramDialogShown, setParamDialogShown] = useState(false)

  // Check if there's an existing conversation for this query
  const checkConversationStatus = useCallback(async (queryHash: string) => {
    try {
      const res = await fetch(`/api/interactive/${queryHash}/status`)
      if (res.ok) {
        const data = await res.json()
        setHasExistingChat(data.exists && data.total_exchanges > 0)
      }
    } catch {
      setHasExistingChat(false)
    }
  }, [])

  // Show param dialog if query has params and we haven't shown it yet
  useEffect(() => {
    if (queryHasParams && !paramDialogShown) {
      setShowParamDialog(true)
      setParamDialogShown(true)
    }
  }, [queryHasParams, paramDialogShown])

  // Reset paramDialogShown when query changes
  useEffect(() => {
    setParamDialogShown(false)
  }, [query])

  useEffect(() => {
    // Only analyze if query doesn't have params (or params were already substituted)
    if (query && !queryHasParams && !passwordLock.isLocked) {
      analyze({ query, target, fast })
    }
  }, [query, target, fast, analyze, queryHasParams, passwordLock.isLocked])

  const handleParamSubmit = useCallback(
    (substitutedQuery: string) => {
      setShowParamDialog(false)
      // Navigate to the same page with the substituted query
      navigate({
        to: '/results',
        search: {
          query: substitutedQuery,
          target,
          fast,
        },
        replace: true, // Replace history entry so back button works correctly
      })
    },
    [navigate, target, fast]
  )

  const handleParamCancel = useCallback(() => {
    setShowParamDialog(false)
    // Go back to the query editor
    navigate({ to: '/analyze' })
  }, [navigate])

  // Check conversation status when analysis completes
  useEffect(() => {
    if (state === 'complete' && results?.query_hash) {
      checkConversationStatus(results.query_hash)
    }
  }, [state, results?.query_hash, checkConversationStatus])

  const handlePanelClose = () => {
    setIsInteractiveOpen(false)
    // Refresh status in case user cleared the conversation
    if (results?.query_hash) {
      checkConversationStatus(results.query_hash)
    }
  }

  // Construct analysisResults with required fields for InteractiveService
  // (see interactive_service.py:316-318 for required keys)
  const analysisResultsForChat = {
    analysis_id: results?.analysis_id || 'unknown',
    target: target || 'unknown',
    query_sql: query || '',
    explain_results: results?.explain_results,
    llm_analysis: results?.llm_analysis,
  }

  return (
    <div className="space-y-6 w-full">
      {/* Header section with back button and actions */}
      <m.div
        className="flex justify-between items-start gap-4"
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        <HStack className="gap-3 items-center">
          <Button
            variant="primary"
            modifier="ghost"
            size="small"
            icon="arrow-left"
            iconPosition="icon"
            label="Back"
            onClick={() => navigate({ to: '/analyze' })}
          />
          <div className="w-px h-5 bg-border-layout-1" />
          <Text as="span" level="body-small" className="text-content-layout-3">
            New Analysis
          </Text>
        </HStack>
      </m.div>

      {passwordLock.isLocked && (
        <TargetLockNotice
          message={passwordLock.message}
          requirements={passwordLock.missingTargetRequirements}
          keyringAvailable={passwordLock.keyringAvailable}
        />
      )}

      {/* Query display card */}
      <m.div
        className="bg-surface-layout-1 rounded-xl border border-border-layout-1 overflow-hidden"
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3, delay: 0.1 }}
      >
        <HStack className="px-4 py-3 border-b border-border-layout-1 justify-between items-center bg-surface-layout-2/50">
          <HStack className="gap-2 items-center">
            <Icon
              name="querypilot"
              label="Query"
              className="w-4 h-4 text-content-layout-3"
            />
            <Text
              level="overline"
              className="text-content-layout-3 uppercase tracking-wider"
            >
              Query
            </Text>
          </HStack>
          <HStack className="gap-2 items-center">
            {/* In-place loop-back: re-run the SAME query to confirm a fix
                helped, without re-typing. [diagnose-to-fix Step 6; T13] */}
            {state === 'complete' && (
              <Button
                variant="primary"
                modifier="ghost"
                size="small"
                label="Re-analyze"
                icon="arrow-left"
                iconPosition="left"
                onClick={handleRetryAnalyze}
              />
            )}
            <CopyButton text={query} />
          </HStack>
        </HStack>
        <SQLDisplay sql={query} className="p-4" />
      </m.div>

      {/* Analysis results */}
      <AnalysisResults
        state={state}
        progress={progress}
        results={results}
        rewriteTesting={rewriteTesting}
        readysetCacheability={readysetCacheability}
        error={error}
        errorEnvelope={errorEnvelope}
        target={target}
        onSetUpCaching={handleSetUpCaching}
        onRecover={handleRecover}
        onRetry={handleRetryAnalyze}
        onStartTrial={() => setShowTrialDialog(true)}
        onAskFollowUp={() => setIsInteractiveOpen(true)}
        hasExistingChat={hasExistingChat}
      />
      <TrialRegistrationDialog
        isOpen={showTrialDialog}
        onClose={() => setShowTrialDialog(false)}
        onSuccess={() => {
          void invalidateTrialRelatedQueries(queryClient)
          setShowTrialDialog(false)
        }}
      />

      {/* Interactive panel */}
      {state === 'complete' && results?.query_hash && (
        <InteractivePanel
          isOpen={isInteractiveOpen}
          onClose={handlePanelClose}
          queryHash={results.query_hash}
          analysisResults={analysisResultsForChat}
        />
      )}

      {/* Parameter warning */}
      {queryHasParams && !showParamDialog && state === 'idle' && (
        <m.div
          className="bg-surface-warning-soft/50 rounded-xl border border-border-warning-soft p-5"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.2 }}
        >
          <HStack className="justify-between items-center gap-4">
            <HStack className="gap-3 items-center">
              <div className="w-8 h-8 rounded-lg bg-surface-warning-soft flex items-center justify-center">
                <Icon
                  name="alert"
                  label="Warning"
                  className="w-4 h-4 text-content-warning-soft"
                />
              </div>
              <VStack className="gap-0.5 items-start">
                <Text level="label-small" className="text-content-warning-soft">
                  Parameterized Query
                </Text>
                <Text level="body-small" className="text-content-layout-2">
                  Enter parameter values to analyze this query.
                </Text>
              </VStack>
            </HStack>
            <Button
              variant="primary"
              modifier="outline"
              size="small"
              label="Enter Parameters"
              icon="edit"
              iconPosition="left"
              onClick={() => setShowParamDialog(true)}
            />
          </HStack>
        </m.div>
      )}

      <ParameterDialog
        isOpen={showParamDialog}
        onClose={handleParamCancel}
        onSubmit={handleParamSubmit}
        query={query}
        initialValues={storedParams}
      />
    </div>
  )
}
