import { describe, expect, it } from 'vitest'
import type { MonitorQuery } from '@/lib/monitor/types'
import {
  bandColors,
  buildLoadModel,
  formatPercent,
  loadFromBreakdown,
  loadFromQueries,
  OTHER_KEY,
  stackMax,
  timeShareRows,
} from './loadModel'

const xs = [0, 60, 120]

describe('buildLoadModel', () => {
  it('bands the top queries by execution time and folds the rest into other', () => {
    const model = buildLoadModel(
      xs,
      [
        { key: 'a', label: 'A', qps: [10, 10, 10], meanMs: [2, 2, 2] },
        { key: 'b', label: 'B', qps: [1, 1, 1], meanMs: [5, 5, 5] },
        { key: 'c', label: 'C', qps: [1, null, 1], meanMs: [1, null, 1] },
      ],
      1
    )
    expect(model.bands.map((band) => band.key)).toEqual(['a', OTHER_KEY])
    expect(model.load[0]).toEqual([20, 20, 20])
    expect(model.load[1]).toEqual([6, 5, 6])
    const a = model.scope.get('a')
    expect(a?.share).toBeCloseTo(60 / 77)
    expect(a?.qps).toBeCloseTo(10)
    expect(a?.meanMs).toBeCloseTo(2)
    expect(model.colorFor('c')).toBe(model.colorFor(OTHER_KEY))
    expect(model.overallLatency[0]).toBeCloseTo(26 / 12)
    expect(stackMax(model.load)).toBe(26)
  })

  it('keeps missing buckets as gaps', () => {
    const model = buildLoadModel(xs, [
      { key: 'a', label: 'A', qps: [1, null, 1], meanMs: [1, null, 1] },
    ])
    expect(model.load[0]).toEqual([1, null, 1])
    expect(model.overallLatency[1]).toBeNull()
  })
})

describe('bands by call count', () => {
  const sources = [
    { key: 'slow', label: 'Slow', qps: [1, 1, 1], meanMs: [500, 500, 500] },
    { key: 'busy', label: 'Busy', qps: [100, 100, 100], meanMs: [1, 1, 1] },
    { key: 'mid', label: 'Mid', qps: [10, 10, 10], meanMs: [5, 5, 5] },
  ]

  it('picks the top bands by calls and keeps pinned colors', () => {
    const byTime = buildLoadModel(xs, sources, 1)
    expect(byTime.bands.map((band) => band.key)).toEqual(['slow', OTHER_KEY])
    const byCalls = buildLoadModel(xs, sources, 2, undefined, {
      rankBy: 'calls',
      pinned: bandColors(byTime),
    })
    expect(byCalls.bands.map((band) => band.key)).toEqual([
      'busy',
      'mid',
      OTHER_KEY,
    ])
    expect(byCalls.calls[0]).toEqual([100, 100, 100])
    expect(byCalls.calls[2]).toEqual([1, 1, 1])
    expect(byCalls.bands[0].share).toBeCloseTo(100 / 111)
    const slowOnly = buildLoadModel(xs, sources, 3, undefined, {
      rankBy: 'calls',
      pinned: bandColors(byTime),
    })
    expect(slowOnly.colorFor('slow')).toBe(byTime.colorFor('slow'))
    const colors = slowOnly.bands.map((band) => band.color)
    expect(new Set(colors).size).toBe(colors.length)
  })
})

describe('time share', () => {
  it('stacks to 100 percent per bucket', () => {
    const rows = timeShareRows([
      [20, null, 0],
      [60, null, 0],
    ])
    expect(rows[0]).toEqual([25, null, null])
    expect(rows[1]).toEqual([75, null, null])
  })

  it('formats percentages', () => {
    expect(formatPercent(42.4)).toBe('42%')
    expect(formatPercent(0.42)).toBe('0.4%')
    expect(formatPercent(0)).toBe('0%')
  })
})

describe('partial buckets', () => {
  it('drops the bucket still filling up from the query series', () => {
    const query = {
      key: 'a',
      display_name: 'A',
      series: [0, 60, 120].map((ts) => ({ ts, qps: 1, mean_ms: 1 })),
    } as unknown as MonitorQuery
    expect(loadFromQueries([query], [0, 150], 8, undefined, 150).xs).toEqual([
      0, 60,
    ])
    expect(loadFromQueries([query], [0, 200], 8, undefined, 200).xs).toEqual([
      0, 60, 120,
    ])
  })

  it('drops it from the served breakdown too', () => {
    const model = loadFromBreakdown(
      {
        res: 60,
        start: 0,
        end: 150,
        total_ms: 2,
        bands: [
          {
            key: 'a',
            kind: 'query',
            display_name: 'A',
            color_index: 0,
            share: 1,
            total_ms: 2,
            calls: 2,
            mean_ms: 1,
          },
        ],
        points: [0, 60, 120].map((ts) => ({
          ts,
          seconds: 60,
          total_ms: [60],
          calls: [60],
          mean_ms: 1,
        })),
      } as never,
      150
    )
    expect(model.xs).toEqual([0, 60])
  })
})

it('keeps hourly fallback values and the minute retention note in the chart model', () => {
  const detail_note = 'Minute detail is kept for 48 hours'
  const model = loadFromBreakdown(
    {
      res: 60,
      start: 0,
      end: 180,
      total_ms: 120,
      detail_note,
      bands: [
        {
          key: 'query',
          kind: 'query',
          display_name: 'Query',
          color_index: 0,
          share: 1,
          total_ms: 120,
          calls: 60,
          mean_ms: 2,
        },
      ],
      points: [0, 60].map((ts) => ({
        ts,
        seconds: 60,
        total_ms: [60],
        calls: [30],
        mean_ms: 2,
      })),
    } as never,
    180
  )
  expect(model.xs).toEqual([0, 60])
  expect(model.calls[0]).toEqual([0.5, 0.5])
  expect(model.latency[0]).toEqual([2, 2])
  expect(model.detailNote).toBe(detail_note)
})
