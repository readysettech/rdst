import { Card } from '@rs/ui-new/card-2'
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

const METRIC_SEGMENTS: Array<SegmentedControlSegment<Metric>> = [
  { value: 'qps', label: 'QPS' },
  { value: 'latency', label: 'p95 latency' },
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

export function CompareLiveChart({
  timeline,
  queryLabel,
  live = false,
  following = false,
}: {
  timeline: CacheCompareSample[]
  /** The one query this curve belongs to; the chart never mixes queries. */
  queryLabel?: string
  live?: boolean
  /** Selection is still tracking whichever query the sandbox is measuring. */
  following?: boolean
}) {
  const [metric, setMetric] = useState<Metric>('qps')
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

  const title = live ? 'Live comparison' : 'Load comparison'

  return (
    <Card>
      <Card.Header className="items-start gap-4 tablet:flex-row tablet:items-center tablet:justify-between">
        <VStack className="items-start gap-0.5">
          <Card.Title>
            {queryLabel ? `${title} · ${queryLabel}` : title}
          </Card.Title>
          <Card.Description>
            {metric === 'qps'
              ? 'Achieved throughput under equal concurrency on both lanes.'
              : 'Tail latency while both lanes receive the same target load.'}
          </Card.Description>
        </VStack>
        <SegmentedControl
          aria-label="Comparison chart metric"
          mode="radio"
          size="small"
          value={metric}
          segments={METRIC_SEGMENTS}
          onValueChange={setMetric}
        />
      </Card.Header>
      <Card.Content>
        <HStack className="mb-2 flex-wrap items-center gap-5">
          <Legend color="var(--content-viz-origin)" label="Upstream" />
          <Legend color="var(--content-viz-cache)" label="Readyset" />
          {live && (
            <Text
              level="caption"
              className="ml-auto text-content-positive-soft"
            >
              {following ? 'Following live' : 'Live'}
            </Text>
          )}
        </HStack>
        <ComparisonLineChart
          series={activeSeries}
          annotations={annotations}
          ariaLabel={
            metric === 'qps'
              ? 'Live upstream and Readyset QPS'
              : 'Live upstream and Readyset p95 latency'
          }
          emptyLabel="Waiting for the first measurement"
          xDomain={[0, lastSecond]}
          yMax={maxValue}
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
      </Card.Content>
    </Card>
  )
}
