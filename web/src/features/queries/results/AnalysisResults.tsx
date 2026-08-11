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
}: AnalysisResultsProps) {
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
