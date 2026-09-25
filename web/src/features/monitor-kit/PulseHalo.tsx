// PulseHalo - the one live element of the Database header (DESIGN_V4 section 1).
// The startup glyph has a white glow. After startup, its heartbeat sits inside a ring
// that fills toward the next reading,
// on the app's one reading timer (pulse.clock). Each reading completes the ring: one
// that lands early finishes its fill quickly instead of jumping, it flashes, a ripple
// leaves it, the glyph beats and the ring starts again from empty. A reading that's due
// and hasn't landed holds the ring full and breathing. Hover or focus shows one line, "Next reading in
// 23 s" or that readings are late; the page carries no other "next in y" text.
//
// Usage:
//   const pulse = useMonitorPulse(target)
//   <PulseHalo pulse={pulse} qps={health.now.qps} fill={0.09} tone="positive" />
import { cn } from '@rs/tailwind-base'
import { Popover, PopoverContent, PopoverTrigger } from '@rs/ui-new/popover'
import { Pressable } from '@rs/ui-new/pressable'
import { useEffect, useRef, useState } from 'react'
import { DatabaseGlyph, type GlyphTone } from './DatabaseGlyph'
import { ago, ReadingActions, readingLine } from './MonitorHeartbeat'
import type { MonitorPulseState } from './useMonitorPulse'

export type HaloState = MonitorPulseState['state'] | 'off'

const RING_COLOR: Record<HaloState, string> = {
  live: 'var(--color-content-positive-plain)',
  ai_paused: 'var(--color-content-positive-plain)',
  late: 'var(--color-content-warning-plain)',
  connection_lost: 'var(--color-content-negative-plain)',
  paused: 'var(--color-content-layout-3)',
  off: 'var(--color-content-layout-3)',
  unknown: 'var(--color-content-layout-3)',
}

export function haloTitle(state: HaloState): string {
  switch (state) {
    case 'late':
      return 'Readings delayed'
    case 'connection_lost':
      return "Can't reach the database"
    case 'paused':
    case 'off':
      return 'Monitoring off'
    case 'unknown':
      return 'Connecting'
    default:
      return 'Monitoring'
  }
}

export interface PulseHaloProps {
  pulse: MonitorPulseState
  monitoringEnabled?: boolean
  qps?: number | null
  fill?: number | null
  tone?: GlyphTone
  /** Outer size in px (the glyph is 20px smaller). */
  size?: number
  startup?: boolean
  className?: string
}

export function PulseHalo({
  pulse,
  monitoringEnabled = true,
  qps = null,
  fill = null,
  tone = 'positive',
  size = 72,
  className,
  startup = false,
}: PulseHaloProps) {
  const [open, setOpen] = useState(false)
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const state: HaloState = monitoringEnabled
    ? pulse.firstReading
      ? 'unknown'
      : pulse.state
    : 'off'
  const live = state === 'live' || state === 'ai_paused'
  const { clock } = pulse
  const lastTs = clock.lastAt ?? pulse.last?.ts ?? null
  const cycle = useRingCycle(lastTs)
  const fraction = cycle.finishing
    ? 1
    : live && lastTs != null
      ? clock.progress
      : state === 'late' || state === 'connection_lost'
        ? 1
        : 0
  const waiting = live && clock.due && !cycle.finishing
  const color = RING_COLOR[state]
  const title =
    pulse.firstReading && monitoringEnabled
      ? 'Taking the first reading'
      : haloTitle(state)
  const lastLine = lastTs
    ? `Last reading ${ago(lastTs, pulse.now)}`
    : 'No reading yet'
  const line = readingLine(pulse, monitoringEnabled)
  const label = live ? `${title}. ${lastLine}. ${line}` : `${title}. ${line}`

  const stroke = 3.5
  const radius = size / 2 - stroke - 1
  const center = size / 2
  const circumference = 2 * Math.PI * radius
  const glyphTone: GlyphTone =
    state === 'connection_lost'
      ? 'negative'
      : state === 'late'
        ? 'warning'
        : live
          ? tone
          : 'neutral'

  const hover = (next: boolean) => {
    if (closeTimer.current) clearTimeout(closeTimer.current)
    if (next) setOpen(true)
    else closeTimer.current = setTimeout(() => setOpen(false), 120)
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Pressable
          aria-label={label}
          onPointerEnter={() => hover(true)}
          onPointerLeave={() => hover(false)}
          className={cn(
            'relative inline-flex shrink-0 cursor-default items-center justify-center rounded-full focus-visible:shadow-focus focus-visible:outline-none',
            className
          )}
          style={{ width: size, height: size }}
          data-testid="pulse-halo"
          data-state={state}
        >
          {startup && monitoringEnabled ? (
            <span
              aria-hidden="true"
              data-testid="startup-glow"
              className="absolute inset-2 rounded-full bg-white/10 blur-md motion-safe:animate-pulse"
            />
          ) : null}
          <svg
            width={size}
            height={size}
            viewBox={`0 0 ${size} ${size}`}
            aria-hidden="true"
            className={cn(
              'absolute inset-0 overflow-visible',
              startup && monitoringEnabled && 'hidden'
            )}
            data-testid="reading-ring"
          >
            <circle
              cx={center}
              cy={center}
              r={radius}
              fill="none"
              stroke="var(--color-border-layout-1)"
              strokeWidth={stroke}
              strokeDasharray={
                state === 'paused' || state === 'off' ? '1.5 5' : undefined
              }
              strokeLinecap="round"
            />
            {state !== 'paused' && state !== 'off' && state !== 'unknown' && (
              <g
                key={cycle.key ?? 0}
                data-progress={fraction.toFixed(3)}
                style={{
                  transform: 'rotate(-90deg)',
                  transformOrigin: 'center',
                }}
              >
                <circle
                  cx={center}
                  cy={center}
                  r={radius}
                  fill="none"
                  stroke={color}
                  strokeWidth={stroke}
                  strokeLinecap="round"
                  strokeDasharray={
                    state === 'connection_lost' ? '6 6' : circumference
                  }
                  strokeDashoffset={
                    state === 'connection_lost'
                      ? 0
                      : circumference * (1 - fraction)
                  }
                  className={cn(
                    'transition-[stroke-dashoffset] motion-reduce:transition-none',
                    cycle.finishing
                      ? 'duration-300 ease-out'
                      : 'duration-1000 ease-linear',
                    (state === 'late' || waiting) && 'mk-breathe'
                  )}
                  style={{ filter: `drop-shadow(0 0 3px ${color})` }}
                />
                {live && (
                  <g
                    style={{
                      transform: `rotate(${fraction * 360}deg)`,
                      transformOrigin: 'center',
                      transition: cycle.finishing
                        ? 'transform 300ms ease-out'
                        : 'transform 1s linear',
                    }}
                    className="motion-reduce:transition-none"
                  >
                    <circle
                      cx={center + radius}
                      cy={center}
                      r={stroke * 0.95}
                      fill={color}
                      style={{ filter: `drop-shadow(0 0 4px ${color})` }}
                    />
                  </g>
                )}
              </g>
            )}
            {live && pulse.beat > 0 && (
              <g key={`beat-${pulse.beat}`}>
                <circle
                  cx={center}
                  cy={center}
                  r={radius}
                  fill="none"
                  stroke={color}
                  className="mk-halo-flash"
                />
                <circle
                  cx={center}
                  cy={center}
                  r={radius}
                  fill="none"
                  stroke={color}
                  strokeWidth="2"
                  className="mk-halo-ripple"
                />
              </g>
            )}
          </svg>
          <DatabaseGlyph
            size={size - 20}
            round
            noTooltip
            qps={monitoringEnabled ? qps : null}
            fill={fill}
            tone={glyphTone}
            beat={pulse.beat}
            label={label}
          />
        </Pressable>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        className="min-w-0 px-3 py-2"
        data-testid="pulse-halo-line"
        onPointerEnter={() => hover(true)}
        onPointerLeave={() => hover(false)}
        onOpenAutoFocus={(event) => event.preventDefault()}
      >
        <p className="flex items-center gap-2 whitespace-nowrap text-body-small tabular-nums text-content-layout-1">
          <span
            aria-hidden="true"
            className="h-2 w-2 shrink-0 rounded-full"
            style={{ background: color }}
          />
          {line}
        </p>
        {monitoringEnabled && pulse.target && <ReadingActions pulse={pulse} />}
      </PopoverContent>
    </Popover>
  )
}

/** How long a ring that's cut short by an early reading takes to finish its fill. */
const FINISH_MS = 320

/**
 * The ring's cycle follows the last reading, but a new reading first lets the old
 * ring finish (FINISH_MS) and only then starts the next one from empty.
 */
function useRingCycle(lastAt: number | null) {
  const [cycle, setCycle] = useState({ key: lastAt, finishing: false })
  useEffect(() => {
    if (lastAt === cycle.key) return
    if (cycle.key == null) {
      setCycle({ key: lastAt, finishing: false })
      return
    }
    setCycle((current) => ({ ...current, finishing: true }))
    const timer = setTimeout(
      () => setCycle({ key: lastAt, finishing: false }),
      FINISH_MS
    )
    return () => clearTimeout(timer)
  }, [lastAt, cycle.key])
  return cycle
}
