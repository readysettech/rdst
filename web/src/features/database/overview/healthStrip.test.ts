import { describe, expect, it } from 'vitest'
import type { MonitorHealth } from '@/lib/monitor/types'
import { hitAxisFloor, stripTiles } from './healthStrip'

const health = (ratios: (number | null)[]) =>
  ({
    res: 60,
    now: {
      active_conns: 9,
      max_conns: 100,
      cache_hit_ratio: ratios.at(-1) ?? null,
    },
    series: ratios.map((ratio, index) => ({
      ts: index * 60,
      cache_hit_ratio: ratio,
      active_conns: 9,
      xact_rollback: 1,
      deadlocks: 0,
    })),
  }) as unknown as MonitorHealth

describe('stripTiles', () => {
  it('reads the last non-null hit ratio and says 100% plainly', () => {
    const tiles = stripTiles(
      { ...health([1, 1, 1]), now: { cache_hit_ratio: null } } as MonitorHealth,
      [0, 180],
      10_000
    )
    const hit = tiles.find((tile) => tile.id === 'hit')
    expect(hit?.value).toBe('100')
    expect(hit?.footer).toBe('100%')
    const gap = stripTiles(health([0.999, null]), [0, 120], 10_000).find(
      (tile) => tile.id === 'hit'
    )
    expect(gap?.value).toBe('99.9')
  })

  it('leaves out the bucket still filling up', () => {
    const tiles = stripTiles(health([1, 0.5]), [0, 120], 90)
    const hit = tiles.find((tile) => tile.id === 'hit')
    expect(hit?.points.map((point) => point.x)).toEqual([0])
    expect(hit?.footer).toBe('100%')
  })

  it('shows the hit ratio with one decimal and an honest low', () => {
    const hit = stripTiles(health([1, 0.99994, null, 1]), [0, 180]).find(
      (tile) => tile.id === 'hit'
    )
    expect(hit?.value).toBe('100')
    expect(hit?.footer).toBe('Low 99.9%')
    expect(hit?.yMin).toBe(0.9)
    expect(hit?.yMax).toBe(1)
  })

  it('keeps the axis floor at 90% unless the data dips lower', () => {
    expect(hitAxisFloor(0.9952)).toBe(0.9)
    expect(hitAxisFloor(0.84)).toBe(0.8)
    expect(hitAxisFloor(null)).toBe(0.9)
  })
})
