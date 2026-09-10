import { ErrorState } from '@rs/ui-new/error-state'
import { VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { ConnectionFailureActions } from '../../components/ConnectionFailureActions'
import { RoutableNotice } from '../../components/RoutableNotice'
import type { AskErrorEvent } from '../../lib/ask'
import {
  classifyError,
  isConnectionFailure,
  isModelContextLimitError,
  isTrialExhaustedError,
  TRIAL_EXHAUSTED_MESSAGE,
} from '../../lib/errorContract'

interface AskErrorStateProps {
  error: AskErrorEvent
  /** The question that failed, echoed so the retry has a visible subject. */
  question?: string
  target?: string | null
  onRetry: () => void
  /** Back to the composer with the question intact. */
  onRefine: () => void
  onStartTrial: () => void
}

// Phrased to complete "Failed while ...", so the tag reads as one sentence
// rather than a label, a colon and a fragment.
const PHASE_LABELS: Record<string, string> = {
  config: 'reading the configuration',
  schema: 'loading the database schema',
  filter: 'selecting relevant tables',
  clarify: 'clarifying the question',
  generate: 'generating SQL',
  validate: 'validating SQL',
  execute: 'running the query',
}

export function AskErrorState({
  error,
  question,
  target,
  onRetry,
  onRefine,
  onStartTrial,
}: AskErrorStateProps) {
  const envelope = {
    code: error.code || error.category || '',
    category: error.category,
    target: error.target || target || undefined,
    message: error.message,
  }
  const errorClass = classifyError(envelope)
  const modelContextLimit = isModelContextLimitError(envelope)
  const isAuthenticationError =
    !modelContextLimit &&
    (errorClass === 'provider' || errorClass === 'rdst-service')
  const trialExhausted = isTrialExhaustedError(envelope)
  const phaseLabel = error.phase ? PHASE_LABELS[error.phase] : undefined
  const timedOut = (envelope.code || '').toLowerCase() === 'query_timeout'

  const body = (() => {
    if (envelope.target && isConnectionFailure(envelope)) {
      return (
        <VStack className="w-full items-stretch gap-3 rounded-xl border border-border-negative-soft bg-surface-negative-soft/50 p-5">
          <ConnectionFailureActions
            failure={{
              target: envelope.target,
              message: envelope.message,
              category: envelope.category,
              code: envelope.code,
            }}
            onRetry={async () => {
              onRetry()
              return true
            }}
            featureRecovery
          />
          {phaseLabel ? <FailurePhase label={phaseLabel} /> : null}
        </VStack>
      )
    }

    if (isAuthenticationError) {
      return (
        <VStack className="w-full items-start gap-3">
          <RoutableNotice
            kind={trialExhausted ? 'trial-exhausted' : 'key-needed'}
            title="AI service authentication failed"
            message={trialExhausted ? TRIAL_EXHAUSTED_MESSAGE : error.message}
            onRetry={trialExhausted ? onStartTrial : undefined}
            retryLabel={trialExhausted ? 'Sign in to Readyset' : undefined}
            className="w-full"
          />
          {phaseLabel ? <FailurePhase label={phaseLabel} /> : null}
        </VStack>
      )
    }

    const title = timedOut
      ? 'Query took too long'
      : modelContextLimit
        ? 'Database schema is too large'
        : error.phase === 'generate'
          ? "Couldn't generate SQL"
          : 'Request failed'

    return (
      <VStack className="w-full items-start gap-3">
        <ErrorState
          className="w-full"
          errorClass={errorClass}
          icon="alert"
          title={title}
          message={
            timedOut
              ? `${error.message} Try a narrower question or retry the query.`
              : error.message
          }
          action={{ label: 'Edit question', onClick: onRefine, icon: 'add' }}
          // A retry that re-runs the same question only helps where the
          // failure was not the question itself.
          onRetry={modelContextLimit ? undefined : onRetry}
        />
        {phaseLabel ? <FailurePhase label={phaseLabel} /> : null}
      </VStack>
    )
  })()

  return (
    <VStack className="w-full items-start gap-2">
      {/* "Try again" re-runs the question, so the question is on screen -
          visible, and the same line the clarification screen uses (C-27). */}
      {question ? (
        <Text level="body-small" className="text-content-layout-3">
          You asked: <span className="text-content-layout-2">{question}</span>
        </Text>
      ) : null}
      {body}
    </VStack>
  )
}

function FailurePhase({ label }: { label: string }) {
  return (
    <Tag
      variant="negative"
      modifier="ghost"
      size="small"
      label={`Failed while ${label}`}
    />
  )
}
