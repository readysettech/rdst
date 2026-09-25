// Each database's monitoring state for the target switcher: a dot that moves while RDST
// is collecting, and the same words and colors as the Database page header
// (monitoringStatus). Labels always show; color is never the only signal.
import { cn } from '@rs/tailwind-base'
import { useQueries } from '@tanstack/react-query'
import { useMemo } from 'react'
import {
  MONITORING_TONE_CLASSES,
  type MonitoringTone,
  monitoringStatus,
} from '@/features/monitor-kit/aiMonitoring'
import { getPulses, getSettings } from '@/lib/monitor/api'
import { monitorKeys } from '@/lib/monitor/keys'
import type {
  MonitorLiveState,
  MonitorPulsePage,
  MonitorSettings,
} from '@/lib/monitor/types'

export interface TargetMonitorLine {
  /** What the dot does: moves while collecting, stays still otherwise, hollow when off. */
  dot: 'live' | 'still' | 'hollow'
  tone: MonitoringTone
  /** "AI monitoring on", "Local monitoring only", "Can't reach the database". */
  label: string
  /** A second, quieter phrase: why AI is off, or that readings are late. */
  reason: string | null
  /** True once the AI state is known to be on. */
  ai: boolean
  /** Monitoring is on (collecting, paused or late). */
  monitored: boolean
}

/** One database's switcher line from its settings and its newest pulse state. */
export function targetMonitorLine(
  settings: MonitorSettings | undefined,
  pulseState: MonitorLiveState | undefined,
  options: {
    failed?: boolean
    now?: number
    firstReading?: boolean
    delayReason?: string | null
  } = {}
): TargetMonitorLine | null {
  if (!settings)
    return options.failed
      ? {
          dot: 'still',
          tone: 'negative',
          label: "Status didn't load",
          reason: null,
          ai: false,
          monitored: false,
        }
      : null
  const status = monitoringStatus({
    enabled: settings.enabled,
    ai: settings.ai_monitoring,
    reason: settings.ai_reason,
    since: settings.ai_since,
    connectionLost: !options.firstReading && pulseState === 'connection_lost',
    now: options.now,
  })
  if (status.kind === 'off')
    return {
      dot: 'hollow',
      tone: 'neutral',
      label: status.label,
      reason: null,
      ai: false,
      monitored: false,
    }
  if (status.kind === 'paused')
    return {
      dot: 'still',
      tone: 'negative',
      label: status.reasonLabel ?? status.label,
      reason: status.reasonLabel ? status.label : null,
      ai: false,
      monitored: true,
    }
  if (options.firstReading && settings.enabled)
    return {
      dot: 'live',
      tone: 'neutral',
      label: 'Taking the first reading',
      reason: options.delayReason ?? null,
      ai: status.kind === 'ai_on',
      monitored: true,
    }
  const late = pulseState === 'late'
  return {
    dot: late ? 'still' : 'live',
    tone: late ? 'warning' : status.tone,
    label: status.label,
    reason: late
      ? (options.delayReason ?? 'Readings delayed')
      : status.reasonLabel,
    ai: status.kind === 'ai_on',
    monitored: true,
  }
}

/**
 * Settings and the newest pulse for every target, refreshed while `watching`. `live`
 * carries a target's state from its open stream, which is fresher than its last read.
 */
export function useTargetMonitorLines(
  targets: string[],
  watching: boolean,
  live?: { target: string | null; state: MonitorLiveState | 'unknown' }
): Map<string, TargetMonitorLine | null> {
  const refetchInterval = watching ? 30_000 : false
  const settings = useQueries({
    queries: targets.map((target) => ({
      queryKey: monitorKeys.settings(target),
      queryFn: () => getSettings(target),
      staleTime: 30_000,
      refetchInterval,
    })),
  })
  const pulses = useQueries({
    queries: targets.map((target) => ({
      queryKey: monitorKeys.pulse(target),
      queryFn: () => getPulses(target, 5),
      staleTime: 30_000,
      refetchInterval,
    })),
  })
  const signature = [...settings, ...pulses]
    .map((result) => `${result.dataUpdatedAt}:${result.isError}`)
    .join(',')
  // biome-ignore lint/correctness/useExhaustiveDependencies: the signature tracks every result
  return useMemo(() => {
    const lines = new Map<string, TargetMonitorLine | null>()
    targets.forEach((target, index) => {
      const pulse = pulses[index]?.data as MonitorPulsePage | undefined
      const streamed =
        live?.target === target && live.state !== 'unknown'
          ? live.state
          : undefined
      lines.set(
        target,
        targetMonitorLine(
          settings[index]?.data as MonitorSettings | undefined,
          streamed ?? pulse?.pulses[0]?.state,
          {
            failed: settings[index]?.isError,
            firstReading: pulse?.first_reading,
            delayReason: pulse?.delay_reason,
          }
        )
      )
    })
    return lines
  }, [targets.join(','), signature, live?.target, live?.state])
}

/** The dot and words for one database. `compact` keeps it to one short line. */
export function TargetMonitorStatus({
  line,
  compact = false,
  className,
}: {
  line: TargetMonitorLine | null | undefined
  compact?: boolean
  className?: string
}) {
  if (!line)
    return (
      <span className={cn('text-label-small text-content-layout-3', className)}>
        Checking monitoring
      </span>
    )
  const tone = MONITORING_TONE_CLASSES[line.tone]
  return (
    <span
      data-testid="target-monitor-status"
      data-dot={line.dot}
      className={cn(
        'inline-flex min-w-0 items-center gap-1.5 text-label-small',
        tone.text,
        className
      )}
    >
      <span
        aria-hidden="true"
        className="relative inline-flex h-2 w-2 shrink-0"
      >
        <span
          data-heartbeat={line.dot === 'live' ? 'on' : undefined}
          className={cn(
            'relative inline-flex h-2 w-2 rounded-full',
            line.dot === 'hollow' ? 'border border-content-layout-3' : tone.dot,
            line.dot === 'live' && 'rdst-heartbeat'
          )}
        />
      </span>
      <span className="truncate">
        {line.label}
        {!compact && line.reason ? (
          <span className="text-content-layout-3">, {line.reason}</span>
        ) : null}
      </span>
    </span>
  )
}
