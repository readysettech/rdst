import { describe, expect, it } from 'vitest'
import type { CompleteEvent } from '@/lib/api'
import {
  addQueryInput,
  checkPaste,
  pastedSql,
  statementCount,
} from './analyzeQueryModel'

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

describe('statementCount', () => {
  it.each([
    'SELECT * FROM t LIMIT 3 /* note; still one statement */',
    'SELECT 1 -- a; b\nFROM t',
    "SELECT ';' AS semi FROM t",
    "SELECT 'it''s; fine' FROM t",
    "SELECT 'it\\'s; fine' FROM t",
    'SELECT "odd;name" FROM t',
    'SELECT `odd;name` FROM t',
    'SELECT $$a; b$$ FROM t',
    'SELECT $body$ x; $$ y; $body$ FROM t',
    'SELECT 1 /* outer /* inner; */ still; */ FROM t',
    'SELECT 1 FROM t;',
    'SELECT 1 FROM t; -- done',
    'SELECT 1 FROM t;;  ',
  ])('reads %j as one statement', (sql) => {
    expect(statementCount(sql)).toBe(1)
    expect(checkPaste(sql).ok).toBe(true)
  })

  it.each([
    'SELECT 1 FROM a; SELECT 2 FROM b',
    "SELECT ';' FROM a; SELECT 2 FROM b",
    'SELECT 1 /* x; */ FROM a;\nSELECT 2 FROM b;',
    'SELECT $$;$$ FROM a; SELECT 2 FROM b',
    'SELECT a$b$ FROM a; SELECT $b$ FROM b',
  ])('reads %j as two statements', (sql) => {
    expect(statementCount(sql)).toBe(2)
    expect(checkPaste(sql)).toEqual({
      ok: false,
      reason: 'Paste one statement at a time.',
    })
  })

  it('counts nothing in blank or comment-only input', () => {
    expect(statementCount('  ;\n-- only a comment')).toBe(0)
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
