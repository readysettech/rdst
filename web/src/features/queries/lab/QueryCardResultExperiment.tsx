import { cn } from '@rs/tailwind-base'
import { Card } from '@rs/ui-new/card-2'
import { Icon } from '@rs/ui-new/icon'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import {
  ComparisonProfile,
  formatMs,
} from '../../../components/CacheComparison'
import { QueryCard, type QueryCardProps } from '../../../components/QueryCard'
import { QueryCardFooter } from '../../../components/query-card/QueryCardFooter'
import { QueryCardHeader } from '../../../components/query-card/QueryCardHeader'
import { QueryCardSql } from '../../../components/query-card/QueryCardSql'
import type { CacheRunResult } from '../../../types/cache'
import { summarizeQuickComparison } from '../../caching/comparisonMetrics'

function resultForQuery(query: string): CacheRunResult {
  return {
    success: true,
    query,
    iterations: 15,
    origin_stats: {
      mean: 15.6,
      median: 15.2,
      min: 14.2,
      max: 19.4,
      p50: 15.2,
      p95: 17.6,
      p99: 18.9,
    },
    cache_stats: {
      mean: 0.17,
      median: 0.16,
      min: 0.12,
      max: 0.32,
      p50: 0.16,
      p95: 0.21,
      p99: 0.28,
    },
    speedup_mean: 92.5,
    speedup_median: 95,
    improvement_pct: 9150,
    winner: 'readyset',
  }
}

type QuickComparison = ReturnType<typeof summarizeQuickComparison>

function speedupLabel(upstream: number, readyset: number) {
  if (readyset <= 0) return '—'
  return `${(upstream / readyset).toFixed(1)}×`
}

function ResultStatus({ label = 'Test complete' }: { label?: string }) {
  return (
    <HStack className="items-center gap-2">
      <Icon
        name="tick"
        label=""
        aria-hidden="true"
        className="h-4 w-4 text-content-positive-soft"
      />
      <Text level="label-small" className="text-content-positive-soft">
        {label}
      </Text>
    </HStack>
  )
}

function ResultHeading({
  comparison,
  eyebrow,
}: {
  comparison: QuickComparison
  eyebrow: string
}) {
  return (
    <HStack className="items-start justify-between gap-4 flex-wrap">
      <VStack className="items-start gap-1">
        <Text level="caption" className="text-content-layout-3">
          {eyebrow}
        </Text>
        <HStack className="items-baseline gap-2 flex-wrap">
          <Text
            level="headline-3"
            className="text-content-positive-soft tabular-nums"
          >
            {comparison.speedup.toFixed(1)}×
          </Text>
          <Text level="label-medium" className="text-content-layout-1">
            faster with Readyset
          </Text>
        </HStack>
      </VStack>
      <ResultStatus />
    </HStack>
  )
}

function ResultMeta({ comparison }: { comparison: QuickComparison }) {
  return (
    <HStack className="items-center justify-between gap-3 border-t border-border-layout-1 pt-4 flex-wrap">
      <Text level="caption" className="text-content-layout-3">
        {comparison.upstream.completed} samples per lane
      </Text>
      <HStack className="items-center gap-4 flex-wrap">
        <Text level="caption" className="text-content-layout-3">
          Target demo
        </Text>
        <Text level="caption" className="text-content-layout-3">
          Updated just now
        </Text>
      </HStack>
    </HStack>
  )
}

function PairedLanesResult({ result }: { result: CacheRunResult }) {
  const comparison = summarizeQuickComparison(result)

  return (
    <VStack className="items-stretch gap-5 py-1">
      <ResultHeading comparison={comparison} eyebrow="Paired latency test" />
      <ComparisonProfile result={result} />
    </VStack>
  )
}

function BeforeAfterValue({
  label,
  value,
  positive = false,
}: {
  label: string
  value: string
  positive?: boolean
}) {
  return (
    <VStack className="min-w-0 items-start gap-2">
      <Text level="caption" className="text-content-layout-3">
        {label}
      </Text>
      <Text
        level="headline-1"
        className={cn(
          'tabular-nums',
          positive ? 'text-content-positive-soft' : 'text-content-layout-1'
        )}
      >
        {value}
      </Text>
      <Text level="caption" className="text-content-layout-3">
        Mean latency
      </Text>
    </VStack>
  )
}

function BeforeAfterResult({ result }: { result: CacheRunResult }) {
  const comparison = summarizeQuickComparison(result)
  const reduction = Math.max(
    0,
    (1 - comparison.readyset.mean / comparison.upstream.mean) * 100
  )

  return (
    <VStack className="items-stretch gap-5 py-1">
      <HStack className="items-center justify-between gap-4 flex-wrap">
        <VStack className="items-start gap-1">
          <Text level="label-medium" className="text-content-layout-1">
            Latency before and after Readyset
          </Text>
          <Text level="caption" className="text-content-layout-3">
            Same query, same sample count, one cache layer added.
          </Text>
        </VStack>
        <ResultStatus label="Paired test complete" />
      </HStack>

      <div className="grid gap-5 laptop:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)_minmax(12rem,0.7fr)] laptop:items-center">
        <BeforeAfterValue
          label="Upstream"
          value={formatMs(comparison.upstream.mean)}
        />
        <Icon
          name="arrow-right"
          label=""
          aria-hidden="true"
          className="hidden h-5 w-5 text-content-layout-3 laptop:block"
        />
        <BeforeAfterValue
          label="Readyset"
          value={formatMs(comparison.readyset.mean)}
          positive
        />
        <VStack className="items-start gap-1 border-t border-border-layout-1 pt-4 laptop:border-t-0 laptop:border-l laptop:pt-0 laptop:pl-5">
          <Text
            level="headline-3"
            className="text-content-positive-soft tabular-nums"
          >
            {reduction.toFixed(1)}%
          </Text>
          <Text level="label-small" className="text-content-layout-1">
            lower mean latency
          </Text>
          <Text level="caption" className="text-content-layout-3">
            {comparison.speedup.toFixed(1)}× faster overall
          </Text>
        </VStack>
      </div>

      <ResultMeta comparison={comparison} />
    </VStack>
  )
}

const evidenceRows = [
  { label: 'Mean', key: 'mean' },
  { label: 'P50', key: 'p50' },
  { label: 'P95', key: 'p95' },
] as const

function EvidenceMatrixResult({ result }: { result: CacheRunResult }) {
  const comparison = summarizeQuickComparison(result)

  return (
    <VStack className="items-stretch gap-5 py-1">
      <ResultHeading comparison={comparison} eyebrow="Latency evidence" />

      <div className="overflow-hidden rounded-xl border border-border-layout-1">
        <div className="grid grid-cols-[minmax(5rem,1fr)_minmax(6rem,1fr)_minmax(6rem,1fr)_minmax(6rem,1fr)] gap-3 border-b border-border-layout-1 bg-surface-layout-2/30 px-4 py-3">
          <Text level="caption" className="text-content-layout-3">
            Metric
          </Text>
          <Text level="caption" className="text-content-layout-3 text-right">
            Upstream
          </Text>
          <Text level="caption" className="text-content-layout-3 text-right">
            Readyset
          </Text>
          <Text level="caption" className="text-content-layout-3 text-right">
            Improvement
          </Text>
        </div>
        {evidenceRows.map(({ label, key }, index) => (
          <div
            key={key}
            className={cn(
              'grid grid-cols-[minmax(5rem,1fr)_minmax(6rem,1fr)_minmax(6rem,1fr)_minmax(6rem,1fr)] items-center gap-3 px-4 py-3',
              index < evidenceRows.length - 1 &&
                'border-b border-border-layout-1'
            )}
          >
            <Text level="label-small" className="text-content-layout-1">
              {label}
            </Text>
            <Text
              level="mono-small"
              className="text-content-layout-2 text-right tabular-nums"
            >
              {formatMs(comparison.upstream[key])}
            </Text>
            <Text
              level="mono-small"
              className="text-content-positive-soft text-right tabular-nums"
            >
              {formatMs(comparison.readyset[key])}
            </Text>
            <Text
              level="mono-small"
              className="text-content-positive-soft text-right tabular-nums"
            >
              {speedupLabel(comparison.upstream[key], comparison.readyset[key])}
            </Text>
          </div>
        ))}
      </div>

      <ResultMeta comparison={comparison} />
    </VStack>
  )
}

function ReceiptMetric({
  label,
  value,
  positive = false,
}: {
  label: string
  value: string
  positive?: boolean
}) {
  return (
    <VStack className="items-start gap-1">
      <Text level="caption" className="text-content-layout-3">
        {label}
      </Text>
      <Text
        level="mono-medium"
        className={cn(
          'tabular-nums',
          positive ? 'text-content-positive-soft' : 'text-content-layout-1'
        )}
      >
        {value}
      </Text>
    </VStack>
  )
}

function RunReceiptResult({ result }: { result: CacheRunResult }) {
  const comparison = summarizeQuickComparison(result)

  return (
    <div className="grid gap-6 py-1 laptop:grid-cols-[minmax(15rem,0.8fr)_minmax(0,1.4fr)]">
      <VStack className="items-stretch justify-between gap-8 border-b border-border-layout-1 pb-5 laptop:border-r laptop:border-b-0 laptop:pr-6 laptop:pb-0">
        <VStack className="items-start gap-2">
          <ResultStatus label="Test passed" />
          <Text
            level="headline-2"
            className="text-content-positive-soft tabular-nums"
          >
            {comparison.speedup.toFixed(1)}× faster
          </Text>
          <Text level="caption" className="text-content-layout-3">
            Readyset returned the same workload with lower latency.
          </Text>
        </VStack>
        <Text level="mono-small" className="text-content-layout-3">
          Run QT-de8588c3
        </Text>
      </VStack>

      <VStack className="items-stretch gap-5">
        <div className="grid grid-cols-2 gap-5 tablet:grid-cols-4">
          <ReceiptMetric
            label="Upstream mean"
            value={formatMs(comparison.upstream.mean)}
          />
          <ReceiptMetric
            label="Readyset mean"
            value={formatMs(comparison.readyset.mean)}
            positive
          />
          <ReceiptMetric
            label="Samples"
            value={`${comparison.upstream.completed} + ${comparison.readyset.completed}`}
          />
          <ReceiptMetric label="Errors" value="0" positive />
        </div>
        <div className="grid gap-3 border-t border-border-layout-1 pt-4 tablet:grid-cols-3">
          <ReceiptMetric label="Target" value="demo" />
          <ReceiptMetric label="Mode" value="Paired" />
          <ReceiptMetric label="Finished" value="Just now" />
        </div>
      </VStack>
    </div>
  )
}

function RangeTrack({
  label,
  min,
  p50,
  max,
  scale,
  positive = false,
}: {
  label: string
  min: number
  p50: number
  max: number
  scale: number
  positive?: boolean
}) {
  const left = Math.min((min / scale) * 100, 100)
  const width = Math.max(((max - min) / scale) * 100, 1.5)
  const marker = Math.min((p50 / scale) * 100, 100)

  return (
    <div className="grid gap-3 tablet:grid-cols-[7rem_minmax(0,1fr)_9rem] tablet:items-center">
      <VStack className="items-start gap-0.5">
        <Text level="label-small" className="text-content-layout-1">
          {label}
        </Text>
        <Text level="caption" className="text-content-layout-3">
          P50 {formatMs(p50)}
        </Text>
      </VStack>
      <div className="relative h-7">
        <div className="absolute inset-x-0 top-3 h-0.5 bg-surface-layout-2" />
        <div
          className={cn(
            'absolute top-2.5 h-1.5 rounded-full',
            positive ? 'bg-surface-positive-solid' : 'bg-content-layout-3/50'
          )}
          style={{ left: `${left}%`, width: `${width}%` }}
        />
        <div
          className={cn(
            'absolute top-1.5 h-3.5 w-0.5',
            positive ? 'bg-surface-positive-solid' : 'bg-content-layout-2'
          )}
          style={{ left: `${marker}%` }}
        />
      </div>
      <Text
        level="mono-small"
        className={cn(
          'text-right tabular-nums',
          positive ? 'text-content-positive-soft' : 'text-content-layout-2'
        )}
      >
        {formatMs(min)}–{formatMs(max)}
      </Text>
    </div>
  )
}

function LatencyRangeResult({ result }: { result: CacheRunResult }) {
  const comparison = summarizeQuickComparison(result)
  const scale = Math.max(result.origin_stats.max, result.cache_stats.max)

  return (
    <VStack className="items-stretch gap-5 py-1">
      <HStack className="items-start justify-between gap-4 flex-wrap">
        <VStack className="items-start gap-1">
          <Text level="label-medium" className="text-content-layout-1">
            Latency distribution
          </Text>
          <Text level="caption" className="text-content-layout-3">
            Both ranges share the same {formatMs(scale)} scale.
          </Text>
        </VStack>
        <HStack className="items-baseline gap-2">
          <Text
            level="headline-3"
            className="text-content-positive-soft tabular-nums"
          >
            {comparison.speedup.toFixed(1)}×
          </Text>
          <Text level="label-small" className="text-content-layout-1">
            faster
          </Text>
        </HStack>
      </HStack>

      <VStack className="items-stretch gap-4 border-y border-border-layout-1 py-5">
        <RangeTrack
          label="Upstream"
          min={result.origin_stats.min}
          p50={result.origin_stats.p50}
          max={result.origin_stats.max}
          scale={scale}
        />
        <RangeTrack
          label="Readyset"
          min={result.cache_stats.min}
          p50={result.cache_stats.p50}
          max={result.cache_stats.max}
          scale={scale}
          positive
        />
      </VStack>

      <ResultMeta comparison={comparison} />
    </VStack>
  )
}

const resultVariants = [
  {
    name: 'Paired lanes',
    description: 'The closest evolution of the current test result.',
    render: (result: CacheRunResult) => <PairedLanesResult result={result} />,
  },
  {
    name: 'Before and after',
    description: 'Makes the latency reduction the primary reading path.',
    render: (result: CacheRunResult) => <BeforeAfterResult result={result} />,
  },
  {
    name: 'Evidence matrix',
    description: 'Treats the result as a compact benchmark report.',
    render: (result: CacheRunResult) => (
      <EvidenceMatrixResult result={result} />
    ),
  },
  {
    name: 'Run receipt',
    description: 'Emphasizes completion, run identity, and test integrity.',
    render: (result: CacheRunResult) => <RunReceiptResult result={result} />,
  },
  {
    name: 'Latency range',
    description: 'Shows how tightly each lane performed on one shared scale.',
    render: (result: CacheRunResult) => <LatencyRangeResult result={result} />,
  },
] as const

function ResultRail({ result }: { result: CacheRunResult }) {
  const comparison = summarizeQuickComparison(result)

  return (
    <VStack className="h-full min-h-72 items-stretch justify-between gap-10">
      <VStack className="items-start gap-2">
        <Text level="caption" className="text-content-layout-3">
          Test result
        </Text>
        <HStack className="items-baseline gap-1.5">
          <Text
            level="headline-2"
            className="text-content-positive-soft tabular-nums"
          >
            {comparison.speedup.toFixed(1)}×
          </Text>
          <Text level="label-small" className="text-content-positive-soft">
            faster
          </Text>
        </HStack>
        <Text level="caption" className="text-content-layout-3">
          Mean latency with Readyset
        </Text>
      </VStack>

      <VStack className="items-stretch gap-3">
        <HStack className="items-center justify-between gap-2">
          <VStack className="items-start gap-0.5">
            <Text level="caption" className="text-content-layout-3">
              Upstream
            </Text>
            <Text level="mono-medium" className="text-content-layout-1">
              {formatMs(comparison.upstream.mean)}
            </Text>
          </VStack>
          <Icon
            name="arrow-right"
            label=""
            aria-hidden="true"
            className="h-4 w-4 text-content-positive-soft"
          />
          <VStack className="items-end gap-0.5">
            <Text level="caption" className="text-content-layout-3">
              Readyset
            </Text>
            <Text level="mono-medium" className="text-content-positive-soft">
              {formatMs(comparison.readyset.mean)}
            </Text>
          </VStack>
        </HStack>
        <div className="border-t border-border-layout-1 pt-3">
          <Text level="caption" className="text-content-layout-3">
            {comparison.upstream.completed} samples per lane
          </Text>
        </div>
      </VStack>
    </VStack>
  )
}

/**
 * Lab-only copy of the Impact rail query card with the quick-test result folded
 * into its evidence hierarchy. The production QueryCardImpact stays untouched.
 */
export function QueryCardResultExperiment({ card }: { card: QueryCardProps }) {
  const result = resultForQuery(card.sql)

  return (
    <Card
      className={cn(
        'overflow-hidden',
        card.className,
        card.highlighted &&
          'bg-surface-primary-soft ring-2 ring-border-primary-soft'
      )}
    >
      <Card.Content className="overflow-hidden rounded-none border-0 bg-transparent p-0">
        <div className="grid laptop:grid-cols-[16rem_minmax(0,1fr)]">
          <div className="border-b border-border-layout-1 bg-surface-layout-2/30 p-5 laptop:border-r laptop:border-b-0">
            <ResultRail result={result} />
          </div>

          <div className="min-w-0">
            <QueryCardHeader
              leading={card.leading}
              title={card.title}
              badges={card.badges}
              menu={card.menu}
            />
            {card.editor ? (
              <div className="bg-surface-layout-1/50 p-4">{card.editor}</div>
            ) : (
              <QueryCardSql
                sql={card.sql}
                dialect={card.dialect}
                initiallyExpanded
                expandable
                copyable
              />
            )}
            <div className="border-t border-border-layout-1 p-4">
              <ComparisonProfile result={result} appearance="contained" />
            </div>
          </div>
        </div>
      </Card.Content>

      <QueryCardFooter
        meta={card.meta}
        secondaryActions={card.secondaryActions}
        primaryAction={card.primaryAction}
        detailsOpen={false}
      />
    </Card>
  )
}

/**
 * Five lab-only result hierarchies rendered with the production QueryCard shell.
 * This keeps the query anatomy fixed so only the test-result reading model
 * changes between directions.
 */
export function QueryCardResultVariants({ card }: { card: QueryCardProps }) {
  const result = resultForQuery(card.sql)
  const {
    selectable: _selectable,
    selected: _selected,
    onSelect: _onSelect,
    selectionLabel: _selectionLabel,
    ...resultCard
  } = card

  return (
    <VStack className="items-stretch gap-8">
      {resultVariants.map((variant, index) => (
        <section key={variant.name} className="space-y-2">
          <HStack className="items-end justify-between gap-4 flex-wrap">
            <VStack className="items-start gap-1">
              <HStack className="items-center gap-2">
                <Text level="mono-small" className="text-content-layout-3">
                  {String(index + 1).padStart(2, '0')}
                </Text>
                <Text level="label-medium" className="text-content-layout-1">
                  {variant.name}
                </Text>
              </HStack>
              <Text level="caption" className="text-content-layout-3">
                {variant.description}
              </Text>
            </VStack>
            <Text level="caption" className="text-content-layout-3">
              Same query · same measurements
            </Text>
          </HStack>

          <QueryCard {...resultCard} expansion={variant.render(result)} />
        </section>
      ))}
    </VStack>
  )
}
