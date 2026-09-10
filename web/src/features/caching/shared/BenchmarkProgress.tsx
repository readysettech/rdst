/**
 * One progress presentation for both benchmark surfaces: what the run is
 * doing on the left, how long it has been at it on the right, and a track
 * underneath. A phase whose end cannot be counted leaves `value` out and gets
 * an indeterminate track. [D-18, D-27]
 */

import { cn } from '@rs/tailwind-base'
import { Progress } from '@rs/ui-new/progress'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { useEffect, useState } from 'react'

export function BenchmarkProgress({
  value,
  max,
  label,
  timing,
  detail,
  ariaLabel,
  className,
}: {
  /** Completed units. Omit while the end is not yet countable. */
  value?: number
  max?: number
  /** What the run is doing, in the user's words. */
  label: string
  /** Elapsed, remaining, or both — whichever the surface can state honestly. */
  timing?: string
  /** One line of context under the track. */
  detail?: string
  ariaLabel: string
  className?: string
}) {
  return (
    <VStack className={cn('items-stretch gap-1.5', className)}>
      <HStack className="items-baseline justify-between gap-3">
        <Text level="caption" className="text-content-layout-2">
          {label}
        </Text>
        {timing && (
          <Text
            level="caption"
            className="shrink-0 text-content-layout-3 tabular-nums"
          >
            {timing}
          </Text>
        )}
      </HStack>
      <Progress value={value} max={max} label={ariaLabel} />
      {detail && (
        <Text level="caption" className="text-content-layout-3">
          {detail}
        </Text>
      )}
    </VStack>
  )
}

/**
 * Wall-clock seconds since `active` last became true. A preparation tick
 * carries no elapsed time of its own, so the clock the user watches has to
 * come from the page.
 */
export function useElapsedSeconds(active: boolean): number {
  const [startedAt, setStartedAt] = useState<number | null>(null)
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active) {
      setStartedAt(null)
      return
    }
    const start = Date.now()
    setStartedAt(start)
    setNow(start)
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [active])
  if (startedAt === null) return 0
  return Math.max(0, (now - startedAt) / 1000)
}
