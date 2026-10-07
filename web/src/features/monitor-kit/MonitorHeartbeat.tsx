// MonitorHeartbeat - "is RDST watching right now?" in one line (brief 3.1).
// A StatusRipple (label always set), the last reading time, and a 14px countdown
// ring to the next collector tick. Each live pulse sends one ring out of the dot.
// Hover or focus shows one line: when the next reading lands, or that it's late.
//
// Usage:
//   const pulse = useMonitorPulse(target)
//   <MonitorHeartbeat pulse={pulse} monitoringEnabled={settings.enabled}
//                     onCheckAgain={() => refetch()} />
//   <MonitorHeartbeat pulse={pulse} compact />   // dot + one short phrase
import { cn } from '@rs/tailwind-base'
import { Button } from '@rs/ui-new/button'
import { Popover, PopoverContent, PopoverTrigger } from '@rs/ui-new/popover'
import { Pressable } from '@rs/ui-new/pressable'
import { StatusRipple } from '@rs/ui-new/status'
import { useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { useRef, useState } from 'react'
import { collectNow } from '@/lib/monitor/api'
import { useConfigure } from '@/lib/useConfigure'
import { formatRelative } from './format'
import { UpdatePasswordDialog } from './UpdatePasswordDialog'
import type { MonitorPulseState } from './useMonitorPulse'

type HeartbeatState = MonitorPulseState['state']

export interface MonitorHeartbeatProps {
  pulse: MonitorPulseState
  /** False renders the "Monitoring off" state regardless of pulses. */
  monitoringEnabled?: boolean
  /** Shown as `Check again` in the connection-lost state. */
  onCheckAgain?: () => void
  /** Dot and one phrase only, no countdown or popover. */
  compact?: boolean
  className?: string
}

const RIPPLE_COLOR: Record<
  Exclude<HeartbeatState, 'paused' | 'unknown'>,
  'positive' | 'warning' | 'negative'
> = {
  live: 'positive',
  ai_paused: 'positive',
  late: 'warning',
  connection_lost: 'negative',
}

export function ago(ts: number | null | undefined, now: number) {
  if (!ts) return 'never'
  const delta = Math.max(0, now - ts)
  if (delta < 60) return `${Math.max(1, Math.floor(delta))} s ago`
  return formatRelative(ts, now)
}

function stateText(
  state: HeartbeatState,
  lastTs: number | null,
  now: number
): { title: string; detail: string | null } {
  switch (state) {
    case 'late':
      return { title: 'Readings delayed', detail: `last ${ago(lastTs, now)}` }
    case 'connection_lost':
      return {
        title: "Can't reach the database",
        detail: `last reading ${ago(lastTs, now)}`,
      }
    case 'paused':
      return { title: 'Monitoring off', detail: null }
    case 'unknown':
      return { title: 'Connecting', detail: null }
    default:
      return {
        title: 'Monitoring',
        detail: lastTs
          ? `reading ${ago(lastTs, now)}`
          : 'waiting for the first reading',
      }
  }
}

/**
 * The heartbeat's hover line: 'Next reading in 23 s' while readings arrive on time,
 * or one line saying they're late or failing.
 */
export function pulseLine(
  state: HeartbeatState | 'off',
  lastTs: number | null,
  nextAt: number | null,
  now: number
): string {
  const last = lastTs ? `last reading ${ago(lastTs, now)}` : 'no reading yet'
  switch (state) {
    case 'late':
      return `${capitalize(last)}, retrying`
    case 'connection_lost':
      return `Can't reach the database, ${last}`
    case 'paused':
    case 'off':
      return 'Monitoring is off'
    case 'unknown':
      return 'Connecting'
    default: {
      if (!lastTs) return 'Waiting for the first reading'
      if (nextAt == null) return capitalize(last)
      const seconds = Math.max(0, Math.round(nextAt - now))
      if (seconds === 0) return 'Next reading any second'
      if (seconds < 60) return `Next reading in ${seconds} s`
      return `Next reading in ${Math.round(seconds / 60)} min`
    }
  }
}

function capitalize(text: string) {
  return text.charAt(0).toUpperCase() + text.slice(1)
}

export function readingLine(pulse: MonitorPulseState, enabled = true): string {
  if (!enabled) return 'Monitoring is off'
  if (pulse.firstReading) return 'Taking the first reading'
  const line = pulseLine(
    pulse.state,
    pulse.clock.lastAt,
    pulse.clock.nextAt,
    pulse.now
  )
  return pulse.delayReason ? `${line}. ${pulse.delayReason}` : line
}

export function ReadingActions({ pulse }: { pulse: MonitorPulseState }) {
  const client = useQueryClient()
  const connection = useConfigure()
  const [checking, setChecking] = useState(false)
  const [result, setResult] = useState<string | null>(null)
  // One manual reading per 30 s, so Check now can't be spammed.
  const [coolUntil, setCoolUntil] = useState(0)
  const [passwordOpen, setPasswordOpen] = useState(false)
  const navigate = useNavigate()
  const target = pulse.target
  if (!target) return null
  const delayed =
    !pulse.firstReading &&
    (pulse.state === 'late' || pulse.state === 'connection_lost')
  const prolonged =
    delayed &&
    pulse.clock.lastAt != null &&
    pulse.now - pulse.clock.lastAt > 300
  if (!delayed && !pulse.setupBlocked && !result && !checking) return null
  const check = async () => {
    setChecking(true)
    setResult(null)
    try {
      const reading = await collectNow(target)
      setResult(reading.detail)
      await client.invalidateQueries({ queryKey: ['monitor', target] })
    } catch {
      setResult("The reading didn't finish. Try again.")
    } finally {
      setChecking(false)
      setCoolUntil(Date.now() + 30_000)
    }
  }
  const test = async () => {
    const tested = await connection.testConnection(target)
    setResult(
      tested?.connected ? 'Connection works' : "Can't connect to the database"
    )
  }
  return (
    <div className="flex flex-wrap items-center gap-2 text-body-small">
      {delayed && (
        <Button
          label={checking ? 'Checking now' : 'Check now'}
          modifier="outline"
          size="small"
          disabled={checking || Date.now() < coolUntil}
          onClick={check}
        />
      )}
      {prolonged && (
        <>
          <Button
            label={
              connection.loading ? 'Testing connection' : 'Test connection'
            }
            modifier="ghost"
            size="small"
            disabled={connection.loading}
            onClick={test}
          />
          <a
            className="text-content-link underline"
            href={`/configure?panel=connections&edit=${encodeURIComponent(target)}`}
          >
            Connection settings
          </a>
        </>
      )}
      {pulse.setupBlocked && (
        <Button
          label="Finish setup"
          modifier="outline"
          size="small"
          onClick={() =>
            void navigate({ to: '/database', search: { panel: 'setup' } })
          }
        />
      )}
      {pulse.state === 'connection_lost' && (
        <Button
          label="Update password"
          modifier="ghost"
          size="small"
          onClick={() => setPasswordOpen(true)}
        />
      )}
      {result && <output aria-live="polite">{result}</output>}
      <UpdatePasswordDialog
        target={target}
        open={passwordOpen}
        onClose={() => setPasswordOpen(false)}
      />
    </div>
  )
}

/** 14px ring that empties toward the next reading, on the app's one reading timer. */
function CountdownRing({ progress }: { progress: number }) {
  const fraction = 1 - progress
  const r = 5.5
  const circumference = 2 * Math.PI * r
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 14 14"
      aria-hidden="true"
      className="-rotate-90"
    >
      <circle
        cx="7"
        cy="7"
        r={r}
        fill="none"
        stroke="var(--color-border-layout-2)"
        strokeWidth="1.8"
      />
      <circle
        cx="7"
        cy="7"
        r={r}
        fill="none"
        stroke="var(--color-content-layout-2)"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeDasharray={circumference}
        strokeDashoffset={circumference * (1 - fraction)}
        className="transition-[stroke-dashoffset] duration-slowest ease-linear motion-reduce:transition-none"
      />
    </svg>
  )
}

export function MonitorHeartbeat({
  pulse,
  monitoringEnabled = true,
  onCheckAgain,
  compact = false,
  className,
}: MonitorHeartbeatProps) {
  const [open, setOpen] = useState(false)
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const state: HeartbeatState = monitoringEnabled
    ? pulse.firstReading
      ? 'unknown'
      : pulse.state
    : 'paused'
  const lastTs = pulse.clock.lastAt ?? pulse.last?.ts ?? null
  const { title, detail } =
    pulse.firstReading && monitoringEnabled
      ? { title: 'Taking the first reading', detail: pulse.delayReason ?? null }
      : state === 'connection_lost' && pulse.connectionProblem
        ? {
            title: "Can't reach the database",
            detail: pulse.connectionProblem.reason,
          }
        : stateText(state, lastTs, pulse.now)
  const label = detail ? `${title} - ${detail}` : title
  const line = readingLine(pulse, monitoringEnabled)
  const nextIn = pulse.clock.remaining

  const ring =
    pulse.beat > 0 ? (
      <svg
        key={pulse.beat}
        className="pointer-events-none absolute top-1/2 left-0 -mt-2 overflow-visible"
        width="16"
        height="16"
        viewBox="0 0 16 16"
        aria-hidden="true"
      >
        <circle
          cx="8"
          cy="8"
          r="4"
          fill="none"
          stroke="var(--color-content-positive-plain)"
          strokeWidth="1.5"
          className="mk-ring"
        />
      </svg>
    ) : null

  const status =
    state === 'paused' || state === 'unknown' ? (
      <span className="inline-flex items-center gap-1">
        <span className="inline-flex h-4 w-4 items-center justify-center">
          <span className="h-2 w-2 rounded-full border-2 border-border-layout-2" />
        </span>
        <span className="text-label-small text-content-layout-2">{title}</span>
      </span>
    ) : (
      <span className="relative inline-flex items-center">
        <StatusRipple color={RIPPLE_COLOR[state]} label={title} />
        {ring}
      </span>
    )

  if (compact)
    return (
      <output
        className={cn('inline-flex items-center gap-1.5', className)}
        aria-label={label}
      >
        {status}
        {detail && (
          <span className="text-label-small text-content-layout-2">
            {detail}
          </span>
        )}
      </output>
    )

  const hover = (next: boolean) => {
    if (closeTimer.current) clearTimeout(closeTimer.current)
    if (next) setOpen(true)
    else closeTimer.current = setTimeout(() => setOpen(false), 120)
  }

  return (
    <div
      className={cn('flex flex-wrap items-center gap-x-3 gap-y-1', className)}
    >
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Pressable
            aria-label={`${label}. ${line}`}
            onPointerEnter={() => hover(true)}
            onPointerLeave={() => hover(false)}
            className="-mx-1.5 inline-flex cursor-default items-center gap-1.5 rounded-control px-1.5 py-0.5 transition-colors duration-fast ease-base hover:bg-surface-layout-2 focus-visible:shadow-focus focus-visible:outline-none"
          >
            {status}
            {detail && (
              <span className="text-label-small text-content-layout-2 tabular-nums">
                {detail}
              </span>
            )}
            {(state === 'live' || state === 'ai_paused') && nextIn != null && (
              <span className="hidden items-center gap-1 text-label-small text-content-layout-3 tabular-nums tablet:inline-flex">
                <CountdownRing progress={pulse.clock.progress} />
                {nextIn > 0 ? `next in ${nextIn} s` : 'next reading due'}
              </span>
            )}
          </Pressable>
        </PopoverTrigger>
        <PopoverContent
          align="start"
          className="min-w-0 px-3 py-2"
          onPointerEnter={() => hover(true)}
          onPointerLeave={() => hover(false)}
          onOpenAutoFocus={(event) => event.preventDefault()}
        >
          <p className="max-w-xs text-body-small tabular-nums text-content-layout-1">
            {line}
          </p>
        </PopoverContent>
      </Popover>
      {monitoringEnabled && pulse.target && <ReadingActions pulse={pulse} />}
      {state === 'connection_lost' && onCheckAgain && !pulse.target && (
        <Button
          label="Check now"
          modifier="outline"
          size="small"
          onClick={onCheckAgain}
        />
      )}
    </div>
  )
}
