import { Alert } from '@rs/ui-new/alert'
import { Disclosure } from '@rs/ui-new/disclosure'
import { m } from '@rs/ui-new/motion'
import type { ReactNode } from 'react'
import {
  AdditionalRecommendationsSection,
  IndexRecommendationsSection,
  TestedOptimizationsSection,
} from '../../../components/analysis/AnalysisSections'
import type {
  CompleteEvent,
  ReadysetCacheability,
  RewriteTesting,
} from '../../../lib/api'
import { ResultsFooter } from './ResultsFooter'
import { ResultsNextStepCard } from './ResultsNextStepCard'
import { ResultsReadysetCard } from './ResultsReadysetCard'
import { ResultsSummaryCard } from './ResultsSummaryCard'
import { selectResultsViewModel } from './resultsSelectors'

export function ResultsSuccess({
  condensed = false,
  results,
  rewriteTesting,
  readysetCacheability,
  target,
  cacheDeployed,
  onCacheQuery,
  onDeployNavigate,
  onSetUpCaching,
  isCaching,
  onAskFollowUp,
  hasExistingChat,
}: {
  results: CompleteEvent
  rewriteTesting?: RewriteTesting
  readysetCacheability?: ReadysetCacheability
  target?: string
  cacheDeployed?: boolean
  onCacheQuery?: () => void
  onDeployNavigate?: () => void
  onSetUpCaching?: () => void
  isCaching?: boolean
  onAskFollowUp?: () => void
  hasExistingChat?: boolean
  condensed?: boolean
}) {
  const view = selectResultsViewModel(
    results,
    rewriteTesting,
    readysetCacheability
  )
  const highlightedIndexSql =
    view.nextStep.kind === 'index' ? view.nextStep.sql : undefined
  const remainingIndexes = highlightedIndexSql
    ? view.indexRecommendations.filter(
        (recommendation) => recommendation.sql !== highlightedIndexSql
      )
    : view.indexRecommendations
  // The top recommendation shown as the next step isn't repeated below.
  const leadOpportunity =
    view.nextStep.kind === 'opportunity' ? view.nextStep.body : undefined
  const remainingOpportunities = (view.additionalRecommendations ?? []).filter(
    (opportunity) => opportunity.description !== leadOpportunity
  )
  // An automatic run skips rewrite testing; that's only worth a word when it suggested one.
  const showTesting =
    view.testing &&
    (view.testing.skipped_reason !== 'background_analysis' ||
      Boolean(results.llm_analysis?.rewrite_suggestions?.length))
  return (
    <m.div
      className="space-y-6"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.3 }}
    >
      <div className="space-y-4">
        <ResultsSummaryCard
          performance={view.performance}
          planOnly={view.planOnly}
          explainResults={view.explainResults}
        />
        {view.cacheability && view.readysetVerdict && !condensed ? (
          <div className="grid items-stretch gap-4 desktop:grid-cols-2">
            <ResultsNextStepCard
              step={view.nextStep}
              onSetUpCaching={onSetUpCaching}
              isCaching={isCaching}
            />
            <ResultsReadysetCard
              verdict={view.readysetVerdict}
              actionIsPrimary={view.nextStep.kind === 'readyset'}
              cacheDeployed={cacheDeployed}
              onCacheQuery={onCacheQuery}
              onDeployNavigate={onDeployNavigate}
              onSetUpCaching={onSetUpCaching}
              isCaching={isCaching}
            />
          </div>
        ) : (
          <ResultsNextStepCard
            step={view.nextStep}
            onSetUpCaching={onSetUpCaching}
            isCaching={isCaching}
          />
        )}
      </div>

      <Details condensed={condensed}>
        {condensed && view.cacheability && view.readysetVerdict ? (
          <ResultsReadysetCard
            verdict={view.readysetVerdict}
            actionIsPrimary={view.nextStep.kind === 'readyset'}
            cacheDeployed={cacheDeployed}
            onCacheQuery={onCacheQuery}
            onDeployNavigate={onDeployNavigate}
            onSetUpCaching={onSetUpCaching}
            isCaching={isCaching}
          />
        ) : null}
        {!view.hasModelAnalysis ? (
          <Alert
            variant="warning"
            modifier="outline"
            icon="info"
            iconPosition="left"
            label={
              view.planOnly
                ? "The analysis model wasn't available. Planner estimates are still shown."
                : "The analysis model wasn't available. Measured execution data is still shown."
            }
          />
        ) : null}

        {showTesting && view.testing ? (
          <TestedOptimizationsSection testing={view.testing} />
        ) : null}

        {remainingIndexes.length > 0 ? (
          <IndexRecommendationsSection
            recommendations={remainingIndexes}
            indexTesting={view.indexTesting}
          />
        ) : null}

        {remainingOpportunities.length > 0 ? (
          <AdditionalRecommendationsSection
            opportunities={remainingOpportunities}
            collapsible
          />
        ) : null}

        <ResultsFooter
          results={results}
          target={target}
          onAskFollowUp={onAskFollowUp}
          hasExistingChat={hasExistingChat}
        />
      </Details>
    </m.div>
  )
}

/** In the query list the score and next step lead; the rest of the analysis waits here. */
function Details({
  condensed,
  children,
}: {
  condensed: boolean
  children: ReactNode
}) {
  if (!condensed) return <>{children}</>
  return (
    <Disclosure
      title="Details"
      subtitle="Readyset fit, tested changes and every finding"
    >
      <div className="space-y-6 pt-2">{children}</div>
    </Disclosure>
  )
}
