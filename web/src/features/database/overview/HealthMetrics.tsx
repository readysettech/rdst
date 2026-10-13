// The four numbers above the chart, divided by hairlines; the selected one drives the
// chart. Queries per second is the hero and steps down one size while the pane is open.
import { cn } from '@rs/tailwind-base'
import { Metric, MetricRow } from '@rs/ui-new/metric-row'
import { Text } from '@rs/ui-new/text'
import {
  DASH,
  type HealthMetric,
  type HealthMetricId,
  readingClock,
} from '../databaseModel'

export interface HealthMetricsProps {
  metrics: HealthMetric[]
  selected: HealthMetricId
  onSelect: (id: HealthMetricId) => void
  /** The pane is open: the hero number steps down a size. */
  compact?: boolean
  /** No readings are arriving: the numbers keep their last values in the third tone. */
  staleSince?: number | null
}

export function HealthMetrics({
  metrics,
  selected,
  onSelect,
  compact = false,
  staleSince = null,
}: HealthMetricsProps) {
  const stale = staleSince != null
  return (
    <section aria-label="Database health" data-testid="health-metrics">
      <MetricRow>
        {metrics.map((metric) => (
          <Metric
            key={metric.id}
            size={metric.id === 'qps' ? (compact ? 'large' : 'hero') : 'base'}
            label={metric.label}
            value={
              metric.value === DASH ? (
                <span className="text-content-layout-3">{DASH}</span>
              ) : (
                metric.value
              )
            }
            unit={metric.unit}
            detail={stale ? (metric.reason ?? metric.detail) : metric.detail}
            reason={stale ? undefined : metric.reason}
            tone={stale ? 'neutral' : metric.tone}
            selected={metric.id === selected}
            onSelect={stale ? undefined : () => onSelect(metric.id)}
            className={cn(stale && '[&>p]:text-content-layout-3')}
          />
        ))}
      </MetricRow>
      {stale && (
        <Text as="p" level="caption" className="mt-2 text-content-layout-3">
          last reading {readingClock(staleSince)}
        </Text>
      )}
    </section>
  )
}
