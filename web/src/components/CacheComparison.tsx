import { cn } from '@rs/tailwind-base'
import { Button } from '@rs/ui-new/button'
import { Icon } from '@rs/ui-new/icon'
import { IconButton } from '@rs/ui-new/icon-button'
import { m } from '@rs/ui-new/motion'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { summarizeQuickComparison } from '../features/caching/comparisonMetrics'
import type { CacheRunResult } from '../types/cache'

export function formatMs(ms: number): string {
  if (ms < 1) return '<1ms'
  if (ms < 1000) return `${ms.toFixed(1)}ms`
  return `${(ms / 1000).toFixed(2)}s`
}

/** Visual latency bar — width proportional to value on a shared scale. */
function LatencyBar({
  label,
  value,
  maxValue,
  variant,
  delay = 0,
}: {
  label: string
  value: number
  maxValue: number
  variant: 'origin' | 'cache-win' | 'cache-lose'
  delay?: number
}) {
  const pct = maxValue > 0 ? Math.max((value / maxValue) * 100, 1.5) : 1.5
  const barColor =
    variant === 'cache-win'
      ? 'bg-surface-positive-solid'
      : variant === 'origin'
        ? 'bg-content-layout-3/40'
        : 'bg-surface-warning-solid/70'
  const textColor =
    variant === 'cache-win'
      ? 'text-content-positive-soft'
      : 'text-content-layout-1'

  return (
    <div className="grid grid-cols-[2.5rem_minmax(0,1fr)_4.5rem] items-center gap-3">
      <Text level="caption" className="text-content-layout-3">
        {label}
      </Text>
      <div className="relative h-1.5 overflow-hidden rounded-full bg-surface-layout-2">
        <m.div
          className={`h-full rounded-full ${barColor}`}
          initial={{ width: 0 }}
          animate={{ width: `${pct}%` }}
          transition={{ duration: 0.6, delay, ease: [0.22, 1, 0.36, 1] }}
        />
      </div>
      <Text
        level="mono-small"
        className={`text-right tabular-nums ${textColor}`}
      >
        {formatMs(value)}
      </Text>
    </div>
  )
}

function ComparisonLane({
  label,
  values,
  maxValue,
  variant,
  delay,
  appearance,
}: {
  label: 'Upstream' | 'Readyset'
  values: { mean: number; p50: number; p95: number }
  maxValue: number
  variant: 'origin' | 'cache-win' | 'cache-lose'
  delay: number
  appearance: 'flat' | 'contained'
}) {
  const dotColor =
    variant === 'cache-win'
      ? 'bg-surface-positive-solid'
      : variant === 'cache-lose'
        ? 'bg-surface-warning-solid'
        : 'bg-content-layout-3/50'

  return (
    <VStack
      className={cn(
        'items-stretch gap-4',
        appearance === 'contained' &&
          'rounded-xl border border-border-layout-1 bg-surface-layout-2/25 p-4',
        appearance === 'flat' &&
          label === 'Upstream' &&
          'pb-5 laptop:pb-0 laptop:pr-6',
        appearance === 'flat' &&
          label === 'Readyset' &&
          'border-t border-border-layout-1 pt-5 laptop:border-t-0 laptop:border-l laptop:pt-0 laptop:pl-6'
      )}
    >
      <HStack className="items-center justify-between gap-3">
        <HStack className="items-center gap-2">
          <span
            aria-hidden="true"
            className={cn('h-2 w-2 rounded-full', dotColor)}
          />
          <Text level="label-small" className="text-content-layout-1">
            {label}
          </Text>
        </HStack>
        <HStack className="items-baseline gap-1.5">
          <Text
            level="mono-medium"
            className={cn(
              'tabular-nums',
              variant === 'cache-win'
                ? 'text-content-positive-soft'
                : 'text-content-layout-1'
            )}
          >
            {formatMs(values.mean)}
          </Text>
          <Text level="caption" className="text-content-layout-3">
            mean
          </Text>
        </HStack>
      </HStack>

      <VStack className="items-stretch gap-2.5">
        <LatencyBar
          label="Mean"
          value={values.mean}
          maxValue={maxValue}
          variant={variant}
          delay={delay}
        />
        <LatencyBar
          label="P50"
          value={values.p50}
          maxValue={maxValue}
          variant={variant}
          delay={delay + 0.05}
        />
        <LatencyBar
          label="P95"
          value={values.p95}
          maxValue={maxValue}
          variant={variant}
          delay={delay + 0.1}
        />
      </VStack>
    </VStack>
  )
}

export function ComparisonProfile({
  result,
  className,
  appearance = 'flat',
}: {
  result: CacheRunResult
  className?: string
  appearance?: 'flat' | 'contained'
}) {
  const comparison = summarizeQuickComparison(result)
  const isWinner = comparison.winner === 'readyset'
  const upstreamSamples = comparison.upstream.completed
  const readysetSamples = comparison.readyset.completed
  const sampleLabel =
    upstreamSamples === readysetSamples
      ? `${upstreamSamples} samples each`
      : `${upstreamSamples} upstream / ${readysetSamples} Readyset samples`
  const maxLatency = Math.max(
    comparison.upstream.mean,
    comparison.upstream.p50,
    comparison.upstream.p95,
    comparison.readyset.mean,
    comparison.readyset.p50,
    comparison.readyset.p95
  )

  return (
    <VStack className={cn('items-stretch gap-4', className)}>
      <div
        className={cn(
          'grid laptop:grid-cols-2',
          appearance === 'contained' && 'gap-3'
        )}
      >
        <ComparisonLane
          label="Upstream"
          values={comparison.upstream}
          maxValue={maxLatency}
          variant="origin"
          delay={0.1}
          appearance={appearance}
        />
        <ComparisonLane
          label="Readyset"
          values={comparison.readyset}
          maxValue={maxLatency}
          variant={isWinner ? 'cache-win' : 'cache-lose'}
          delay={0.2}
          appearance={appearance}
        />
      </div>

      <HStack className="items-center justify-between gap-3 border-t border-border-layout-1 pt-4 flex-wrap">
        <Text level="caption" className="text-content-layout-3">
          {sampleLabel}
        </Text>
        <HStack className="items-center gap-4 flex-wrap">
          <HStack className="items-baseline gap-1.5">
            <Text level="caption" className="text-content-layout-3">
              Upstream range
            </Text>
            <Text level="mono-small" className="text-content-layout-2">
              {formatMs(result.origin_stats.min)}–
              {formatMs(result.origin_stats.max)}
            </Text>
          </HStack>
          <HStack className="items-baseline gap-1.5">
            <Text level="caption" className="text-content-layout-3">
              Readyset range
            </Text>
            <Text
              level="mono-small"
              className={cn(
                isWinner
                  ? 'text-content-positive-soft'
                  : 'text-content-layout-2'
              )}
            >
              {formatMs(result.cache_stats.min)}–
              {formatMs(result.cache_stats.max)}
            </Text>
          </HStack>
        </HStack>
      </HStack>
    </VStack>
  )
}

// Origin-vs-cache before/after card. Layout-agnostic (no table/card wrapper) so
// it can drop into the Cache page's table and the Queries workbench alike. Shows
// the outcome honestly whether the cache wins or loses (rdst-41p.4).
export function ComparisonCard({
  result,
  onDismiss,
  onDelete,
  onClose,
  appearance = 'embedded',
}: {
  result: CacheRunResult
  onDismiss?: () => void
  onDelete?: () => void
  onClose?: () => void
  appearance?: 'embedded' | 'contained'
}) {
  const comparison = summarizeQuickComparison(result)
  const isWinner = comparison.winner === 'readyset'
  const speedupDisplay = isWinner
    ? `${comparison.speedup.toFixed(1)}×`
    : `${Math.abs(comparison.improvementPercent).toFixed(0)}%`

  return (
    <m.div
      className={cn(
        'w-full',
        appearance === 'contained' &&
          'overflow-hidden rounded-xl border border-border-layout-1 bg-surface-layout-1/80'
      )}
      initial={{ opacity: 0, y: -8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35 }}
    >
      <div
        className={cn(
          'flex items-start justify-between gap-4 border-b py-4',
          appearance === 'contained' && 'px-5',
          appearance === 'contained' &&
            (isWinner
              ? 'border-border-positive-soft/30 bg-surface-positive-soft/15'
              : 'border-border-warning-soft/30 bg-surface-warning-soft/10'),
          appearance === 'embedded' &&
            (isWinner
              ? 'border-border-positive-soft/20'
              : 'border-border-warning-soft/20')
        )}
      >
        <VStack className="min-w-0 items-start gap-1">
          <Text level="caption" className="text-content-layout-3">
            Paired latency test
          </Text>
          <HStack className="items-baseline gap-2 flex-wrap">
            <m.span
              initial={{ scale: 0.8, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              transition={{ duration: 0.3, delay: 0.2 }}
            >
              <Text
                as="span"
                level="headline-3"
                className={cn(
                  'tabular-nums',
                  isWinner
                    ? 'text-content-positive-soft'
                    : 'text-content-warning-soft'
                )}
              >
                {speedupDisplay}
              </Text>
            </m.span>
            <Text level="label-medium" className="text-content-layout-1">
              {isWinner ? 'faster with Readyset' : 'slower with Readyset'}
            </Text>
            <Text level="caption" className="text-content-layout-3">
              based on mean latency
            </Text>
          </HStack>
        </VStack>
        <HStack className="shrink-0 items-center gap-2 flex-wrap justify-end">
          <HStack className="items-center gap-2 px-2">
            <Icon
              name="tick"
              label=""
              aria-hidden="true"
              className={cn(
                'h-4 w-4',
                isWinner
                  ? 'text-content-positive-soft'
                  : 'text-content-warning-soft'
              )}
            />
            <Text
              level="label-small"
              className={cn(
                isWinner
                  ? 'text-content-positive-soft'
                  : 'text-content-warning-soft'
              )}
            >
              Test complete
            </Text>
          </HStack>
          {onDelete && (
            <Button
              icon="trash"
              iconPosition="left"
              label="Delete result"
              variant="negative"
              modifier="ghost"
              size="small"
              onClick={onDelete}
            />
          )}
          {(onClose || onDismiss) && (
            <IconButton
              icon="close"
              label={onClose ? 'Close details' : 'Dismiss'}
              variant="primary"
              modifier="ghost"
              size="small"
              onClick={onClose ?? onDismiss}
            />
          )}
        </HStack>
      </div>

      <div
        className={cn(
          'py-4',
          appearance === 'contained' && 'px-5',
          appearance === 'embedded' && 'pb-0'
        )}
      >
        <ComparisonProfile result={result} />
      </div>
    </m.div>
  )
}
