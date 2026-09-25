import { describe, expect, it } from 'vitest'
import { FIXTURE_NOW, historySpan } from '@/lib/monitor/fixtures'
import {
  availableRanges,
  chartTitle,
  clampRange,
  domainFor,
  fetchRange,
  historyLine,
  LIVE_RANGES,
  type LiveRange,
  priorCovered,
  resolutionFor,
  ticksFor,
} from './rangeModel'

const HOUR = 3600
const DAY = 86400
const localdev = historySpan('localdev', FIXTURE_NOW) // 2 days 19 hours
const firstHour = historySpan('first_hour', FIXTURE_NOW) // 42 minutes
const longHistory = historySpan('long_history', FIXTURE_NOW) // 95 days
const empty = historySpan('empty', FIXTURE_NOW)

const enabled = (history: typeof localdev) =>
  availableRanges(history)
    .filter((option) => option.enabled)
    .map((option) => option.range)

describe('availableRanges', () => {
  it('enables a range once history exceeds the next smaller one', () => {
    expect(enabled(localdev)).toEqual(['1h', '24h', '7d', 'all'])
    expect(enabled(firstHour)).toEqual(['1h'])
    expect(enabled(longHistory)).toEqual([
      '1h',
      '24h',
      '7d',
      '30d',
      '90d',
      'all',
    ])
    expect(enabled(empty)).toEqual(['1h'])
  })

  it('explains disabled ranges and labels All with the span', () => {
    const options = availableRanges(localdev)
    expect(options.find((o) => o.range === '30d')?.reason).toBe(
      '2 days recorded. Needs more than 7 days.'
    )
    expect(options.find((o) => o.range === 'all')?.label).toBe('All - 2 days')
  })
})

describe('live ranges', () => {
  it('offers 5m and 15m first and reads the 1h series for them', () => {
    const options = availableRanges(firstHour, LIVE_RANGES)
    expect(options.slice(0, 3).map((o) => o.range)).toEqual(['5m', '15m', '1h'])
    expect(options[0].enabled).toBe(true)
    expect(fetchRange('5m')).toBe('1h')
    expect(fetchRange('15m')).toBe('1h')
    expect(fetchRange('24h')).toBe('24h')
    expect(clampRange<LiveRange>('90d', firstHour, LIVE_RANGES)).toBe('1h')
    const [start, end] = domainFor('5m', localdev, FIXTURE_NOW)
    expect(end - start).toBe(300)
  })
})

describe('clampRange', () => {
  it('keeps enabled ranges and clamps to the largest enabled one below', () => {
    expect(clampRange('24h', localdev)).toBe('24h')
    expect(clampRange('90d', localdev)).toBe('7d')
    expect(clampRange('90d', firstHour)).toBe('1h')
  })
})

describe('domainFor', () => {
  it('never pads before the first reading', () => {
    expect(domainFor('7d', localdev, FIXTURE_NOW)).toEqual([
      localdev.first_ts,
      FIXTURE_NOW,
    ])
    expect(domainFor('24h', localdev, FIXTURE_NOW)).toEqual([
      FIXTURE_NOW - DAY,
      FIXTURE_NOW,
    ])
  })
})

describe('resolutionFor', () => {
  it('follows the brief table and caps All at 1,000 points', () => {
    expect(resolutionFor('1h')).toBe(60)
    expect(resolutionFor('7d')).toBe(300)
    expect(resolutionFor('90d')).toBe(3600)
    expect(resolutionFor('all', 2 * DAY)).toBe(300)
    expect(resolutionFor('all', 95 * DAY)).toBe(86400)
  })
})

describe('priorCovered', () => {
  it('needs twice the range of history', () => {
    expect(priorCovered('24h', localdev)).toBe(true)
    expect(priorCovered('7d', localdev)).toBe(false)
    expect(priorCovered('all', longHistory)).toBe(false)
  })
})

describe('ticksFor', () => {
  it('labels 4 to 8 ticks inside the domain', () => {
    const day = ticksFor([FIXTURE_NOW - DAY, FIXTURE_NOW], 6)
    expect(day.length).toBeGreaterThanOrEqual(4)
    expect(day.length).toBeLessThanOrEqual(6)
    for (const tick of day) {
      expect(tick.x).toBeGreaterThanOrEqual(FIXTURE_NOW - DAY)
      expect(tick.x).toBeLessThanOrEqual(FIXTURE_NOW)
      expect(tick.label).not.toMatch(/start|now/i)
    }
    const hour = ticksFor([FIXTURE_NOW - HOUR, FIXTURE_NOW], 6)
    expect(hour.length).toBeGreaterThanOrEqual(4)
  })
})

describe('copy', () => {
  it('says what is shown when history is shorter than the range', () => {
    expect(chartTitle('Queries per second', '24h', localdev)).toBe(
      'Queries per second, last 24 hours'
    )
    expect(chartTitle('Queries per second', '7d', localdev)).toMatch(
      /^Queries per second, last 7 days \(history starts \w{3}, \w{3} \d+\)$/
    )
    expect(chartTitle('Queries per second', '1h', firstHour)).toBe(
      'Queries per second, last 42 minutes'
    )
    expect(historyLine(firstHour)).toBe('Recording for 42 minutes')
    expect(historyLine(localdev)).toMatch(/^2 days of history, since /)
    expect(historyLine(empty)).toBeNull()
  })
})
