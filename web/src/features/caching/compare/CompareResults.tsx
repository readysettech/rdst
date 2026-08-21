import { cn } from '@rs/tailwind-base'
import { Button } from '@rs/ui-new/button'
import { Card } from '@rs/ui-new/card-2'
import { IconTile } from '@rs/ui-new/icon-tile'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { summarizeSustainedComparison } from '../comparisonMetrics'
import { CompareLiveChart } from './CompareLiveChart'
import { compareStatusLabel } from './CompareRunning'
import {
  type CompareBatchSnapshot,
  type CompareQueryOutcome,
  isNotComparableCompareOutcome,
  isUnsupportedCompareOutcome,
} from './compareRuns'
import type { CompareController } from './useCompareController'

function formatQps(value: number | null) {
  const qps = value ?? 0
  return qps.toFixed(qps >= 10 ? 0 : 1)
}

export function compareQueryOutcomePresentation(outcome: CompareQueryOutcome): {
  label: string
  variant: 'positive' | 'warning' | 'negative'
} {
  if (outcome.status === 'succeeded') {
    return { label: 'Measured', variant: 'positive' }
  }
  if (outcome.status === 'cancelled') {
    return { label: 'Cancelled', variant: 'warning' }
  }
  if (isUnsupportedCompareOutcome(outcome)) {
    return { label: 'Unsupported', variant: 'warning' }
  }
  if (isNotComparableCompareOutcome(outcome)) {
    return { label: 'Not comparable', variant: 'warning' }
  }
  return { label: 'Failed', variant: 'negative' }
}

// An incomplete batch reads as classification, not as an app failure: most of
// what keeps a query out of "compared" is Readyset declining to cache it or a
// pre-flight check ruling the two lanes out, not something breaking.
function compareBatchOutcomeSummary(
  snapshot: CompareBatchSnapshot,
  queryOutcomes: CompareQueryOutcome[]
): string {
  const notComparable = queryOutcomes.filter(
    isNotComparableCompareOutcome
  ).length
  const failed = snapshot.failed - notComparable
  const parts = [`${snapshot.succeeded} compared`]
  if (notComparable > 0) parts.push(`${notComparable} not comparable`)
  if (failed > 0) parts.push(`${failed} failed`)
  if (snapshot.cancelled > 0) parts.push(`${snapshot.cancelled} cancelled`)
  return `${parts.join(', ')}.`
}

function ResultLane({
  label,
  qps,
  p95,
  errors,
  readyset = false,
}: {
  label: string
  qps: number | null
  p95: number
  errors: number
  readyset?: boolean
}) {
  return (
    <VStack className="min-w-0 items-stretch gap-4 p-5">
      <HStack className="items-center gap-2">
        <span
          aria-hidden="true"
          className={cn(
            'h-2 w-2 rounded-full',
            readyset ? 'bg-surface-positive-solid' : 'bg-surface-layout-2'
          )}
        />
        <Text level="label-small" className="text-content-layout-1">
          {label}
        </Text>
      </HStack>
      <HStack className="items-end justify-between gap-4">
        <VStack className="items-start gap-0.5">
          <Text
            level="headline-2"
            className={cn(
              'tabular-nums',
              readyset ? 'text-content-positive-soft' : 'text-content-layout-1'
            )}
          >
            {formatQps(qps)}
          </Text>
          <Text level="caption" className="text-content-layout-3">
            sustained QPS
          </Text>
        </VStack>
        <VStack className="items-end gap-1">
          <Text
            level="label-small"
            className="text-content-layout-1 tabular-nums"
          >
            {p95.toFixed(1)} ms p95
          </Text>
          <Text
            level="caption"
            className={cn(
              'tabular-nums',
              errors > 0 ? 'text-content-warning-soft' : 'text-content-layout-3'
            )}
          >
            {errors} {errors === 1 ? 'error' : 'errors'}
          </Text>
        </VStack>
      </HStack>
    </VStack>
  )
}

export function CompareResults({
  controller,
  onOpenQueries,
}: {
  controller: CompareController
  onOpenQueries: () => void
}) {
  const snapshot = controller.snapshot
  if (!snapshot || snapshot.status === 'running') return null
  const results = snapshot.results
  const queryOutcomes =
    snapshot.queryOutcomes ??
    results.map(({ cacheId, label, runId, result }) => ({
      cacheId,
      label,
      runId,
      status: 'succeeded' as const,
      result,
    }))
  const comparison = summarizeSustainedComparison(
    results.map(({ result }) => result)
  )
  const readysetWins = comparison.winner === 'readyset'

  return (
    <VStack className="items-stretch gap-6">
      <Card>
        <Card.Header className="items-start gap-4 tablet:flex-row tablet:items-center tablet:justify-between">
          <HStack className="items-center gap-3">
            <IconTile
              icon={readysetWins ? 'tick-double' : 'alert'}
              size="base"
              accent={readysetWins ? 'positive' : 'warning'}
            />
            <VStack className="items-start gap-0.5">
              <Card.Title>
                {results.length === 0
                  ? 'No measurements completed'
                  : readysetWins
                    ? `${comparison.speedup.toFixed(comparison.speedup >= 10 ? 0 : 1)}× faster with Readyset`
                    : `${Math.abs(comparison.improvementPercent).toFixed(0)}% slower with Readyset`}
              </Card.Title>
              <Card.Description>
                {snapshot.status === 'complete'
                  ? `Based on a sustained load across ${results.length} ${
                      results.length === 1 ? 'query' : 'queries'
                    }.`
                  : compareBatchOutcomeSummary(snapshot, queryOutcomes)}
              </Card.Description>
            </VStack>
          </HStack>
          <Tag
            variant={
              snapshot.status === 'complete'
                ? 'positive'
                : snapshot.status === 'partial'
                  ? 'warning'
                  : 'negative'
            }
            modifier="solid"
            label={compareStatusLabel(snapshot.status)}
          />
        </Card.Header>
        {results.length > 0 && (
          <Card.Content>
            <div className="grid overflow-hidden rounded-xl border border-border-layout-soft tablet:grid-cols-2 tablet:divide-x tablet:divide-border-layout-soft">
              <ResultLane
                label="Upstream"
                qps={comparison.upstream.throughput}
                p95={comparison.upstream.p95}
                errors={comparison.upstream.errors}
              />
              <ResultLane
                label="Readyset"
                qps={comparison.readyset.throughput}
                p95={comparison.readyset.p95}
                errors={comparison.readyset.errors}
                readyset
              />
            </div>
            <HStack className="mt-4 flex-wrap items-center justify-between gap-3">
              <Text level="caption" className="text-content-layout-3">
                {comparison.upstream.completed.toLocaleString()} upstream and{' '}
                {comparison.readyset.completed.toLocaleString()} Readyset
                requests completed.
              </Text>
              <Text
                level="label-small"
                className={cn(
                  readysetWins
                    ? 'text-content-positive-soft'
                    : 'text-content-warning-soft'
                )}
              >
                {comparison.speedup.toFixed(comparison.speedup >= 10 ? 0 : 1)}×
                mean latency improvement
              </Text>
            </HStack>
          </Card.Content>
        )}
        <Card.Footer>
          <HStack className="w-full flex-wrap justify-between gap-3">
            <Button
              variant="primary"
              modifier="ghost"
              label={`History ${controller.historyEntries.length}`}
              icon="observe"
              onClick={() => controller.setHistoryOpen(true)}
            />
            <HStack className="flex-wrap items-center gap-2">
              <Button
                variant="primary"
                modifier="ghost"
                label="View queries"
                icon="folder-file"
                onClick={onOpenQueries}
              />
              <Button
                variant="primary"
                modifier="outline"
                label="Adjust"
                icon="settings"
                onClick={controller.clearBatch}
              />
              <Button
                variant="rising"
                modifier="solid"
                label="Run again"
                icon="play"
                onClick={() => {
                  controller.clearBatch()
                  controller.setReviewOpen(true)
                }}
              />
            </HStack>
          </HStack>
        </Card.Footer>
      </Card>

      {snapshot.timeline.length > 0 && (
        <CompareLiveChart timeline={snapshot.timeline} />
      )}

      {queryOutcomes.length > 0 && (
        <Card>
          <Card.Header>
            <Card.Title>Per-query breakdown</Card.Title>
            <Card.Description>
              Measurements and explicit outcomes for every selected query.
            </Card.Description>
          </Card.Header>
          <Card.Content className="p-0">
            {queryOutcomes.map((outcome) => {
              const presentation = compareQueryOutcomePresentation(outcome)
              const result = outcome.result
              return (
                <div
                  key={outcome.runId ?? outcome.cacheId}
                  className="grid gap-5 border-b border-border-layout-soft px-6 py-5 last:border-0 tablet:grid-cols-[minmax(0,1fr)_minmax(20rem,1fr)]"
                >
                  <VStack className="min-w-0 items-start gap-1">
                    <Text
                      level="label-small"
                      className="truncate text-content-layout-1"
                    >
                      {outcome.label || 'Query'}
                    </Text>
                    <Text
                      level={result ? 'mono-small' : 'body-small'}
                      className={cn(
                        'line-clamp-2 text-content-layout-3',
                        result && 'line-clamp-1'
                      )}
                    >
                      {result?.query ||
                        outcome.message ||
                        'No comparison measurement was produced.'}
                    </Text>
                  </VStack>
                  {result ? (
                    <div className="grid overflow-hidden rounded-lg border border-border-layout-soft tablet:grid-cols-2 tablet:divide-x tablet:divide-border-layout-soft">
                      <VStack className="items-stretch gap-1 px-3 py-2">
                        <Text level="caption" className="text-content-layout-3">
                          Upstream
                        </Text>
                        <Text
                          level="label-small"
                          className="text-content-layout-1 tabular-nums"
                        >
                          {formatQps(result.origin.throughput_rps)} QPS ·{' '}
                          {result.origin.p95_ms.toFixed(1)} ms p95
                        </Text>
                      </VStack>
                      <VStack className="items-stretch gap-1 px-3 py-2">
                        <Text
                          level="caption"
                          className="text-content-positive-soft"
                        >
                          Readyset ·{' '}
                          {result.speedup_mean.toFixed(
                            result.speedup_mean >= 10 ? 0 : 1
                          )}
                          ×
                        </Text>
                        <Text
                          level="label-small"
                          className="text-content-layout-1 tabular-nums"
                        >
                          {formatQps(result.readyset.throughput_rps)} QPS ·{' '}
                          {result.readyset.p95_ms.toFixed(1)} ms p95
                        </Text>
                      </VStack>
                    </div>
                  ) : (
                    <HStack className="items-center justify-between gap-3 rounded-lg border border-border-layout-soft px-3 py-3">
                      <Text
                        level="body-small"
                        className="text-content-layout-2"
                      >
                        This query did not produce a measurement.
                      </Text>
                      <Tag
                        variant={presentation.variant}
                        modifier="solid"
                        label={presentation.label}
                      />
                    </HStack>
                  )}
                </div>
              )
            })}
          </Card.Content>
        </Card>
      )}
    </VStack>
  )
}
