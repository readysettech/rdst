import { afterEach, beforeEach, expect, it } from 'vitest'
import {
  getLoad,
  getQueries,
  getQuery,
  getQueryAnalysis,
  resetStubState,
  setFixtureTiming,
} from './api'
import { setFixtureOverride } from './contract'
import { FIXTURE_NOW } from './fixtures'
import { loadFixture, queriesV4Fixture } from './fixturesV4'

beforeEach(() => {
  setFixtureTiming(0)
  resetStubState()
  setFixtureOverride('localdev')
})

afterEach(() => setFixtureOverride(undefined))

it('bands and query rows share one ranking and color per key', async () => {
  const load = await getLoad('localdev', '24h')
  const page = await getQueries('localdev', '24h')
  const colors = new Map(page.queries.map((row) => [row.key, row.color_index]))
  load.bands
    .filter((band) => band.kind === 'query')
    .forEach((band, index) => {
      expect(band.color_index).toBe(index)
      expect(colors.get(band.key)).toBe(index)
    })
  expect(load.bands.at(-1)?.key).toBe('other')
  const shares = load.bands.reduce((sum, band) => sum + band.share, 0)
  expect(shares).toBeCloseTo(1, 5)
  expect(load.points[0].total_ms?.length ?? load.bands.length).toBe(load.bands.length)
})

it('short ranges clip to their window and report availability', () => {
  const load = loadFixture('localdev', '5m', FIXTURE_NOW)
  expect(load.points.every((point) => point.ts >= FIXTURE_NOW - 300)).toBe(true)
  const ranges = Object.fromEntries(load.ranges.map((entry) => [entry.range, entry]))
  expect(ranges['7d'].enabled).toBe(true)
  expect(ranges['30d'].enabled).toBe(false)
  expect(ranges['30d'].reason).toMatch(/days recorded/)
})

it('rows say whether RDST analyzes them, and single rows and headers resolve', async () => {
  const rows = queriesV4Fixture('localdev', '24h', FIXTURE_NOW).queries
  expect(rows.every((row) => typeof row.analyzable === 'boolean')).toBe(true)
  const row = rows.find((candidate) => candidate.analyzable)!
  expect((await getQuery('localdev', row.key, '24h')).key).toBe(row.key)
  const digest = await getQueryAnalysis('localdev', row.key)
  expect(digest.analysis_ref).toContain(row.key)
  await expect(getQuery('localdev', 'missing-key', '24h')).rejects.toThrow('Query not found')
})
