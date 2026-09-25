import { describe, expect, it } from 'vitest'
import type { CompleteEvent } from '@/lib/api'
import { addQueryInput, checkPaste, pastedSql } from './analyzeQueryModel'

describe('checkPaste', () => {
  it('accepts one read, and asks for values when it has placeholders', () => {
    expect(pastedSql('  SELECT 1 FROM t;  \n')).toBe('SELECT 1 FROM t')
    expect(checkPaste('SELECT * FROM orders WHERE id = 5;')).toEqual({
      ok: true,
      needsValues: false,
    })
    expect(checkPaste('SELECT * FROM orders WHERE id = $1')).toEqual({
      ok: true,
      needsValues: true,
    })
  })

  it('says why it refuses writes, several statements or nothing', () => {
    expect(checkPaste('   ')).toEqual({ ok: false, reason: null })
    expect(checkPaste('SELECT 1 FROM a; SELECT 2 FROM b')).toEqual({
      ok: false,
      reason: 'Paste one statement at a time.',
    })
    const write = checkPaste('UPDATE orders SET total = 0')
    expect(write.ok).toBe(false)
    expect(!write.ok && write.reason).toMatch(/changes data/)
  })
})

describe('addQueryInput', () => {
  const done = {
    query_hash: 'abc123def456',
    stored_analysis_id: 'an-1',
    explain_results: { plan_format: 'analyze' },
  } as unknown as CompleteEvent

  it('files the SQL as pasted with the stored analysis and how it ran', () => {
    expect(
      addQueryInput('SELECT * FROM t WHERE id = $1;', done, false)
    ).toEqual({
      sql: 'SELECT * FROM t WHERE id = $1',
      query_hash: 'abc123def456',
      analysis_id: 'an-1',
      mode: 'analyze',
    })
    const planned = {
      ...done,
      explain_results: { plan_format: 'json' },
    } as unknown as CompleteEvent
    expect(addQueryInput('SELECT 1 FROM t', planned, false).mode).toBe(
      'explain'
    )
  })

  it('files the query alone when the analysis was not stored', () => {
    expect(addQueryInput('SELECT 1 FROM t', undefined, false)).toEqual({
      sql: 'SELECT 1 FROM t',
      query_hash: null,
      analysis_id: null,
      mode: null,
    })
  })
})
