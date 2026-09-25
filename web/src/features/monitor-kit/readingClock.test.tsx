import { act, render, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { noteServerDate, resetServerClock, serverOffset } from '@/lib/monitor/serverClock'
import { PulseHalo } from './PulseHalo'
import { type MonitorPulseState, nextReadingAt, readingClock, useNow } from './useMonitorPulse'

beforeEach(() => resetServerClock())
afterEach(() => vi.useRealTimers())

it('measures progress between the last reading and the next', () => {
  expect(readingClock(1000, 1060, 1030)).toMatchObject({
    period: 60,
    progress: 0.5,
    remaining: 30,
    due: false,
  })
  // Late: full and due, never past full.
  expect(readingClock(1000, 1060, 1090)).toMatchObject({ progress: 1, due: true })
  expect(readingClock(null, null, 5)).toMatchObject({ progress: 0, remaining: null })
})

it('ticks every view on one shared clock', () => {
  vi.useFakeTimers()
  const a = renderHook(() => useNow())
  const b = renderHook(() => useNow())
  act(() => vi.advanceTimersByTime(3000))
  expect(a.result.current).toBe(b.result.current)
})

it('follows the backend clock when the browser clock is off by more than noise', () => {
  const local = Date.parse('2026-10-01T20:00:00Z')
  noteServerDate('Thu, 01 Oct 2026 20:00:20 GMT', local)
  expect(serverOffset()).toBeGreaterThanOrEqual(20)
  expect(serverOffset()).toBeLessThan(21)
  resetServerClock()
  noteServerDate('Thu, 01 Oct 2026 20:00:00 GMT', local + 400)
  expect(serverOffset()).toBe(0)
})

function pulse(lastAt: number, nextAt: number, now: number): MonitorPulseState {
  const collect = {
    ts: lastAt,
    kind: 'collect' as const,
    ok: true,
    detail: '',
    next_collect_at: nextAt,
    state: 'live' as const,
    last_reading_at: lastAt,
    next_reading_at: nextAt,
  }
  return {
    pulses: [collect],
    last: collect,
    lastCollect: collect,
    state: 'live',
    nextAt,
    now,
    clock: readingClock(lastAt, nextAt, now),
    beat: 1,
    isLoading: false,
  }
}

const progress = (container: HTMLElement) =>
  Number(container.querySelector('[data-progress]')?.getAttribute('data-progress'))

it('finishes the ring when a reading lands early, then starts again', () => {
  vi.useFakeTimers()
  const { container, rerender } = render(<PulseHalo pulse={pulse(1000, 1060, 1030)} />)
  expect(progress(container)).toBeCloseTo(0.5)
  // The next reading lands halfway: the old ring completes first.
  rerender(<PulseHalo pulse={pulse(1030, 1090, 1030)} />)
  expect(progress(container)).toBe(1)
  act(() => vi.advanceTimersByTime(400))
  expect(progress(container)).toBe(0)
})

it('prefers the scheduled reading time and falls back to the estimate', () => {
  const reading = pulse(1000, 1060, 1005).last!
  expect(nextReadingAt({ ...reading, next_reading_at: 1010 })).toBe(1010)
  expect(nextReadingAt({ ...reading, next_reading_at: undefined })).toBe(1060)
  expect(nextReadingAt({ ...reading, state: 'paused', next_reading_at: null })).toBeNull()
})
