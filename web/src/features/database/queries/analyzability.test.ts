import { describe, expect, it } from 'vitest'
import type { MonitorQuery } from '@/lib/monitor/types'
import {
  analyzability,
  noScoreLine,
  notAnalyzedLine,
  sqlAnalyzability,
} from './analyzability'

const query = (patch: Partial<MonitorQuery>): MonitorQuery =>
  ({
    key: 'abc',
    text: 'SELECT 1',
    text_status: 'ok',
    text_reason: null,
    display_name: 'Select',
    tables: [],
    state: [],
    last_analysis: null,
    ...patch,
  }) as MonitorQuery

describe('sqlAnalyzability', () => {
  it('allows reads', () => {
    expect(sqlAnalyzability('SELECT * FROM orders').ok).toBe(true)
    expect(sqlAnalyzability('WITH x AS (SELECT 1) SELECT * FROM x').ok).toBe(
      true
    )
    expect(sqlAnalyzability('/* app */ SELECT 1').ok).toBe(true)
  })

  it('says why writes, DDL, session commands and sleeps are refused', () => {
    expect(sqlAnalyzability('DROP INDEX idx_orders_total').reason).toBe(
      "DROP changes the schema, so RDST doesn't run it."
    )
    expect(sqlAnalyzability('update orders set total = $1').reason).toBe(
      "UPDATE changes data, so RDST doesn't run it."
    )
    expect(sqlAnalyzability('BEGIN').code).toBe('utility')
    expect(sqlAnalyzability('SELECT pg_sleep($1)').reason).toBe(
      "It calls pg_sleep, so RDST doesn't run it."
    )
    expect(sqlAnalyzability('').code).toBe('no_text')
  })
})

describe('analyzability', () => {
  it('prefers the served field, then the analyzer skip reason', () => {
    expect(
      analyzability(
        query({
          analyzable: false,
          analyzable_code: 'write',
          not_analyzable_reason: 'Served reason',
        })
      ).reason
    ).toBe('Served reason')
    expect(
      analyzability(
        query({
          last_analysis: {
            state: 'skipped',
            reason: 'Too slow to run safely',
          } as MonitorQuery['last_analysis'],
        })
      )
    ).toEqual({ ok: false, code: 'skipped', reason: 'Too slow to run safely' })
    expect(
      analyzability(query({ text_status: 'unavailable', text: '' })).ok
    ).toBe(false)
  })
})

describe('notAnalyzedLine', () => {
  it('names the statement type for writes and schema changes', () => {
    const update = query({ text: 'UPDATE payments SET status = $1' })
    expect(notAnalyzedLine(update, analyzability(update))).toBe(
      "RDST doesn't analyze UPDATE statements because they change data."
    )
    const drop = query({ text: 'DROP INDEX idx' })
    expect(notAnalyzedLine(drop, analyzability(drop))).toBe(
      "RDST doesn't analyze DROP statements because they change the schema."
    )
  })

  it('passes other reasons through and is empty for reads', () => {
    const begin = query({ text: 'BEGIN' })
    expect(notAnalyzedLine(begin, analyzability(begin))).toBe(
      'BEGIN is a session command with no plan to analyze.'
    )
    const read = query({})
    expect(notAnalyzedLine(read, analyzability(read))).toBe('')
  })
})

describe('noScoreLine', () => {
  const row = (text: string) => ({ text })
  it('names the statement RDST does not run', () => {
    const text = 'UPDATE sessions SET seen = now() WHERE id = $1'
    expect(noScoreLine(row(text), sqlAnalyzability(text))).toBe(
      "No score: RDST doesn't run UPDATE statements"
    )
  })
  it('says when a score is on its way', () => {
    const text = 'SELECT 1'
    expect(
      noScoreLine(row(text), sqlAnalyzability(text), 'Queued, about 6 min')
    ).toBe('No score yet: queued, about 6 min')
    expect(noScoreLine(row(text), sqlAnalyzability(text))).toBe(
      'No score yet. Click Analyze now to analyze.'
    )
  })
})
