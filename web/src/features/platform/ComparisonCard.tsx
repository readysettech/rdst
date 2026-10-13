import { SegmentedControl } from '@rs/ui-new/segmented-control'
import { Text } from '@rs/ui-new/text'
import { useMemo } from 'react'
import {
  ComparisonLineChart,
  comparisonChartMax,
} from '../../components/ComparisonLineChart'
import type { LoadEvent, LoadSample } from '../../lib/useDemo'
import { chartMarkers, shortClock, spanLabel } from './platformModel'

export type ComparisonMetric = 'qps' | 'latency'

const READYSET = 'var(--color-series-1)'
const POSTGRES = 'var(--color-content-layout-3)'
const WINDOW_S = 600

// The chart's viewBox and plot margins, so a marker label can sit over its line.
const VIEW = { width: 920, left: 42, right: 92 }

function markerLeft(fraction: number): string {
  const plot = VIEW.width - VIEW.left - VIEW.right
  return `${((VIEW.left + fraction * plot) / VIEW.width) * 100}%`
}

function Swatch({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span
        aria-hidden="true"
        className="inline-block h-0.5 w-3.5 rounded-xs"
        style={{ background: color }}
      />
      {label}
    </span>
  )
}

export interface ComparisonCardProps {
  samples: LoadSample[]
  events: LoadEvent[]
  metric: ComparisonMetric
  onMetric: (metric: ComparisonMetric) => void
}

/** The two paths side by side: the same traffic through Readyset and straight to Postgres. */
export function ComparisonCard({
  samples,
  events,
  metric,
  onMetric,
}: ComparisonCardProps) {
  const latest = samples.length ? samples[samples.length - 1] : null
  const newestEvent = events.reduce((max, event) => Math.max(max, event.t), 0)
  const now = Math.max(latest?.t ?? 0, newestEvent) || Date.now() / 1000
  const firstT = samples.length ? samples[0].t : now - WINDOW_S
  const start = Math.min(now - 1, Math.max(firstT, now - WINDOW_S))
  const span = Math.max(1, now - start)
  const points = useMemo(
    () => samples.filter((sample) => sample.t >= start - 1),
    [samples, start]
  )
  const value = (sample: LoadSample, path: 'router' | 'direct') =>
    metric === 'qps' ? sample[path].qps : sample[path].p50_ms
  const yMax = comparisonChartMax(
    Math.max(
      metric === 'qps' ? 100 : 1,
      ...points.flatMap((p) => [value(p, 'direct'), value(p, 'router')])
    )
  )
  const markers = chartMarkers(events).filter(
    (marker) => marker.x >= start && marker.x <= now + 2
  )
  const format = (v: number) =>
    metric === 'qps' ? `${Math.round(v)} queries/s` : `${v.toFixed(1)} ms`
  return (
    <section
      aria-label="Comparison"
      data-testid="platform-comparison-card"
      className="mt-7 rounded-card border border-border-layout-2 bg-surface-primary-soft px-6 py-5.5"
    >
      <div className="flex flex-wrap items-center gap-3.5">
        <SegmentedControl
          aria-label="Chart metric"
          mode="radio"
          size="small"
          value={metric}
          segments={[
            { value: 'qps', label: 'Queries/s' },
            { value: 'latency', label: 'Latency' },
          ]}
          onValueChange={onMetric}
        />
        <Text
          as="div"
          level="caption"
          className="ml-auto flex items-center gap-3.5 text-content-layout-3"
        >
          <span className="flex items-center gap-4">
            <Swatch color={READYSET} label="Readyset path" />
            <Swatch color={POSTGRES} label="Postgres direct" />
          </span>
          <span>{spanLabel(span)}</span>
        </Text>
      </div>
      <div className="relative mt-4">
        {markers.map((marker) => (
          <span
            key={`${marker.x}-${marker.label}`}
            data-testid="chart-marker"
            className="absolute top-0 z-10 translate-x-1.5 whitespace-nowrap rounded-sm border border-border-layout-2 bg-surface-layout-2 px-1.5 text-label-extra-small text-content-layout-2"
            style={{ left: markerLeft((marker.x - start) / span) }}
          >
            {marker.label}
          </span>
        ))}
        <ComparisonLineChart
          series={[
            {
              id: 'readyset',
              label: 'Readyset path',
              color: READYSET,
              points: points.map((p) => ({ x: p.t, y: value(p, 'router') })),
            },
            {
              id: 'upstream',
              label: 'Postgres direct',
              color: POSTGRES,
              points: points.map((p) => ({ x: p.t, y: value(p, 'direct') })),
            },
          ]}
          annotations={markers.map((marker) => ({
            x: marker.x,
            label: marker.label,
          }))}
          ariaLabel={
            metric === 'qps'
              ? 'Queries per second on each path'
              : 'Latency on each path'
          }
          emptyLabel="Waiting for traffic"
          xDomain={[start, now]}
          yMax={yMax}
          xStartLabel={shortClock(start)}
          xEndLabel={shortClock(now)}
          formatAxisValue={(v) =>
            metric === 'qps'
              ? v >= 1000
                ? `${Math.round(v / 1000)}k`
                : String(Math.round(v))
              : `${v.toFixed(v >= 10 ? 0 : 1)}`
          }
          formatValue={format}
          formatHoverX={(v) =>
            new Date(v * 1000).toLocaleTimeString([], {
              hour: 'numeric',
              minute: '2-digit',
              second: '2-digit',
            })
          }
        />
      </div>
    </section>
  )
}
