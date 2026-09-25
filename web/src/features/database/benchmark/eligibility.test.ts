import { describe, expect, it } from 'vitest'
import type { MonitorQuery } from '@/lib/monitor/types'
import {
  benchmarkBlockReason,
  hasQueryText,
  sqlBlockReason,
  withoutLegacyBenchmarkParams,
} from './eligibility'

describe('eligibility', () => {
  it('only benchmarks queries whose text was captured', () => {
    const base = { text: 'SELECT 1', text_status: 'ok' } as MonitorQuery
    expect(hasQueryText(base)).toBe(true)
    expect(hasQueryText({ ...base, text_status: 'unavailable' })).toBe(false)
    expect(hasQueryText({ ...base, text: '  ' })).toBe(false)
  })

  it("blocks queries RDST won't run, with the reason", () => {
    const base = {
      text: 'SELECT 1',
      text_status: 'ok',
      last_analysis: null,
    } as MonitorQuery
    expect(benchmarkBlockReason(base)).toBeNull()
    expect(benchmarkBlockReason({ ...base, text: ' (SELECT 1)' })).toBeNull()
    expect(
      benchmarkBlockReason({
        ...base,
        text: 'SELECT pg_sleep(180)',
        last_analysis: {
          state: 'skipped',
          reason: "This query calls pg_sleep, which RDST won't run.",
        } as MonitorQuery['last_analysis'],
      })
    ).toBe("This query calls pg_sleep, which RDST won't run.")
    expect(benchmarkBlockReason({ ...base, text: 'DROP INDEX foo' })).toBe(
      'RDST only runs read-only queries. This statement runs DROP.'
    )
    expect(
      benchmarkBlockReason({ ...base, text: '', text_status: 'unavailable' })
    ).toBeNull()
    expect(sqlBlockReason('update orders set total = 1')).toMatch(/UPDATE/)
    expect(sqlBlockReason('WITH x AS (SELECT 1) SELECT * FROM x')).toBeNull()
  })
})

describe('old benchmark links', () => {
  it('leaves links without them alone', () => {
    expect(withoutLegacyBenchmarkParams({ range: '1h' })).toBeNull()
  })

  it('drops the drawer params', () => {
    expect(
      withoutLegacyBenchmarkParams({
        range: '1h',
        bench: 'load-test',
        run: 'load_test_x',
      })
    ).toEqual({ range: '1h' })
  })

  it("opens a selected query's Benchmarks section", () => {
    expect(
      withoutLegacyBenchmarkParams({ bench: 'compare', select: ' abc, def' })
    ).toEqual({ query: 'abc', tab: 'readyset' })
  })

  it('keeps a query the link already opens', () => {
    expect(
      withoutLegacyBenchmarkParams({
        query: 'q1',
        tab: 'analysis',
        select: 'abc',
      })
    ).toEqual({ query: 'q1', tab: 'analysis' })
  })
})
