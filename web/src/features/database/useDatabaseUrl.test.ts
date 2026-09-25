import { describe, expect, it } from 'vitest'
import { fitZoom, frameLabel, MIN_ZOOM, windowLabel } from './useDatabaseUrl'

const NOW = 1_790_720_100

describe('fitZoom', () => {
  it('widens a short window to five minutes around its middle', () => {
    const [start, end] = fitZoom([NOW - 3600, NOW - 3600 + 60], NOW, null)
    expect(end - start).toBeGreaterThanOrEqual(MIN_ZOOM)
    expect(start).toBeLessThanOrEqual(NOW - 3600)
    expect(end).toBeGreaterThanOrEqual(NOW - 3600 + 60)
  })

  it('keeps the window inside the recorded history', () => {
    expect(fitZoom([NOW - 60, NOW + 600], NOW, null)[1]).toBe(NOW)
    const first = NOW - 7200
    expect(fitZoom([first - 600, first - 400], NOW, first)[0]).toBe(first)
  })

  it('keeps minute boundaries for an older Inbox incident', () => {
    const start = NOW - 8 * 86400 + 75
    const [from, to] = fitZoom([start, start + 300], NOW, null)
    expect(from % 60).toBe(0)
    expect(to % 60).toBe(0)
    expect(from).toBeLessThanOrEqual(start)
    expect(to).toBeGreaterThanOrEqual(start + 300)
  })
})

describe('windowLabel', () => {
  it('names the day for a window before today', () => {
    const now = new Date(2026, 8, 29, 16, 0).getTime() / 1000
    const start = new Date(2026, 8, 26, 4, 15).getTime() / 1000
    expect(windowLabel([start, start + 600], now)).toBe(
      'Sat, Sep 26, 4:15 AM to 4:25 AM'
    )
    const today = new Date(2026, 8, 29, 14, 10).getTime() / 1000
    expect(windowLabel([today, today + 900], now)).toBe('2:10 PM to 2:25 PM')
  })
})

describe('frameLabel', () => {
  // An Inbox link opens an hour before the change (owner round 9: the chip said 10:37 PM
  // while the query's change and its shading began at 11:37 PM).
  const now = new Date(2026, 9, 1, 8, 38).getTime() / 1000
  const onset = new Date(2026, 8, 30, 23, 37).getTime() / 1000
  const domain: [number, number] = [onset - 3600, now]

  it('names the change itself, not the hour of context before it', () => {
    expect(frameLabel(domain, { start: onset, end: null }, now)).toBe(
      'Wed, Sep 30, 11:37 PM to Thu, Oct 1, 8:38 AM, shaded'
    )
    expect(frameLabel(domain, { start: onset, end: onset + 900 }, now)).toBe(
      'Wed, Sep 30, 11:37 PM to 11:52 PM, shaded'
    )
  })

  it('names the window when there is no change inside it', () => {
    expect(frameLabel(domain, null, now)).toBe(windowLabel(domain, now))
    expect(frameLabel(domain, { start: now + 60, end: null }, now)).toBe(
      windowLabel(domain, now)
    )
  })
})
