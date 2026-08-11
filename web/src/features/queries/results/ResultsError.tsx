import { ErrorState } from '@rs/ui-new/error-state'
import { m } from '@rs/ui-new/motion'
import {
  type ApiErrorEnvelope,
  classifyError,
  recoveryFor,
  retryHelps,
} from '../../../lib/errorContract'

export function ResultsError({
  error,
  errorEnvelope,
  onRecover,
  onRetry,
}: {
  error?: string
  errorEnvelope?: ApiErrorEnvelope
  onRecover?: (to: string) => void
  onRetry?: () => void
}) {
  const envelope: ApiErrorEnvelope = errorEnvelope ?? {
    code: 'error',
    message:
      error ||
      'An unknown error occurred while analyzing the query. Try again.',
  }
  const errorClass = classifyError(envelope)
  const invalidSql = envelope.code === 'invalid_sql'
  const title = invalidSql
    ? "We couldn't run this query"
    : "Analysis couldn't complete"

  let action: { label: string; onClick: () => void } | undefined
  if (invalidSql && onRecover) {
    action = {
      label: 'Back to queries',
      onClick: () => onRecover('/queries'),
    }
  } else {
    const recovery = recoveryFor(errorClass)
    if (recovery && onRecover) {
      action = {
        label: recovery.label,
        onClick: () => onRecover(recovery.to),
      }
    }
  }

  return (
    <m.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25 }}
    >
      <ErrorState
        errorClass={errorClass}
        title={title}
        message={envelope.message}
        action={action}
        onRetry={retryHelps(errorClass) && onRetry ? onRetry : undefined}
        detail={envelope.detail}
      />
    </m.div>
  )
}
