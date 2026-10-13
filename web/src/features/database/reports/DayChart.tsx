// The day's chart card in the report: the series switch (Queries/s, Latency, Time
// spent), the spike word with the link to the query list, and the stacked chart on the
// identity ramp over the day's 24 hours, drawn from the numbers frozen into the report.
// The Detailed view marks its spikes or busiest stretches on the chart.
import { SegmentedControl } from '@rs/ui-new/segmented-control'
import { Text } from '@rs/ui-new/text'
import { Link } from '@tanstack/react-router'
import { useMemo, useState } from 'react'
import {
  type ChartMarkLine,
  formatPercent,
  type LoadMetric,
  SERIES_COLOR,
  StackedLoadChart,
  type StackSeries,
  timeShareRows,
} from '@/features/monitor-kit/charts'
import {
  formatClock,
  formatMsWithUnit,
  formatQps,
} from '@/features/monitor-kit/format'
import type {
  DetailedStretch,
  SnapshotChart,
  SnapshotSpike,
} from '@/lib/monitor/types'
import { bandColor, stretchCaption } from './reportModel'

const HEIGHT = 200

const SEGMENTS: { value: LoadMetric; label: string }[] = [
  { value: 'calls', label: 'Queries/s' },
  { value: 'latency', label: 'Latency' },
  { value: 'time', label: 'Time spent' },
]

const hairlineLink =
  'border-b border-border-layout-2 hover:border-content-layout-2'

function msLabel(value: number) {
  const formatted = formatMsWithUnit(value)
  return `${formatted.value} ${formatted.unit}`
}

/** The chart's series for one metric; latency is the one line over every query. */
export function chartSeries(
  chart: SnapshotChart,
  metric: LoadMetric
): StackSeries[] {
  if (metric === 'latency')
    return [
      {
        id: 'overall',
        label: 'Mean of all queries',
        color: SERIES_COLOR.throughput,
        values: chart.overall_mean_ms ?? chart.xs.map(() => null),
      },
    ]
  const rows =
    metric === 'time'
      ? timeShareRows(
          chart.ms ?? chart.bands.map(() => chart.xs.map(() => null))
        )
      : chart.qps
  return chart.bands.map((band, index) => ({
    id: band.key,
    label: band.display_name,
    color: bandColor(band.kind === 'other' ? null : band.color_index),
    values: rows[index] ?? chart.xs.map(() => null),
  }))
}

/** The day's start, or the bucket readings began in when that is later. */
export function chartStart(
  chart: Pick<SnapshotChart, 'start' | 'end' | 'res'>,
  observedFrom?: number | null
): number {
  if (observedFrom == null) return chart.start
  const bucket = Math.floor(observedFrom / chart.res) * chart.res
  return bucket > chart.start && bucket < chart.end ? bucket : chart.start
}

export function DayChart({
  chart,
  spikes,
  queryCount,
  target,
  stretches,
  observedFrom,
}: {
  chart: SnapshotChart
  spikes: SnapshotSpike[]
  queryCount: number
  target: string
  /** Detailed: the spikes or busiest stretches to mark. */
  stretches?: DetailedStretch[]
  /** When readings began; a day that started partway is drawn from there. */
  observedFrom?: number | null
}) {
  const [metric, setMetric] = useState<LoadMetric>('calls')
  const series = useMemo(() => chartSeries(chart, metric), [chart, metric])
  const markers = useMemo<ChartMarkLine[]>(
    () => [
      ...chart.markers.map((marker) => ({ x: marker.ts, label: marker.label })),
      ...(stretches ?? []).map((stretch) => ({
        x: stretch.start,
        label: formatClock(stretch.start),
      })),
    ],
    [chart.markers, stretches]
  )
  const spikeWord = stretches
    ? stretchCaption(stretches)
    : spikes.length === 0
      ? 'No spikes.'
      : `${spikes.length} ${spikes.length === 1 ? 'spike' : 'spikes'}.`
  return (
    <section
      aria-label="Chart"
      data-testid="day-chart"
      data-view={metric}
      className="mt-2 rounded-card border border-border-layout-2 bg-surface-primary-soft px-6 pt-4.5 pb-4.5"
    >
      <div className="flex items-center gap-3.5">
        <SegmentedControl<LoadMetric>
          mode="radio"
          size="small"
          aria-label="Chart series"
          value={metric}
          onValueChange={setMetric}
          segments={SEGMENTS}
          className="border-transparent bg-transparent"
        />
        <Text
          as="span"
          level="caption"
          className="ml-auto text-content-layout-3"
        >
          {spikeWord}{' '}
          <Link
            to="/database"
            search={{ target, view: 'live', range: '24h' } as never}
            className={hairlineLink}
          >
            All {queryCount} queries
          </Link>
        </Text>
      </div>
      <div className="mt-3.5">
        <StackedLoadChart
          ariaLabel={`${SEGMENTS.find((segment) => segment.value === metric)?.label} over the day`}
          xs={chart.xs}
          series={series}
          mode={metric === 'latency' ? 'lines' : 'stack'}
          domain={[chartStart(chart, observedFrom), chart.end]}
          resolution={chart.res}
          height={HEIGHT}
          formatValue={
            metric === 'time'
              ? formatPercent
              : metric === 'calls'
                ? (value) => `${formatQps(value)}/s`
                : msLabel
          }
          fixedMax={metric === 'time' ? 100 : undefined}
          hideShare={metric !== 'calls'}
          markers={markers}
          baselineOnly
          totalLabel="All queries"
          emptyLabel="No readings this day"
        />
      </div>
    </section>
  )
}
