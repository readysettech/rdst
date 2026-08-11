import { StatusRipple } from '@rs/ui-new/status'
import { formatTimestamp } from '../../../lib/formatters'
import type { QueryLibraryController } from './useQueryLibraryController'

export function QueryLibraryDiscoveryStatus({
  controller,
}: {
  controller: QueryLibraryController
}) {
  const { discovery } = controller.library
  const status = {
    starting: {
      color: 'informative' as const,
      label: 'Starting discovery',
    },
    watching: {
      color: 'positive' as const,
      label: `Watching ${controller.target ?? 'target'}`,
    },
    unavailable: {
      color: 'warning' as const,
      label: 'Discovery unavailable',
    },
  }[discovery.state]
  const lastUpdated = discovery.updated_at
    ? `Last updated ${formatTimestamp(discovery.updated_at).toLowerCase()}`
    : undefined

  return (
    <span title={lastUpdated} className="inline-flex items-center">
      <StatusRipple color={status.color} label={status.label} />
    </span>
  )
}
