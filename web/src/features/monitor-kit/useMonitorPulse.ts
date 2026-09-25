// useMonitorPulse - the live heartbeat of one target (brief 3.1, 5.2).
//
// Usage:
//   const pulse = useMonitorPulse(target)
//   <MonitorHeartbeat pulse={pulse} />
//   <DatabaseGlyph beat={pulse.beat} ... />
// Seeds from GET /pulse?limit=5, then appends each `pulse` event from the target's
// stream. Every collector pulse refetches that target's live readings and reports so
// the page moves with the real tick. `now` comes from one app-wide clock that ticks once
// a second, and `clock` is the one reading timer every "next reading" animation draws
// from: the backend's last and next reading times.

import { type QueryClient, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState, useSyncExternalStore } from 'react'
import { subscribePulse } from '@/lib/monitor/api'
import { usePulses } from '@/lib/monitor/hooks'
import { monitorKeys } from '@/lib/monitor/keys'
import { serverOffset } from '@/lib/monitor/serverClock'
import type { MonitorLiveState, MonitorPulse } from '@/lib/monitor/types'

export interface MonitorPulseState {
  target?: string | null
  firstReading?: boolean
  delayReason?: string | null
  /** A setup problem blocks readings; the page offers Finish setup. */
  setupBlocked?: boolean
  /** Newest first, at most 10. */
  pulses: MonitorPulse[]
  /** Latest pulse of any kind. */
  last: MonitorPulse | null
  /** Latest collector reading. */
  lastCollect: MonitorPulse | null
  /** Server state, upgraded to 'late' when more than two ticks were missed. */
  state: MonitorLiveState | 'unknown'
  /** Unix seconds of the next expected collector tick. */
  nextAt: number | null
  /** Unix seconds on the backend's clock, ticking every second on the app-wide clock. */
  now: number
  /** The reading timer: where this target is between its last and next reading. */
  clock: ReadingClock
  /** Increments on every pulse received live (drives one-shot animations). */
  beat: number
  isLoading: boolean
}

export function nextReadingAt(pulse: MonitorPulse | null): number | null {
  if (!pulse || pulse.state === 'paused') return null
  return pulse.next_reading_at ?? pulse.next_collect_at
}

const TICK_SECONDS = 60

export interface ReadingClock {
  /** Unix seconds of the last collector reading, from the backend. */
  lastAt: number | null
  /** Unix seconds the backend expects the next reading. */
  nextAt: number | null
  /** Seconds between the two (60 when unknown). */
  period: number
  /** 0 right after a reading, 1 once the next one is due. */
  progress: number
  /** Whole seconds until the next reading, or null when unknown. */
  remaining: number | null
  /** The next reading is due and hasn't landed yet. */
  due: boolean
}

/** Where `now` sits between the last reading and the next one. */
export function readingClock(
  lastAt: number | null,
  nextAt: number | null,
  now: number
): ReadingClock {
  const period =
    lastAt != null && nextAt != null && nextAt > lastAt ? nextAt - lastAt : 60
  const progress =
    lastAt == null ? 0 : Math.min(1, Math.max(0, (now - lastAt) / period))
  return {
    lastAt,
    nextAt,
    period,
    progress,
    remaining: nextAt == null ? null : Math.max(0, Math.round(nextAt - now)),
    due: nextAt != null && now >= nextAt,
  }
}

// One clock for the whole app, so every countdown and ring moves on the same second.
const clockListeners = new Set<() => void>()
let clockNow = Date.now() / 1000
let clockTimer: ReturnType<typeof setInterval> | null = null

function subscribeClock(listener: () => void) {
  clockListeners.add(listener)
  if (!clockTimer) {
    clockNow = Date.now() / 1000
    clockTimer = setInterval(() => {
      clockNow = Date.now() / 1000
      for (const notify of clockListeners) notify()
    }, 1000)
  }
  return () => {
    clockListeners.delete(listener)
    if (!clockListeners.size && clockTimer) {
      clearInterval(clockTimer)
      clockTimer = null
    }
  }
}

/** Unix seconds, ticking once a second on the app-wide clock. */
export function useNow(): number {
  return useSyncExternalStore(subscribeClock, () => clockNow)
}

// Several views can follow one target; each pulse refreshes its readings once.
const refreshed = new WeakMap<QueryClient, Map<string, string>>()

function refreshOnce(client: QueryClient, target: string, pulse: MonitorPulse) {
  const seen = refreshed.get(client) ?? new Map<string, string>()
  refreshed.set(client, seen)
  const id = `${pulse.kind}:${pulse.ts}`
  const key = `${target}:${pulse.kind}`
  if (seen.get(key) === id) return
  seen.set(key, id)
  if (pulse.kind === 'collect') {
    for (const part of ['health', 'queries', 'series', 'query', 'load'])
      void client.invalidateQueries({ queryKey: ['monitor', target, part] })
  }
  // New readings queue analyses, and finished ones change rows and the queue.
  if (pulse.kind === 'collect' || pulse.kind === 'analyze')
    void client.invalidateQueries({ queryKey: monitorKeys.analyzeJobs(target) })
  if (pulse.kind === 'analyze')
    void client.invalidateQueries({ queryKey: ['monitor', target, 'queries'] })
}

export function useMonitorPulse(target: string | null): MonitorPulseState {
  const client = useQueryClient()
  const seed = usePulses(target ?? '')
  const [live, setLive] = useState<MonitorPulse[]>([])
  const [beat, setBeat] = useState(0)
  // Reading times are the backend's, so countdowns run on the backend's clock.
  const now = useNow() + serverOffset()

  useEffect(() => {
    setLive([])
    if (!target) return
    return subscribePulse(target, (pulse) => {
      setLive((previous) => [pulse, ...previous].slice(0, 10))
      setBeat((value) => value + 1)
      refreshOnce(client, target, pulse)
    })
  }, [client, target])

  useEffect(() => {
    if (!target) return
    const collect = seed.data?.pulses.find((pulse) => pulse.kind === 'collect')
    if (collect) refreshOnce(client, target, collect)
  }, [client, target, seed.data])

  return useMemo(() => {
    const merged = [...live, ...(seed.data?.pulses ?? [])]
      .sort((a, b) => b.ts - a.ts)
      .filter(
        (pulse, index, list) =>
          index === 0 ||
          pulse.ts !== list[index - 1].ts ||
          pulse.kind !== list[index - 1].kind
      )
      .slice(0, 10)
    const last = merged[0] ?? null
    const lastCollect = merged.find((pulse) => pulse.kind === 'collect') ?? null
    const nextAt = live.length
      ? nextReadingAt(last)
      : seed.data?.next_reading_at !== undefined
        ? seed.data.next_reading_at
        : nextReadingAt(last)
    const lastAt =
      last?.last_reading_at ??
      seed.data?.last_reading_at ??
      lastCollect?.ts ??
      null
    const firstReading =
      seed.data?.first_reading ?? last?.first_reading ?? false
    const delayReason = seed.data?.delay_reason ?? last?.delay_reason ?? null
    const setupBlocked = Boolean(
      seed.data?.setup_blocked ?? last?.setup_blocked
    )
    let state: MonitorPulseState['state'] = last?.state ?? 'unknown'
    if (
      !firstReading &&
      (state === 'live' || state === 'ai_paused') &&
      nextAt != null &&
      now > nextAt + 2 * TICK_SECONDS
    )
      state = 'late'
    return {
      target,
      firstReading,
      delayReason,
      setupBlocked,
      pulses: merged,
      last,
      lastCollect,
      state,
      nextAt,
      now,
      clock: readingClock(lastAt, nextAt, now),
      beat,
      isLoading: Boolean(target) && seed.isPending,
    }
  }, [live, seed.data, seed.isPending, now, beat, target])
}
