// The one way RDST says monitoring can't start yet: a warning banner with what's missing and
// why it matters. The connection form's test and the turn-on dialog both show it.
import { cn } from '@rs/tailwind-base'
import { Icon } from '@rs/ui-new/icon'
import { Text } from '@rs/ui-new/text'

export function StatsBlocked({
  statsOff = true,
  source,
  className,
}: {
  /** Query statistics themselves are missing; false for any other blocking check. */
  statsOff?: boolean
  /** What RDST reads them from, such as `pg_stat_statements`. */
  source?: string | null
  className?: string
}) {
  return (
    <div
      role="alert"
      data-testid="monitoring-blocked"
      className={cn(
        'flex items-start gap-3 rounded-panel border border-border-warning-soft bg-surface-warning-soft px-3.5 py-3',
        className
      )}
    >
      <Icon
        name="alert"
        label=""
        size="small"
        className="mt-0.5 shrink-0 text-content-warning-plain"
      />
      <div className="min-w-0">
        <Text as="p" level="label-medium" className="text-content-layout-1">
          {statsOff
            ? "Query stats are off, so monitoring can't start"
            : "Monitoring can't start until this is fixed"}
        </Text>
        <Text as="p" level="caption" className="mt-0.5 text-content-layout-2">
          {statsOff
            ? `RDST reads ${source || 'query statistics'} to see your queries.`
            : 'Make the change below, then check again.'}
        </Text>
      </div>
    </div>
  )
}
