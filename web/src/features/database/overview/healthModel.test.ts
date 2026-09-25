import { describe, expect, it } from 'vitest'
import { FIXTURE_NOW, healthFixture } from '@/lib/monitor/fixtures'
import { overallTone, tileModels } from './healthModel'

const tile = (tiles: ReturnType<typeof tileModels>, metric: string) =>
  tiles.find((item) => item.metric === metric)

describe('tileModels', () => {
  it('builds five tiles with real series for every metric', () => {
    const tiles = tileModels(healthFixture('localdev', '24h', FIXTURE_NOW))
    expect(tiles.map((item) => item.metric)).toEqual([
      'throughput',
      'latency',
      'connections',
      'hit',
      'rollback',
    ])
    for (const item of tiles)
      expect(
        item.points.filter((point) => point.y != null).length
      ).toBeGreaterThan(3)
  })

  it('compares with the prior period only when it is covered', () => {
    const day = tileModels(healthFixture('localdev', '24h', FIXTURE_NOW))
    expect(tile(day, 'throughput')?.footer).toMatch(/vs prior 24 h$/)
    const week = tileModels(healthFixture('localdev', '7d', FIXTURE_NOW))
    expect(tile(week, 'throughput')?.footer).toBe('No prior period yet')
  })

  it('tones only by rule', () => {
    const health = healthFixture('localdev', '24h', FIXTURE_NOW)
    const tiles = tileModels(health)
    expect(tile(tiles, 'throughput')?.tone).toBe('neutral')
    expect(tile(tiles, 'hit')?.tone).toBe(
      (health.period.cache_hit_ratio_min ?? 1) < 0.99 ? 'warning' : 'neutral'
    )
    expect(tile(tiles, 'rollback')?.tone).toBe(
      health.period.deadlocks > 0 ? 'negative' : expect.any(String)
    )
    const busy = tileModels({
      ...health,
      now: { ...health.now, active_conns: 90, max_conns: 100 },
    })
    expect(tile(busy, 'connections')?.tone).toBe('warning')
    expect(overallTone(busy)).toBe('warning')
  })
})
