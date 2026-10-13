import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import type { HomeSparkPoint } from '../../lib/monitor/types'
import { DayChart, dayChartModel } from './DayChart'

const MIDNIGHT = new Date(2026, 9, 8, 0, 0).getTime() / 1000
const NOW = MIDNIGHT + 9 * 3600 + 56 * 60
const RES = 300

function readings(from: number, to: number): HomeSparkPoint[] {
  const out: HomeSparkPoint[] = []
  for (let ts = from; ts <= to; ts += RES) out.push({ ts, qps: 10 })
  return out
}

afterEach(cleanup)

describe('dayChartModel', () => {
  it('spans the day with no pauses when readings cover it', () => {
    const model = dayChartModel(readings(MIDNIGHT, NOW), NOW, MIDNIGHT)
    expect(model.short).toBe(false)
    expect(model.domain).toEqual([MIDNIGHT, NOW])
    expect(model.runs).toHaveLength(1)
    expect(model.pauses).toEqual([])
    expect(model.collecting).toBe(false)
    expect(model.ticks[0].label).toBe('12 AM')
    expect(model.ticks.map((tick) => tick.label)).toContain('3 AM')
  })

  it('narrows to the recorded window when readings cover less than half the day', () => {
    const since = NOW - 40 * 60
    const model = dayChartModel(readings(since, NOW), NOW, MIDNIGHT)
    expect(model.short).toBe(true)
    expect(model.since).toBe(since)
    expect(model.domain).toEqual([since, NOW])
    expect(model.pauses).toEqual([])
    for (const tick of model.ticks) {
      expect(tick.x).toBeGreaterThanOrEqual(since)
      expect(tick.x).toBeLessThanOrEqual(NOW)
    }
  })

  it('draws a pause between fragments and breaks the line there', () => {
    const series = [
      ...readings(MIDNIGHT, MIDNIGHT + 3 * 3600),
      ...readings(MIDNIGHT + 7 * 3600, NOW),
    ]
    const model = dayChartModel(series, NOW, MIDNIGHT)
    expect(model.short).toBe(false)
    expect(model.runs).toHaveLength(2)
    expect(model.pauses).toEqual([
      {
        from: MIDNIGHT + 3 * 3600 + RES,
        to: MIDNIGHT + 7 * 3600,
        cause: 'unknown',
        ongoing: false,
      },
    ])
  })

  it('marks the lead-in and an ongoing gap, and a lone reading as collecting', () => {
    const model = dayChartModel(
      readings(MIDNIGHT + 2 * 3600, MIDNIGHT + 6 * 3600),
      NOW,
      MIDNIGHT
    )
    expect(model.pauses.map((pause) => [pause.from, pause.ongoing])).toEqual([
      [MIDNIGHT, false],
      [MIDNIGHT + 6 * 3600 + RES, true],
    ])
    const lone = dayChartModel([{ ts: NOW - RES, qps: 4 }], NOW, MIDNIGHT)
    expect(lone.collecting).toBe(true)
    expect(lone.runs).toHaveLength(1)
  })
})

describe('DayChart', () => {
  it('draws the line over its fill, the pause bands and the axis, with no y labels', () => {
    const series = [
      ...readings(MIDNIGHT, MIDNIGHT + 3 * 3600),
      ...readings(MIDNIGHT + 7 * 3600, NOW),
    ]
    const { container } = render(
      <DayChart
        model={dayChartModel(series, NOW, MIDNIGHT)}
        ariaLabel="Queries per second since midnight"
      />
    )
    const svg = container.querySelector('svg') as SVGSVGElement
    expect(svg.getAttribute('data-window')).toBe('day')
    expect(
      container.querySelectorAll('[data-testid="home-day-chart-run"]')
    ).toHaveLength(2)
    expect(
      container.querySelectorAll('[data-testid="chart-pause"]')
    ).toHaveLength(1)
    expect(container.querySelector('[fill-opacity="0.1"]')).toBeTruthy()
    const labels = [...container.querySelectorAll('svg text')].map(
      (text) => text.textContent
    )
    expect(labels).toContain('12 AM')
    expect(labels.some((label) => /^\d$/.test(label ?? ''))).toBe(false)
  })

  it('shows the collecting dash for a single reading', () => {
    const { container } = render(
      <DayChart
        model={dayChartModel([{ ts: NOW - RES, qps: 4 }], NOW, MIDNIGHT)}
        ariaLabel="Queries per second since midnight"
      />
    )
    expect(
      container.querySelector('[data-testid="home-day-chart-collecting"]')
    ).toBeTruthy()
    expect(container.querySelector('circle')).toBeNull()
  })
})
