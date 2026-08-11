import { Button } from '@rs/ui-new/button'
import { Icon } from '@rs/ui-new/icon'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { ConnectionFailureActions } from '../../components/ConnectionFailureActions'
import { RoutableNotice } from '../../components/RoutableNotice'
import type { AskErrorEvent } from '../../lib/ask'
import {
  classifyError,
  isConnectionFailure,
  isTrialExhaustedError,
  TRIAL_EXHAUSTED_MESSAGE,
} from '../../lib/errorContract'

interface AskErrorStateProps {
  error: AskErrorEvent
  target?: string | null
  onRetry: () => void
  onNewQuestion: () => void
  onStartTrial: () => void
}

const PHASE_LABELS: Record<string, string> = {
  config: 'Configuration',
  schema: 'Loading database schema',
  filter: 'Selecting relevant tables',
  clarify: 'Clarifying the question',
  generate: 'Generating SQL',
  validate: 'Validating SQL',
  execute: 'Running the query',
}

export function AskErrorState({
  error,
  target,
  onRetry,
  onNewQuestion,
  onStartTrial,
}: AskErrorStateProps) {
  const envelope = {
    code: error.code || error.category || '',
    category: error.category,
    target: error.target || target || undefined,
    message: error.message,
  }
  const errorClass = classifyError(envelope)
  const isAuthenticationError =
    errorClass === 'provider' || errorClass === 'rdst-service'
  const trialExhausted = isTrialExhaustedError(error.message)
  const phaseLabel = error.phase ? PHASE_LABELS[error.phase] : undefined

  if (envelope.target && isConnectionFailure(envelope)) {
    return (
      <VStack className="gap-3 items-stretch rounded-xl border border-border-negative-soft bg-surface-negative-soft/20 p-5">
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
        {phaseLabel && <FailurePhase label={phaseLabel} />}
      </VStack>
    )
  }

  if (isAuthenticationError) {
    return (
      <VStack className="gap-3 items-start">
        <RoutableNotice
          kind={trialExhausted ? 'trial-exhausted' : 'key-needed'}
          title="AI service authentication failed"
          message={trialExhausted ? TRIAL_EXHAUSTED_MESSAGE : error.message}
          onRetry={trialExhausted ? onStartTrial : undefined}
          retryLabel={trialExhausted ? 'Start trial' : undefined}
          className="w-full"
        />
        {phaseLabel && <FailurePhase label={phaseLabel} />}
      </VStack>
    )
  }

  const title =
    error.phase === 'generate' ? "Couldn't generate SQL" : 'Request failed'

  return (
    <div className="rounded-xl border border-border-negative-soft bg-surface-negative-soft/50 p-6">
      <HStack className="gap-4 items-start">
        <div className="flex size-12 shrink-0 items-center justify-center rounded-xl bg-surface-negative-soft">
          <Icon
            name="alert"
            label="Error"
            className="size-6 text-content-negative-soft"
          />
        </div>
        <VStack className="gap-3 items-start flex-1">
          <VStack className="gap-1 items-start">
            <Text level="headline-4" className="text-content-negative-soft">
              {title}
            </Text>
            <Text
              level="body-small"
              className="text-content-layout-2 leading-relaxed"
            >
              {error.message}
            </Text>
          </VStack>
          {phaseLabel && <FailurePhase label={phaseLabel} />}
          <HStack className="gap-3 items-center">
            <Button
              onClick={onRetry}
              variant="primary"
              modifier="outline"
              size="small"
              label="Try again"
              icon="arrow-left"
              iconPosition="left"
            />
            <Button
              onClick={onNewQuestion}
              variant="primary"
              modifier="ghost"
              size="small"
              label="Ask another"
              icon="add"
              iconPosition="left"
            />
          </HStack>
        </VStack>
      </HStack>
    </div>
  )
}

function FailurePhase({ label }: { label: string }) {
  return (
    <Tag
      variant="negative"
      modifier="ghost"
      size="small"
      label={`Failed while: ${label}`}
    />
  )
}
