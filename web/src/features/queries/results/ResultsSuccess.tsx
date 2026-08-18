import { Alert } from '@rs/ui-new/alert'
import { m } from '@rs/ui-new/motion'
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
          explainResults={view.explainResults}
        />
        {view.cacheability && view.readysetVerdict ? (
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

      {!view.hasModelAnalysis ? (
        <Alert
          variant="warning"
          modifier="outline"
          icon="info"
          iconPosition="left"
          label="The analysis model wasn't available. Measured execution data is still shown."
        />
      ) : null}

      {view.testing ? (
        <TestedOptimizationsSection testing={view.testing} />
      ) : null}

      {remainingIndexes.length > 0 ? (
        <IndexRecommendationsSection
          recommendations={remainingIndexes}
          indexTesting={view.indexTesting}
        />
      ) : null}

      {view.additionalRecommendations &&
      view.additionalRecommendations.length > 0 ? (
        <AdditionalRecommendationsSection
          opportunities={view.additionalRecommendations}
          collapsible
        />
      ) : null}

      <ResultsFooter
        results={results}
        target={target}
        onAskFollowUp={onAskFollowUp}
        hasExistingChat={hasExistingChat}
      />
    </m.div>
  )
}
