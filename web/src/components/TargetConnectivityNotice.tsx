import { InlineNotice } from '@rs/ui-new/error-state'
import { useNavigate } from '@tanstack/react-router'
import type { TargetConnectivityFailure } from '../lib/useTargetConnectivityGate'

export function TargetConnectivityNotice({
  target,
  failure,
  isChecking,
  onRetry,
  retryLabel = 'Check again',
}: {
  target?: string | null
  failure: TargetConnectivityFailure | null
  isChecking: boolean
  onRetry: () => void
  retryLabel?: string
}) {
  const navigate = useNavigate()

  if (isChecking) {
    return (
      <InlineNotice
        errorClass="database"
        accent="info"
        title="Checking database connection"
        message={`Verifying that ${target || 'the selected database'} is reachable before starting.`}
      />
    )
  }

  if (!failure) return null

  return (
    <InlineNotice
      errorClass="database"
      title={`Can't reach ${failure.target}`}
      message="RDST stopped before starting this request. Check the database or its connection settings, then try again."
      action={{
        label: 'Open connection settings',
        icon: 'database-settings',
        onClick: () => {
          const returnTo = `${window.location.pathname}${window.location.search}`
          navigate({
            to: '/configure',
            search: { edit: target || undefined, returnTo },
          })
        },
      }}
      onRetry={onRetry}
      retryLabel={retryLabel}
      detail={failure.message || undefined}
    />
  )
}
