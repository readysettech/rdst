import { cn } from '@rs/tailwind-base'
import { Card } from '@rs/ui-new/card-2'
import { Skeleton } from '@rs/ui-new/skeleton'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import {
  type CompareBatchSnapshot,
  type CompareBatchStatus,
  type CompareQueryOutcome,
  isNotComparableCompareOutcome,
  isUnsupportedCompareOutcome,
} from './compareRuns'

export function compareErrorDetail(error: unknown) {
  if (error instanceof Error) return error.message
  if (typeof error === 'string') return error
  return error ? String(error) : undefined
}

export function compareStatusLabel(status: CompareBatchStatus) {
  if (status === 'complete') return 'Complete'
  if (status === 'partial') return 'Completed with errors'
  if (status === 'failed') return 'Failed'
  if (status === 'cancelled') return 'Cancelled'
  return 'Comparing'
}

/**
 * One line naming what the whole batch is doing, from the snapshot's own
 * counters. The sandbox measures one query at a time, so "running" is the
 * single query holding the lease and "queued" is everything behind it.
 */
export function compareBatchProgressLine(
  snapshot: CompareBatchSnapshot
): string {
  // "done", not "compared": a not-comparable or failed query is finished
  // without having been compared; each query's own card carries that split.
  const parts = [`${snapshot.completed} of ${snapshot.total} done`]
  if (snapshot.running > 0) parts.push(`${snapshot.running} running`)
  if (snapshot.queued > 0) parts.push(`${snapshot.queued} queued`)
  return parts.join(' · ')
}

/**
 * Seconds of measurement still ahead: whatever the running query has left,
 * plus a full duration for every query still waiting for the sandbox.
 */
export function compareBatchRemainingSeconds(
  snapshot: CompareBatchSnapshot,
  durationSeconds: number
): number {
  return snapshot.queryOutcomes.reduce((remaining, outcome) => {
    if (outcome.status === 'queued') return remaining + durationSeconds
    if (outcome.status === 'running') {
      return remaining + Math.max(0, durationSeconds - outcome.elapsedSeconds)
    }
    return remaining
  }, 0)
}

/**
 * The batch has started but nothing has measured yet, so there is no curve to
 * read and the honest thing to report is what the sandbox is doing.
 */
export function comparePreparationState(snapshot: CompareBatchSnapshot) {
  if (snapshot.status !== 'running') return null
  if (snapshot.queryOutcomes.some((outcome) => outcome.timeline.length > 0)) {
    return null
  }
  const active = snapshot.runs.find((run) =>
    ['running', 'reconnecting', 'needs_key'].includes(run.status)
  )
  const stage = active?.stage ?? 'connecting'
  const label =
    stage === 'queued' ? 'Queued' : stage === 'warming' ? 'Warming' : 'Starting'
  return {
    label,
    message: active?.message || 'Preparing the temporary Readyset sandbox.',
  }
}

/** Speedup readouts stay at one decimal until they no longer need it. */
export function formatSpeedup(value: number) {
  return value.toFixed(value >= 10 ? 0 : 1)
}

/** Throughput readouts drop their decimal once the number is big enough. */
export function formatQps(value: number | null | undefined) {
  const qps = value ?? 0
  return qps.toFixed(qps >= 10 ? 0 : 1)
}

// A measured query's own verdict is its speedup, so the chip carries the
// number rather than a generic "Measured". Below this margin the two lanes
// are close enough that naming a winner would overstate the measurement.
const COMPARE_TIE_MARGIN = 1.05

/**
 * The chip every surface uses for one query's outcome: the verdict, its tone,
 * and (for a measurement) the speedup it produced.
 */
export function compareQueryOutcomePresentation(outcome: CompareQueryOutcome): {
  label: string
  variant: 'positive' | 'warning' | 'negative' | 'informative' | 'neutral'
} {
  if (outcome.status === 'succeeded') {
    const speedup = outcome.result?.speedup_mean
    if (!speedup) return { label: 'Measured', variant: 'positive' }
    if (speedup >= COMPARE_TIE_MARGIN) {
      return { label: `${formatSpeedup(speedup)}× faster`, variant: 'positive' }
    }
    if (speedup <= 1 / COMPARE_TIE_MARGIN) {
      return {
        label: `${formatSpeedup(1 / speedup)}× slower`,
        variant: 'warning',
      }
    }
    return { label: 'About the same', variant: 'warning' }
  }
  if (outcome.status === 'queued') {
    return { label: 'Queued', variant: 'neutral' }
  }
  if (outcome.status === 'running') {
    return { label: 'Running', variant: 'informative' }
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

/**
 * The chip in a query card's footer band: what happened to the query, rather
 * than how much faster it got. A measurement's speedup is its own readout
 * beside the two lanes, so the footer keeps the classification.
 */
export function compareQueryStatusChip(outcome: CompareQueryOutcome) {
  return outcome.status === 'succeeded'
    ? { label: 'Compared', variant: 'positive' as const }
    : compareQueryOutcomePresentation(outcome)
}

/**
 * What a multi-query batch costs, disclosed before the run: the sandbox takes
 * one query at a time, so the wait is the configured duration times the number
 * of queries. Rounded up to whole minutes -- the estimate excludes per-query
 * sandbox setup, so it should never read as optimistic.
 */
export function compareBatchDurationEstimate(
  queryCount: number,
  durationSeconds: number
): string | null {
  if (queryCount < 2) return null
  const minutes = Math.max(1, Math.round((queryCount * durationSeconds) / 60))
  return `${queryCount} queries run one at a time · about ${minutes} ${
    minutes === 1 ? 'minute' : 'minutes'
  }`
}

/**
 * The chart's colour key for one lane. Both lanes carry it wherever their
 * numbers appear, so a lane's readout and its line read as the same lane.
 */
export function CompareLaneDot({ lane }: { lane: 'upstream' | 'readyset' }) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        'inline-block h-2 w-2 shrink-0 rounded-full',
        lane === 'readyset' ? 'bg-content-viz-cache' : 'bg-content-viz-origin'
      )}
    />
  )
}

export function CompareSkeleton() {
  return (
    <Card aria-label="Loading comparison">
      <Card.Header>
        <Skeleton className="h-5 w-44" />
        <Skeleton className="h-4 w-80 max-w-full" />
      </Card.Header>
      <Card.Content>
        <div className="grid gap-6 tablet:grid-cols-3">
          <VStack className="items-stretch gap-3 tablet:col-span-2">
            <Skeleton className="h-16 w-full rounded-xl" />
            <Skeleton className="h-16 w-full rounded-xl" />
            <Skeleton className="h-16 w-full rounded-xl" />
          </VStack>
          <Skeleton className="h-60 w-full rounded-xl" />
        </div>
      </Card.Content>
      <Card.Footer>
        <Skeleton className="h-9 w-36 rounded-lg" />
      </Card.Footer>
    </Card>
  )
}

export function CompareSummaryRow({
  label,
  value,
  tone = 'neutral',
}: {
  label: string
  value: string
  tone?: 'neutral' | 'positive' | 'warning'
}) {
  return (
    <HStack className="items-start justify-between gap-4 border-b border-border-layout-soft py-3 last:border-0">
      <Text level="caption" className="text-content-layout-3">
        {label}
      </Text>
      <Text
        level="label-small"
        className={cn(
          'text-right',
          tone === 'positive'
            ? 'text-content-positive-soft'
            : tone === 'warning'
              ? 'text-content-warning-soft'
              : 'text-content-layout-1'
        )}
      >
        {value}
      </Text>
    </HStack>
  )
}
