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
import type { LoadTestProgress } from '../../../lib/backgroundRuns'

type Metric = 'qps' | 'latency'

const METRICS: Array<SegmentedControlSegment<Metric>> = [
  { value: 'qps', label: 'QPS' },
  { value: 'latency', label: 'p95 latency' },
]

export function loadTestP95(sample: LoadTestProgress | undefined) {
  if (!sample) return 0
  const successful = sample.queries.reduce(
    (total, query) => total + Math.max(0, query.successes),
    0
  )
  if (successful === 0) return 0
  return (
    sample.queries.reduce(
      (total, query) => total + query.p95_ms * Math.max(0, query.successes),
      0
    ) / successful
  )
}

export function LoadTestLiveChart({
  timeline,
  live = false,
}: {
  timeline: LoadTestProgress[]
  live?: boolean
}) {
  const [metric, setMetric] = useState<Metric>('qps')
  const lastSecond = Math.max(
    1,
    ...timeline.map((sample) => sample.elapsed_seconds)
  )
  const series = [
    {
      id: metric,
      label: metric === 'qps' ? 'Achieved QPS' : 'p95 latency',
      color:
        metric === 'qps'
          ? 'var(--content-viz-cache)'
          : 'var(--content-viz-origin)',
      points: timeline.map((sample) => ({
        x: sample.elapsed_seconds,
        y: metric === 'qps' ? sample.qps : loadTestP95(sample),
      })),
    },
  ]
  const maxValue = comparisonChartMax(
    Math.max(1, ...series[0].points.map((point) => point.y))
  )

  return (
    <Card aria-live={live ? 'polite' : undefined}>
      <Card.Header className="items-start gap-4 tablet:flex-row tablet:items-center tablet:justify-between">
        <VStack className="items-start gap-0.5">
          <Card.Title>{live ? 'Live load' : 'Load over time'}</Card.Title>
          <Card.Description>
            {metric === 'qps'
              ? 'Completed requests per second throughout this run.'
              : 'Successful-query tail latency throughout this run.'}
          </Card.Description>
        </VStack>
        <SegmentedControl
          aria-label="Load test chart metric"
          mode="radio"
          size="small"
          value={metric}
          segments={METRICS}
          onValueChange={setMetric}
        />
      </Card.Header>
      <Card.Content>
        <HStack className="mb-2 items-center gap-2">
          <span
            aria-hidden="true"
            className="h-1 w-4 rounded-full bg-surface-positive-solid"
          />
          <Text level="caption" className="text-content-layout-2">
            {series[0].label}
          </Text>
          {live && (
            <Text
              level="caption"
              className="ml-auto text-content-positive-soft"
            >
              Live
            </Text>
          )}
        </HStack>
        <ComparisonLineChart
          series={series}
          ariaLabel={series[0].label}
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
