import { expect, it } from 'vitest'
import type { ReportSummary } from '@/lib/monitor/types'
import {
  bodyCounts,
  firstSentence,
  fixTitle,
  normalizeHealth,
  parseReportText,
  plainReportText,
  verdictParts,
} from './reportModel'
import { cleanHistory } from './reportNav'

const KEY = 'c58ed20d30d1'

it('turns tokens and known bare hashes into query references', () => {
  expect(parseReportText(`Load is {{q:${KEY}}} mostly.`)).toEqual([
    { kind: 'text', text: 'Load is ' },
    { kind: 'query', key: KEY },
    { kind: 'text', text: ' mostly.' },
  ])
  // A bare 12-hex word is a reference only when the report knows it.
  expect(parseReportText(`${KEY} (33.6%)`)).toEqual([
    { kind: 'text', text: `${KEY} (33.6%)` },
  ])
  expect(parseReportText(`${KEY} (33.6%)`, new Set([KEY]))[0]).toEqual({
    kind: 'query',
    key: KEY,
  })
  expect(
    plainReportText(`{{q:${KEY}}} is slow.`, {
      [KEY]: { display_name: 'Select - orders', text_snippet: '' },
    })
  ).toBe('Select - orders is slow.')
})

it('takes the first sentence without splitting numbers or identifiers', () => {
  expect(
    firstSentence('Load is on orders.total at 0.5 ms. Second sentence here.')
  ).toBe('Load is on orders.total at 0.5 ms.')
  expect(firstSentence('No period')).toBe('No period')
})

it('falls back to a short title from the action', () => {
  expect(
    fixTitle({
      query_key: KEY,
      title: '',
      why: '',
      action: 'Find the caller and remove it, then retry.',
    })
  ).toBe('Find the caller and remove it')
  expect(
    fixTitle({
      query_key: KEY,
      title: '',
      why: '',
      action: 'CREATE INDEX idx ON t (a);',
    })
  ).toBe('Apply the recommended change')
  expect(
    fixTitle({ query_key: KEY, title: 'Stop the caller', why: '', action: 'x' })
  ).toBe('Stop the caller')
})

it('normalizes legacy health rows to human labels and known statuses', () => {
  const rows = normalizeHealth([
    {
      check: 'cache_hit_ratio',
      label: '',
      value: '0.99',
      status: 'OK' as never,
      note: '',
    },
    {
      check: 'some_new_check',
      label: '',
      value: '',
      status: 'Collected' as never,
      note: '',
    },
  ])
  expect(rows.map((r) => [r.label, r.status])).toEqual([
    ['Cache hit ratio', 'ok'],
    ['Some new check', 'info'],
  ])
})

it('counts and phrases the verdict', () => {
  const counts = bodyCounts({
    summary: '',
    fix_first: [{ query_key: KEY, title: 't', why: '', action: '' }],
    one_fix_several_queries: [],
    health: [
      { check: 'a', label: 'A', value: '', status: 'warn', note: '' },
      { check: 'b', label: 'B', value: '', status: 'ok', note: '' },
    ],
    changed_since_yesterday: [],
    provisional_query_keys: [],
    narrative_error: null,
  })
  expect(counts).toEqual({ fixFirst: 1, oneFix: 0, healthWarn: 1, healthOk: 1 })
  expect(verdictParts(counts)).toEqual([
    '1 thing to fix first',
    '1 health warning',
  ])
})

it('keeps one row per report, newest first', () => {
  const summary = (id: number, ts: number): ReportSummary => ({
    id,
    ts,
    trigger: 'user',
    duration_s: 1,
    headline: '',
    counts: { fix_first: 0, one_fix: 0, health_warn: 0, health_ok: 0 },
    provisional_count: 0,
  })
  expect(
    cleanHistory([summary(1, 10), summary(2, 30), summary(1, 10)]).map(
      (r) => r.id
    )
  ).toEqual([2, 1])
})
