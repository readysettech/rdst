// One measurement-only sentence about a query's latency, judged from its own
// readings over the range: back to normal after a slow stretch, or slower than its
// typical latency now. It names no cause.
import { cn } from '@rs/tailwind-base'
import { Icon } from '@rs/ui-new/icon'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import type { QuerySeriesPoint } from '@/lib/monitor/types'
import { measureMs, text } from './format'

export interface LatencyStatus {
  kind: 'recovered' | 'slower'
  title: string
  sentence: string
}

/** Readings needed before the typical latency means anything. */
const MIN_READINGS = 6
/** A reading this many times the typical latency, and at least 0.1 ms above it, is slow. */
const SLOW_RATIO = 2
const SLOWER_NOW_RATIO = 1.5
const MIN_DELTA_MS = 0.1

function median(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b)
  const mid = Math.floor(sorted.length / 2)
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2
}

const clock = (ts: number) =>
  new Date(ts * 1000).toLocaleString(undefined, {
    hour: 'numeric',
    minute: '2-digit',
  })

const pair = (from: number, to: number) => {
  const a = measureMs(from)
  const b = measureMs(to)
  return a.unit === b.unit
    ? `${a.value} -> ${b.value} ${b.unit}`
    : `${text(a)} -> ${text(b)}`
}

/**
 * Compares the latest readings with the query's typical (median) latency over the
 * range. Returns null when the query is steady or there are too few readings.
 */
export function latencyStatus(
  points: Pick<QuerySeriesPoint, 'ts' | 'mean_ms' | 'qps'>[]
): LatencyStatus | null {
  const readings = points
    .filter(
      (point): point is typeof point & { mean_ms: number } =>
        point.mean_ms != null && (point.qps ?? 0) > 0
    )
    .sort((a, b) => a.ts - b.ts)
  if (readings.length < MIN_READINGS) return null
  const typical = median(readings.map((point) => point.mean_ms))
  const recent = readings.slice(-3)
  const now = median(recent.map((point) => point.mean_ms))
  const slow = (ms: number, ratio: number) =>
    ms >= typical * ratio && ms - typical >= MIN_DELTA_MS

  if (slow(now, SLOWER_NOW_RATIO))
    return {
      kind: 'slower',
      title: 'Still slower than its baseline',
      sentence: `${text(measureMs(now))} now against a typical ${text(measureMs(typical))} over this range.`,
    }

  const earlier = readings.slice(0, -recent.length)
  let last = -1
  for (let i = earlier.length - 1; i >= 0; i--)
    if (slow(earlier[i].mean_ms, SLOW_RATIO)) {
      last = i
      break
    }
  if (last < 0) return null
  let peak = earlier[last].mean_ms
  for (let i = last; i >= 0 && slow(earlier[i].mean_ms, SLOW_RATIO); i--)
    peak = Math.max(peak, earlier[i].mean_ms)
  const since = readings[last + 1].ts
  return {
    kind: 'recovered',
    title: 'Latency back to normal',
    sentence: `Back to normal since ${clock(since)} (${pair(peak, now)}).`,
  }
}

export function LatencyStatusLine({
  status,
  className,
}: {
  status: LatencyStatus
  className?: string
}) {
  const recovered = status.kind === 'recovered'
  return (
    <HStack
      className={cn(
        'items-start gap-3 rounded-panel border px-4 py-3',
        recovered
          ? 'border-border-positive-soft bg-surface-positive-soft/40'
          : 'border-border-warning-soft bg-surface-warning-soft/40',
        className
      )}
      data-testid="latency-status"
      data-kind={status.kind}
    >
      <Icon
        name={recovered ? 'tick' : 'alert'}
        label=""
        aria-hidden="true"
        className={cn(
          'mt-0.5 h-4 w-4 shrink-0',
          recovered ? 'text-content-positive-soft' : 'text-content-warning-soft'
        )}
      />
      <VStack className="min-w-0 items-start gap-0.5">
        <Text level="label-medium" className="text-content-layout-1">
          {status.title}
        </Text>
        <Text level="body-small" className="tabular-nums text-content-layout-2">
          {status.sentence}
        </Text>
      </VStack>
    </HStack>
  )
}
