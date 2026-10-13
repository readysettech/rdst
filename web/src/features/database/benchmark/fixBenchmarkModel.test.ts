import { describe, expect, it } from 'vitest'
import { CA_FIX_COUNT, caFixtures } from '@/lib/monitor/fixturesCa'
import {
  allFixesSql,
  benchmarkRows,
  benchmarkVerdict,
  changePercent,
  type FixBenchmarkRow,
  formatChange,
  queriesWithFixes,
} from './fixBenchmarkModel'

const rows = caFixtures('ca-database').queries('demo', '1h', 500, 0).queries
const fixes = queriesWithFixes(rows)
const result = caFixtures('ca-database').benchmarkFixes(
  'demo',
  fixes.map((row) => row.key)
)

function row(before: number | null, after: number | null): FixBenchmarkRow {
  return {
    key: `${before}-${after}`,
    name: 'q',
    color: 'c',
    sql: 'CREATE INDEX x ON t (a);',
    before_ms: before,
    after_ms: after,
    estimated: false,
  }
}

describe('fixBenchmarkModel', () => {
  it('covers every query with a suggested fix', () => {
    expect(queriesWithFixes(rows)).toHaveLength(CA_FIX_COUNT)
  })

  it('reads before and after from the result, else the live mean and nothing', () => {
    const waiting = benchmarkRows(fixes, undefined)
    expect(waiting[0].before_ms).toBe(rows[0].mean_ms)
    expect(waiting[0].after_ms).toBeNull()
    expect(waiting[0].estimated).toBe(true)
    expect(allFixesSql(waiting).split('\n\n')).toHaveLength(CA_FIX_COUNT)
    const out = benchmarkRows(fixes, result)
    expect(out.map((row) => [row.before_ms, row.after_ms])).toEqual([
      [69.7, 3.1],
      [29.8, 0.9],
      [65.4, 1.2],
      [27.4, 0.8],
      [70.6, 18.4],
      [23.1, 0.7],
      [67.2, 31.5],
      [12.8, 0.6],
      [9.4, 4.1],
    ])
    expect(out.every((row) => row.estimated)).toBe(true)
    expect(benchmarkVerdict(out, 'demo', result.share)).toEqual({
      lead: 'All 9 faster with their fixes.',
      rest: 'Total time on demo would fall 54%.',
    })
  })

  it('phrases the change as a percent of before', () => {
    expect(changePercent(69.7, 3.1)).toBe(-96)
    expect(changePercent(10, 12)).toBe(20)
    expect(changePercent(null, 3)).toBeNull()
    expect(formatChange(-96)).toBe('-96%')
    expect(formatChange(20)).toBe('+20%')
  })

  it('gives one verdict when every fix measured faster', () => {
    expect(benchmarkVerdict([row(100, 50), row(10, 2)], 'demo', 0.31)).toEqual({
      lead: 'All 2 faster with their fixes.',
      rest: 'Total time on demo would fall 31%.',
    })
    expect(benchmarkVerdict([row(100, 50), row(10, 2)], 'demo')).toEqual({
      lead: 'All 2 faster with their fixes.',
      rest: null,
    })
    expect(
      benchmarkVerdict([row(100, 50), row(10, null)], 'demo', 0.3)
    ).toEqual({
      lead: '1 of 2 faster with their fixes.',
      rest: 'No estimate for 1 change.',
    })
  })

  it('never counts a change with no estimate as slower', () => {
    expect(benchmarkVerdict([row(0.51, null)], 'demo')).toEqual({
      lead: 'No estimate for this change.',
      rest: null,
    })
    expect(
      benchmarkVerdict(
        [
          {
            ...row(0.51, null),
            reason: 'Hypothetical indexes are unavailable.',
          },
          row(2, null),
        ],
        'demo'
      )
    ).toEqual({
      lead: 'No estimate for these changes.',
      rest: 'Hypothetical indexes are unavailable.',
    })
    const [only] = benchmarkRows(fixes.slice(0, 1), {
      ...result,
      rows: [
        {
          ...result.rows[0],
          after_ms: null,
          change_pct: null,
          reason: 'Hypothetical indexes are unavailable.',
        } as (typeof result.rows)[number],
      ],
    })
    expect(only.reason).toBe('Hypothetical indexes are unavailable.')
  })
})
