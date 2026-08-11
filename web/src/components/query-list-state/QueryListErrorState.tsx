import { ErrorState } from '@rs/ui-new/error-state'

interface QueryListErrorStateProps {
  title: string
  message: string
  error: unknown
  trustworthy?: string
  onRetry?: () => void
}

function errorDetail(error: unknown) {
  if (error instanceof Error) return error.message
  if (typeof error === 'string') return error
  return error ? String(error) : undefined
}

export function QueryListErrorState({
  title,
  message,
  error,
  trustworthy,
  onRetry,
}: QueryListErrorStateProps) {
  return (
    <ErrorState
      errorClass="database"
      title={title}
      message={message}
      trustworthy={trustworthy}
      onRetry={onRetry}
      detail={errorDetail(error)}
    />
  )
}
