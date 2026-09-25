import { describe, expect, it } from 'vitest'
import type { MonitorQuery } from '@/lib/monitor/types'
import { stableOrder } from './stableOrder'

const row = (key: string, ms: number) =>
  ({ key, db_time_ms: ms, qps: ms, mean_ms: ms }) as MonitorQuery
const keys = (rows: MonitorQuery[]) => rows.map((query) => query.key)
const fresh = (...rows: MonitorQuery[]) =>
  [...rows].sort((a, b) => b.db_time_ms - a.db_time_ms)

describe('stableOrder', () => {
  it('takes the fresh order the first time, or for a sort without a metric', () => {
    const rows = fresh(row('a', 10), row('b', 30))
    expect(keys(stableOrder(null, rows, 'impact', true))).toEqual(['b', 'a'])
    expect(keys(stableOrder(['a', 'b'], rows, 'change', false))).toEqual([
      'b',
      'a',
    ])
  })

  it('keeps places between readings, even when the numbers cross', () => {
    const rows = fresh(row('a', 10), row('b', 50))
    expect(keys(stableOrder(['a', 'b'], rows, 'impact', false))).toEqual([
      'a',
      'b',
    ])
  })

  it('swaps near ties only past the margin at a reading', () => {
    const close = fresh(row('a', 100), row('b', 115))
    expect(keys(stableOrder(['a', 'b'], close, 'impact', true))).toEqual([
      'a',
      'b',
    ])
    const clear = fresh(row('a', 100), row('b', 125))
    expect(keys(stableOrder(['a', 'b'], clear, 'impact', true))).toEqual([
      'b',
      'a',
    ])
  })

  it('never moves the open row, nor lets rows pass it', () => {
    const reordered = fresh(row('a', 10), row('b', 100), row('c', 1000))
    expect(
      keys(
        stableOrder(['a', 'b', 'c'], reordered, 'impact', true, undefined, 'b')
      )
    ).toEqual(['a', 'b', 'c'])
  })

  it('lets a real change climb several places at a reading', () => {
    const rows = fresh(row('a', 100), row('b', 90), row('c', 400))
    expect(keys(stableOrder(['a', 'b', 'c'], rows, 'impact', true))).toEqual([
      'c',
      'a',
      'b',
    ])
  })

  it('slots new rows in by the fresh order and drops gone ones', () => {
    const rows = fresh(row('a', 100), row('n', 60), row('b', 50))
    expect(
      keys(stableOrder(['a', 'gone', 'b'], rows, 'impact', false))
    ).toEqual(['a', 'n', 'b'])
  })
})
