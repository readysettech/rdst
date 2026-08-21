import { cn } from '@rs/tailwind-base'
import { Card } from '@rs/ui-new/card-2'
import { Skeleton } from '@rs/ui-new/skeleton'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import {
  type CompareQueryOutcome,
  isNotComparableCompareOutcome,
  isUnsupportedCompareOutcome,
} from './compareRuns'

export function compareErrorDetail(error: unknown) {
  if (error instanceof Error) return error.message
  if (typeof error === 'string') return error
  return error ? String(error) : undefined
}

/** Speedup readouts stay at one decimal until they no longer need it. */
export function formatSpeedup(value: number) {
  return value.toFixed(value >= 10 ? 0 : 1)
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
