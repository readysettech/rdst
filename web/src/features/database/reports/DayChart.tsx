// The day's chart in a snapshot (DESIGN_V5 section 5, FEEDBACK_ROUND3 B3): drawn from the
// numbers frozen into the report, so a past day never drifts. It shows queries per second
// stacked by query, each query's latency, or its share of database time, or one
// database-wide reading picked from the day in numbers. It reuses the monitor chart kit as
// is; event markers sit in a strip under the plot on the same time axis. Picking a query
// opens it in the report; dragging across a stretch opens the Database page there.
import { cn } from '@rs/tailwind-base'
import { useNavigate } from '@tanstack/react-router'
import { useMemo } from 'react'
import {
  type ChartHighlight,
  MonitorTimeChart,
  SERIES_COLOR,
  StackedLoadChart,
  type StackSeries,
} from '@/features/monitor-kit/charts'
import { useElementWidth } from '@/features/monitor-kit/charts/chartHooks'
import {
  formatClock,
  formatMsWithUnit,
  formatQps,
} from '@/features/monitor-kit/format'
import type {
  SnapshotChart,
  SnapshotMarker,
  SnapshotVitals,
} from '@/lib/monitor/types'
import {
  CHIP_COLORS,
  type ChartView,
  databaseHref,
  METRIC_TITLE,
  metricBands,
  OTHER_CHIP_COLOR,
  vitalPoints,
} from './snapshotModel'

const HEIGHT = 260
const RIGHT = 14

function plotLeft(width: number) {
  return width < 420 ? 40 : 52
}

const MARKER_TONE: Record<string, string> = {
  negative: 'bg-content-negative-plain',
  warning: 'bg-content-warning-plain',
  positive: 'bg-content-positive-plain',
}

const VITAL_COLOR: Record<string, string> = {
  active_conns: SERIES_COLOR.connections,
  cache_hit_ratio: SERIES_COLOR.hit,
  xact_rollback: SERIES_COLOR.rollback,
  deadlocks: SERIES_COLOR.rollback,
}

function bandColor(chart: SnapshotChart, key: string): string {
  const band = chart.bands.find((item) => item.key === key)
  return !band || band.kind === 'other' || band.color_index == null
    ? OTHER_CHIP_COLOR
    : CHIP_COLORS[band.color_index % CHIP_COLORS.length]
}

export function chartSeries(
  chart: SnapshotChart,
  view: ChartView = 'calls'
): StackSeries[] {
  const metric = view === 'latency' || view === 'time' ? view : 'calls'
  return metricBands(chart, metric).map((band) => ({
    id: band.id,
    label:
      band.id === 'overall'
        ? 'Every query'
        : (chart.bands.find((item) => item.key === band.id)?.display_name ??
          band.id),
    color:
      band.id === 'overall' ? SERIES_COLOR.neutral : bandColor(chart, band.id),
    values: band.values,
    reference: band.reference,
  }))
}

function msLabel(value: number) {
  const formatted = formatMsWithUnit(value)
  return `${formatted.value} ${formatted.unit}`
}

function axisNumber(value: number): string {
  if (value === 0) return '0'
  if (value >= 1000) return `${Math.round(value / 100) / 10}k`
  if (value >= 10) return String(Math.round(value))
  if (value >= 1) return String(Math.round(value * 10) / 10)
  return String(Math.round(value * 100) / 100)
}

function hrefParts(href: string) {
  const url = new URL(href, 'http://local')
  return Object.fromEntries(url.searchParams.entries())
}

function MarkerStrip({
  markers,
  domain,
  width,
}: {
  markers: SnapshotMarker[]
  domain: [number, number]
  width: number
}) {
  if (!markers.length || width <= 0) return null
  const left = plotLeft(width)
  const plot = Math.max(1, width - left - RIGHT)
  const span = Math.max(1, domain[1] - domain[0])
  return (
    <div className="relative h-5" aria-hidden="true">
      <div
        className="absolute top-1/2 h-px bg-border-layout-1"
        style={{ left, width: plot }}
      />
      {markers.map((marker, index) => {
        const x = left + ((marker.ts - domain[0]) / span) * plot
        return (
          <span
            key={`${marker.ts}-${index}`}
            title={`${formatClock(marker.ts)}: ${marker.label}`}
            className={cn(
              'absolute top-1/2 h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 border-2 border-surface-layout-1',
              marker.kind === 'index' ? 'rotate-45 rounded-sm' : 'rounded-full',
              MARKER_TONE[marker.tone ?? ''] ?? 'bg-content-layout-3'
            )}
            style={{ left: x }}
          />
        )
      })}
    </div>
  )
}

export function DayChart({
  chart,
  vitals,
  target,
  domain,
  view = 'calls',
  highlight = null,
  hoverKey,
  onHoverKey,
  onSelectKey,
}: {
  chart: SnapshotChart
  vitals?: SnapshotVitals | null
  target: string
  domain: [number, number]
  view?: ChartView
  highlight?: ChartHighlight | null
  hoverKey?: string | null
  onHoverKey?: (key: string | null) => void
  onSelectKey?: (key: string) => void
}) {
  const navigate = useNavigate()
  const [ref, width] = useElementWidth<HTMLDivElement>()
  const series = useMemo(() => chartSeries(chart, view), [chart, view])
  const markers = useMemo(
    () =>
      chart.markers.filter(
        (marker) => marker.ts >= domain[0] && marker.ts <= domain[1]
      ),
    [chart.markers, domain]
  )
  const openWindow = (window: [number, number]) => {
    const search = hrefParts(
      databaseHref({ target, period: { start: window[0], end: window[1] } })
    )
    void navigate({ to: '/database' as never, search: search as never })
  }
  const vital =
    view === 'calls' || view === 'latency' || view === 'time' ? null : view
  return (
    <div
      ref={ref}
      className="space-y-1"
      data-testid="day-chart"
      data-view={view}
    >
      <div key={view} className="mk-fade">
        {vital ? (
          <MonitorTimeChart
            ariaLabel={`${METRIC_TITLE[vital]} over the day`}
            series={[
              {
                id: vital,
                label: METRIC_TITLE[vital],
                color: VITAL_COLOR[vital],
                points: vitalPoints(vitals, vital),
                emphasis: true,
                unit: vital === 'cache_hit_ratio' ? '%' : undefined,
              },
            ]}
            domain={domain}
            resolution={vitals?.res}
            height={HEIGHT}
            yMin={vital === 'cache_hit_ratio' ? 'auto' : 0}
            yCap={vital === 'cache_hit_ratio' ? 100 : undefined}
            formatValue={(value) =>
              vital === 'cache_hit_ratio'
                ? `${value.toFixed(1)}%`
                : String(Math.round(value))
            }
            highlight={highlight}
            onZoom={openWindow}
            emptyLabel="No readings this day"
            hideEndLabels
          />
        ) : (
          <StackedLoadChart
            ariaLabel={`${METRIC_TITLE[view]} by query over the day`}
            xs={chart.xs}
            series={series}
            mode={view === 'latency' ? 'lines' : 'stack'}
            domain={domain}
            resolution={chart.res}
            height={HEIGHT}
            formatValue={
              view === 'time'
                ? (value) => `${Math.round(value)}%`
                : view === 'latency'
                  ? msLabel
                  : (value) => `${formatQps(value)}/s`
            }
            formatAxis={axisNumber}
            fixedMax={view === 'time' ? 100 : undefined}
            hideShare={view === 'time'}
            axisUnit={(yMax) =>
              view === 'time'
                ? { unit: '%', divisor: 1 }
                : view === 'latency'
                  ? yMax >= 2000
                    ? { unit: 's', divisor: 1000 }
                    : { unit: 'ms', divisor: 1 }
                  : { unit: '/s', divisor: 1 }
            }
            activeId={hoverKey ?? null}
            highlight={highlight}
            onHover={(key) => onHoverKey?.(key === 'overall' ? null : key)}
            onSelect={(key) => {
              if (key === 'other' || key === 'overall') return
              onSelectKey?.(key)
            }}
            onZoom={openWindow}
            totalLabel="All queries"
            emptyLabel="No readings this day"
          />
        )}
      </div>
      {/* The strip lines up with the stacked chart's plot. */}
      {vital ? null : (
        <MarkerStrip markers={markers} domain={domain} width={width} />
      )}
    </div>
  )
}
