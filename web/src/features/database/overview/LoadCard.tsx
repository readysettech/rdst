// The chart card under the numbers: the series switch (Queries/s, Latency, Time spent)
// while Queries per second is the selected number, the range menu and the recorded span
// at the right, then the stacked chart on the identity ramp with its event markers.
import { Button } from '@rs/ui-new/button'
import { Dropdown } from '@rs/ui-new/dropdown'
import { SegmentedControl } from '@rs/ui-new/segmented-control'
import { Skeleton } from '@rs/ui-new/skeleton'
import { Text } from '@rs/ui-new/text'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@rs/ui-new/tooltip'
import { useMemo, useState } from 'react'
import {
  type ChartMarkLine,
  type ChartPause,
  formatPercent,
  type LiveRange,
  type LoadMetric,
  type LoadModel,
  MonitorTimeChart,
  OTHER_KEY,
  type RangeOption,
  SERIES_COLOR,
  StackedLoadChart,
  type StackSeries,
  timeShareRows,
} from '@/features/monitor-kit/charts'
import { formatMsWithUnit, formatQps } from '@/features/monitor-kit/format'
import type { MonitorHealth } from '@/lib/monitor/types'
import { type HealthMetricId, RANGE_LABEL } from '../databaseModel'
import { FirstRunNote, type FirstRunNoteProps } from './FirstReadings'

const SEGMENTS: { value: LoadMetric; label: string }[] = [
  { value: 'calls', label: 'Queries/s' },
  { value: 'latency', label: 'Latency' },
  { value: 'time', label: 'Time spent' },
]

const HEALTH_SERIES: Record<
  Exclude<HealthMetricId, 'qps'>,
  {
    label: string
    color: string
    pick: (point: MonitorHealth['series'][number]) => number | null
    format: (value: number) => string
    yMin?: 'auto'
    yCap?: number
  }
> = {
  connections: {
    label: 'Connections',
    color: SERIES_COLOR.connections,
    pick: (point) => point.active_conns,
    format: (value) => `${Math.round(value)} connections`,
  },
  hit: {
    label: 'Cache hit',
    color: SERIES_COLOR.hit,
    pick: (point) => point.cache_hit_ratio,
    format: (value) => `${(value * 100).toFixed(1)}%`,
    yMin: 'auto',
    yCap: 1,
  },
  rollback: {
    label: 'Rollbacks and deadlocks',
    color: SERIES_COLOR.rollback,
    pick: (point) => point.xact_rollback,
    format: (value) => `${Math.round(value)} rollbacks`,
  },
}

function msLabel(value: number) {
  const formatted = formatMsWithUnit(value)
  return `${formatted.value} ${formatted.unit}`
}

export const CHART_HEIGHT = 220

export interface LoadCardProps {
  /** Which number is selected; the chart follows it. */
  selected: HealthMetricId
  metric: LoadMetric
  onMetricChange: (metric: LoadMetric) => void
  range: LiveRange
  ranges: RangeOption[]
  onRangeChange: (range: LiveRange) => void
  /** Stands in for the range menu's label and holds the menu (the first minutes). */
  rangeLabel?: string
  /** Why the range menu is held, in its tooltip. */
  rangeHint?: string
  /** "12 min recorded", "no readings since 9:41". */
  caption: string | null
  model: LoadModel | null
  health: MonitorHealth | undefined
  domain: [number, number]
  markers: ChartMarkLine[]
  pauses?: ChartPause[]
  loading: boolean
  hoverKey: string | null
  onHoverKey: (key: string | null) => void
  focusKey: string | null
  onSelectKey: (key: string) => void
  /** The first hour: the line under the chart says when the rest arrives. */
  firstRun?: FirstRunNoteProps
}

export function LoadCard({
  selected,
  metric,
  onMetricChange,
  range,
  ranges,
  onRangeChange,
  rangeLabel,
  rangeHint,
  caption,
  model,
  health,
  domain,
  markers,
  pauses,
  loading,
  hoverKey,
  onHoverKey,
  focusKey,
  onSelectKey,
  firstRun,
}: LoadCardProps) {
  const [menuOpen, setMenuOpen] = useState(false)
  const series = useMemo<StackSeries[]>(() => {
    if (!model) return []
    if (metric === 'latency')
      return [
        {
          id: 'overall',
          label: 'Mean of all queries',
          color: SERIES_COLOR.throughput,
          values: model.overallLatency,
        },
      ]
    const rows = metric === 'time' ? timeShareRows(model.load) : model.calls
    return model.bands.map((band, index) => ({
      id: band.key,
      label: band.label,
      color: band.color,
      values: rows[index],
    }))
  }, [model, metric])
  const healthSeries = selected === 'qps' ? null : HEALTH_SERIES[selected]
  const title =
    healthSeries?.label ??
    SEGMENTS.find((segment) => segment.value === metric)?.label ??
    ''
  const rangeButton = (
    <Button
      variant="primary"
      modifier="ghost"
      size="small"
      icon="chevron-down"
      iconPosition="right"
      label={rangeLabel ?? RANGE_LABEL[range]}
      disabled={Boolean(rangeLabel)}
      data-testid="range-menu"
    />
  )

  return (
    <section
      aria-label="Chart"
      data-testid="load-card"
      className="mt-6 rounded-card border border-border-layout-2 bg-surface-primary-soft px-6 pt-4.5 pb-4.5"
    >
      <div className="flex flex-wrap items-center gap-x-3.5 gap-y-2">
        {healthSeries ? (
          <Text as="h2" level="headline-5" className="text-content-layout-1">
            {healthSeries.label}
          </Text>
        ) : (
          <SegmentedControl<LoadMetric>
            mode="radio"
            size="small"
            aria-label="Chart series"
            value={metric}
            onValueChange={onMetricChange}
            segments={SEGMENTS}
            className="border-transparent bg-transparent"
          />
        )}
        {metric === 'latency' && !healthSeries && (
          <Text as="span" level="caption" className="text-content-layout-3">
            Mean of all queries
          </Text>
        )}
        <div className="ml-auto flex items-center gap-2.5">
          {/* A held menu's button takes no pointer: its wrapper carries the hint. */}
          {rangeLabel && rangeHint ? (
            <TooltipProvider delayDuration={150}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="inline-flex" data-testid="range-hint">
                    {rangeButton}
                  </span>
                </TooltipTrigger>
                <TooltipContent label={rangeHint} side="bottom" />
              </Tooltip>
            </TooltipProvider>
          ) : (
            <Dropdown open={menuOpen} onOpenChange={setMenuOpen}>
              <Dropdown.Trigger asChild>{rangeButton}</Dropdown.Trigger>
              <Dropdown.Content
                align="end"
                className="min-w-44 rounded-panel bg-surface-primary-soft p-1.5"
              >
                {ranges.map((option) => (
                  <Dropdown.Item
                    key={option.range}
                    label={RANGE_LABEL[option.range as LiveRange]}
                    disabled={!option.enabled}
                    active={option.range === range}
                    className="h-8.5 rounded-md px-2.5 text-body-small"
                    onClick={() => onRangeChange(option.range as LiveRange)}
                  />
                ))}
              </Dropdown.Content>
            </Dropdown>
          )}
          {caption && (
            <Text
              as="span"
              level="caption"
              className="whitespace-nowrap text-content-layout-3"
            >
              {caption}
            </Text>
          )}
        </div>
      </div>
      <div
        className="mt-3.5"
        data-testid={firstRun ? 'first-run-chart' : undefined}
      >
        {healthSeries && health ? (
          <MonitorTimeChart
            ariaLabel={`${healthSeries.label}, ${RANGE_LABEL[range].toLowerCase()}`}
            series={[
              {
                id: selected,
                label: healthSeries.label,
                color: healthSeries.color,
                points: health.series.map((point) => ({
                  x: point.ts,
                  y: healthSeries.pick(point),
                })),
                emphasis: true,
              },
            ]}
            domain={domain}
            resolution={health.res}
            yMin={healthSeries.yMin}
            yCap={healthSeries.yCap}
            formatValue={healthSeries.format}
            markers={health.markers}
            pauses={pauses}
            height={CHART_HEIGHT}
            hideEndLabels
          />
        ) : loading && !model ? (
          <Skeleton className="h-[220px] w-full rounded-panel" />
        ) : (
          <StackedLoadChart
            ariaLabel={`${title}, ${(rangeLabel ?? RANGE_LABEL[range]).toLowerCase()}`}
            xs={model?.xs ?? []}
            series={series}
            mode={metric === 'latency' ? 'lines' : 'stack'}
            domain={domain}
            resolution={model?.res ?? health?.res ?? 60}
            height={CHART_HEIGHT}
            formatValue={
              metric === 'time'
                ? formatPercent
                : metric === 'calls'
                  ? (value) => `${formatQps(value)}/s`
                  : msLabel
            }
            formatAxis={
              metric === 'time' ? (value) => `${Math.round(value)}%` : undefined
            }
            fixedMax={metric === 'time' ? 100 : undefined}
            hideShare={metric !== 'calls'}
            activeId={hoverKey}
            focusId={metric === 'latency' ? null : focusKey}
            onHover={(id) => onHoverKey(id === 'overall' ? null : id)}
            onSelect={(id) =>
              id !== OTHER_KEY && id !== 'overall' && onSelectKey(id)
            }
            markers={markers}
            pauses={pauses}
            totalLabel="All queries"
          />
        )}
        {firstRun && <FirstRunNote {...firstRun} />}
      </div>
    </section>
  )
}
