import { describe, expect, it } from 'vitest'
import {
  clipToDomain,
  downsample,
  pointsInView,
  segmentPoints,
  yDomain,
} from './chartMath'

describe('segmentPoints', () => {
  it('breaks the line at nulls and records long gaps', () => {
    const points = [0, 60, 120, 180, 240, 300, 360, 420, 480].map((x) => ({
      x,
      y: x >= 180 && x <= 360 ? null : 1,
    }))
    const { runs, gaps } = segmentPoints(points, 60)
    expect(runs).toHaveLength(2)
    expect(gaps).toEqual([{ from: 120, to: 420 }])
  })

  it('treats a missing bucket as a break without a gap label', () => {
    const { runs, gaps } = segmentPoints(
      [
        { x: 0, y: 1 },
        { x: 60, y: 1 },
        { x: 180, y: 1 },
      ],
      60
    )
    expect(runs).toHaveLength(2)
    expect(gaps).toEqual([])
  })
})

describe('yDomain', () => {
  it('uses a zero-based nice max by default', () => {
    expect(yDomain([3, 180]).max).toBe(200)
    expect(yDomain([3, 65]).max).toBe(80)
    expect(yDomain([0.13, 0.31]).max).toBe(0.4)
  })

  it('fits a tight window for ratios and respects the cap', () => {
    const domain = yDomain([0.985, 0.9999], { min: 'auto', cap: 1 })
    expect(domain.min).toBeLessThanOrEqual(0.985)
    expect(domain.max).toBeLessThanOrEqual(1)
    expect(domain.max).toBeGreaterThanOrEqual(0.9999)
  })
})

describe('clipToDomain', () => {
  // A week of five-minute readings shown in a ten-hour time frame, on a narrow chart.
  const HOUR = 3600
  const weekStart = 1_790_000_000
  const week = Array.from({ length: 7 * 288 }, (_, index) => ({
    x: weekStart + index * 300,
    y: index,
  }))
  const from = weekStart + 6 * 24 * HOUR + 37 * 60
  const domain: [number, number] = [from, from + 10 * HOUR + 60]

  it('starts the line within one reading of the time frame', () => {
    const shown = downsample(clipToDomain(week, domain, 300), 150)
    const first = shown.find((point) => point.x >= domain[0])
    expect(first).toBeDefined()
    expect((first as { x: number }).x - domain[0]).toBeLessThanOrEqual(300)
    expect(shown[0].x).toBeLessThan(domain[0])
  })

  it('keeps full resolution inside the time frame', () => {
    const shown = downsample(clipToDomain(week, domain, 300), 150)
    expect(
      shown.every(
        (point, index) => !index || point.x - shown[index - 1].x === 300
      )
    ).toBe(true)
  })

  it('downsampling the whole week first loses most of an hour at the left edge', () => {
    const shown = downsample(week, 150)
    const first = shown.find((point) => point.x >= domain[0]) as { x: number }
    expect(first.x - domain[0]).toBeGreaterThan(300)
  })

  it('leaves points alone without a domain', () => {
    expect(clipToDomain(week, undefined)).toBe(week)
  })
})

describe('downsample', () => {
  it('averages buckets and keeps all-null buckets as gaps', () => {
    const points = [1, 3, null, null, 5, 7].map((y, x) => ({ x, y }))
    expect(downsample(points, 3)).toEqual([
      { x: 1, y: 2 },
      { x: 3, y: null },
      { x: 5, y: 6 },
    ])
  })
})

describe('pointsInView', () => {
  // A first window from 2:56:40 for ten minutes, minute buckets on the clock.
  const from = 1_790_000_000 + 40
  const domain: [number, number] = [from, from + 600]

  it('draws a lone reading on the left edge, as one point', () => {
    expect(pointsInView([from - 40], domain, 60)).toEqual([
      { x: from, index: 0 },
    ])
  })

  it('draws two readings as the line between them, nothing beyond the edge', () => {
    expect(pointsInView([from - 40, from + 20], domain, 60)).toEqual([
      { x: from, index: 0 },
      { x: from + 20, index: 1 },
    ])
  })

  it('drops readings over a step outside and keeps one past the right edge', () => {
    const xs = [from - 100, from - 40, from + 560, from + 620, from + 680]
    expect(pointsInView(xs, domain, 60)).toEqual([
      { x: from, index: 1 },
      { x: from + 560, index: 2 },
      { x: from + 620, index: 3 },
    ])
  })
})
