import {
  SegmentedControl,
  type SegmentedControlSegment,
} from '@rs/ui-new/segmented-control'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { useState } from 'react'
import { formatMs } from '../../../components/CacheComparison'
import {
  ComparisonLineChart,
  comparisonChartMax,
} from '../../../components/ComparisonLineChart'
import type { CacheCompareSample } from '../../../types/cache'

type Metric = 'qps' | 'latency'
type Scale = 'log' | 'linear'

const METRIC_SEGMENTS: Array<SegmentedControlSegment<Metric>> = [
  { value: 'qps', label: 'QPS' },
  { value: 'latency', label: 'p95 latency' },
]

const SCALE_SEGMENTS: Array<SegmentedControlSegment<Scale>> = [
  { value: 'log', label: 'Log' },
  { value: 'linear', label: 'Linear' },
]

function Legend({ color, label }: { color: string; label: string }) {
  return (
    <HStack className="items-center gap-2">
      <span
        aria-hidden="true"
        className="inline-block h-1 w-4 rounded-full"
        style={{ background: color }}
      />
      <Text level="caption" className="text-content-layout-2">
        {label}
      </Text>
    </HStack>
  )
}

/**
 * One query's own curve, drawn inside that query's card. A cached lane can run
 * two orders of magnitude ahead of the origin, which flattens the origin line
 * onto the baseline on a linear axis, so the axis starts logarithmic and says
 * so beside the legend.
 */
export function CompareLiveChart({
  timeline,
  queryLabel,
  live = false,
}: {
  timeline: CacheCompareSample[]
  /** The one query this curve belongs to; the chart never mixes queries. */
  queryLabel: string
  live?: boolean
}) {
  const [metric, setMetric] = useState<Metric>('qps')
  const [scale, setScale] = useState<Scale>('log')
  const lastSecond = Math.max(
    1,
    ...timeline.map((sample) => sample.elapsed_seconds)
  )
  const annotations = timeline.flatMap((sample, index) => {
    const previous = timeline[index - 1]
    return !previous || previous.concurrency !== sample.concurrency
      ? [
          {
            x: sample.elapsed_seconds,
            label: `Load changed to ${sample.concurrency} concurrent clients`,
          },
        ]
      : []
  })
  const qpsSeries = [
    {
      id: 'upstream',
      label: 'Upstream QPS',
      color: 'var(--content-viz-origin)',
      points: timeline.map((sample) => ({
        x: sample.elapsed_seconds,
        y: sample.origin.throughput_rps,
      })),
    },
    {
      id: 'readyset',
      label: 'Readyset QPS',
      color: 'var(--content-viz-cache)',
      points: timeline.map((sample) => ({
        x: sample.elapsed_seconds,
        y: sample.readyset.throughput_rps,
      })),
    },
  ]
  const latencySeries = [
    {
      id: 'upstream',
      label: 'Upstream p95',
      color: 'var(--content-viz-origin)',
      points: timeline.map((sample) => ({
        x: sample.elapsed_seconds,
        y: sample.origin.p95_ms,
      })),
    },
    {
      id: 'readyset',
      label: 'Readyset p95',
      color: 'var(--content-viz-cache)',
      points: timeline.map((sample) => ({
        x: sample.elapsed_seconds,
        y: sample.readyset.p95_ms,
      })),
    },
  ]
  const activeSeries = metric === 'qps' ? qpsSeries : latencySeries
  const maxValue = comparisonChartMax(
    Math.max(
      1,
      ...activeSeries.flatMap((series) => series.points.map((point) => point.y))
    )
  )
  const unit = metric === 'qps' ? 'QPS' : 'p95 latency'
  const axisLabel =
    scale === 'log' ? `${unit} · log scale` : `${unit} · linear scale`

  return (
    <VStack className="min-w-0 items-stretch gap-3">
      <HStack className="flex-wrap items-center justify-between gap-3">
        <SegmentedControl
          aria-label={`Chart metric for ${queryLabel}`}
          mode="radio"
          size="small"
          value={metric}
          segments={METRIC_SEGMENTS}
          onValueChange={setMetric}
        />
        <SegmentedControl
          aria-label={`Chart scale for ${queryLabel}`}
          mode="radio"
          size="small"
          value={scale}
          segments={SCALE_SEGMENTS}
          onValueChange={setScale}
        />
      </HStack>
      <HStack className="flex-wrap items-center gap-x-5 gap-y-1">
        <Text level="caption" className="text-content-layout-3">
          {axisLabel}
        </Text>
        <Legend color="var(--content-viz-origin)" label="Upstream" />
        <Legend color="var(--content-viz-cache)" label="Readyset" />
        {live && (
          <Text level="caption" className="ml-auto text-content-positive-soft">
            Live
          </Text>
        )}
      </HStack>
      <ComparisonLineChart
        series={activeSeries}
        annotations={annotations}
        ariaLabel={`${queryLabel} upstream and Readyset ${
          metric === 'qps' ? 'QPS' : 'p95 latency'
        }, ${scale === 'log' ? 'logarithmic' : 'linear'} scale`}
        emptyLabel="Waiting for the first measurement"
        xDomain={[0, lastSecond]}
        yMax={maxValue}
        yScale={scale === 'log' ? 'log' : 'linear'}
        xStartLabel="0s"
        xEndLabel={`${Math.ceil(lastSecond)}s`}
        formatAxisValue={
          metric === 'qps' ? (value) => `${Math.round(value)}` : formatMs
        }
        formatValue={
          metric === 'qps'
            ? (value) => `${value.toFixed(value >= 10 ? 0 : 1)} QPS`
            : formatMs
        }
        formatHoverX={(value) => `${value.toFixed(1)}s`}
      />
    </VStack>
  )
}
