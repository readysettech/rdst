import { ErrorState } from '@rs/ui-new/error-state'
import type {
  AnalysisState,
  CompleteEvent,
  ProgressEvent,
  ReadysetCacheability,
  RewriteTesting,
} from '../../../lib/api'
import type { ApiErrorEnvelope } from '../../../lib/errorContract'
import { ResultsError } from './ResultsError'
import { ResultsLoading } from './ResultsLoading'
import { ResultsSuccess } from './ResultsSuccess'

export interface AnalysisResultsProps {
  state: AnalysisState
  progress?: ProgressEvent
  results?: CompleteEvent
  rewriteTesting?: RewriteTesting
  readysetCacheability?: ReadysetCacheability
  error?: string
  errorEnvelope?: ApiErrorEnvelope
  target?: string
  cacheDeployed?: boolean
  onCacheQuery?: () => void
  onDeployNavigate?: () => void
  onSetUpCaching?: () => void
  onRecover?: (to: string) => void
  onRetry?: () => void
  isCaching?: boolean
  onAskFollowUp?: () => void
  hasExistingChat?: boolean
  /**
   * A stored-analysis link whose id no longer resolves — an evicted record or
   * a URL from another install. Distinct from a stored record kept without its
   * body, which the viewer explains in place.
   */
  storedAnalysisMissing?: boolean
  /** The read failure behind `storedAnalysisMissing`, kept behind an expander. */
  storedAnalysisDetail?: string
  /** Route back to the query list; the only useful exit from a dead link. */
  onBrowseQueries?: () => void
}

export function AnalysisResults({
  state,
  progress,
  results,
  rewriteTesting,
  readysetCacheability,
  error,
  errorEnvelope,
  target,
  cacheDeployed,
  onCacheQuery,
  onDeployNavigate,
  onSetUpCaching,
  onRecover,
  onRetry,
  isCaching,
  onAskFollowUp,
  hasExistingChat,
  storedAnalysisMissing,
  storedAnalysisDetail,
  onBrowseQueries,
}: AnalysisResultsProps) {
  if (storedAnalysisMissing) {
    return (
      <ErrorState
        errorClass="valid-negative"
        title="This analysis is no longer available"
        message="The link points at a saved analysis this install no longer has. Older analyses are evicted as newer ones are kept."
        trustworthy="Your saved queries and their newer analyses are untouched."
        detail={storedAnalysisDetail}
        action={
          onBrowseQueries
            ? { label: 'Back to queries', onClick: onBrowseQueries }
            : undefined
        }
      />
    )
  }

  if (state === 'analyzing') {
    return <ResultsLoading progress={progress} />
  }

  if (state === 'error') {
    return (
      <ResultsError
        error={error}
        errorEnvelope={errorEnvelope}
        onRecover={onRecover}
        onRetry={onRetry}
      />
    )
  }

  if (state === 'complete' && results) {
    return (
      <ResultsSuccess
        results={results}
        rewriteTesting={rewriteTesting}
        readysetCacheability={readysetCacheability}
        target={target}
        cacheDeployed={cacheDeployed}
        onCacheQuery={onCacheQuery}
        onDeployNavigate={onDeployNavigate}
        onSetUpCaching={onSetUpCaching}
        isCaching={isCaching}
        onAskFollowUp={onAskFollowUp}
        hasExistingChat={hasExistingChat}
      />
    )
  }

  return null
}
