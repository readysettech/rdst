import { describe, expect, it } from 'vitest'
import type { AskResultEvent } from '@/lib/ask'
import {
  addedLimit,
  cellText,
  DEFAULT_ASK_LIMIT,
  fillParams,
  groupByDay,
  hasPlaceholders,
  leadSentence,
  numericColumns,
  stepForPhase,
  whenWord,
} from './askModel'

function result(overrides: Partial<AskResultEvent>): AskResultEvent {
  return {
    type: 'result',
    success: true,
    sql: 'select 1',
    columns: ['Post', 'Votes', 'Score'],
    rows: [],
    row_count: 0,
    execution_time_ms: 400,
    llm_calls: 1,
    total_tokens: 10,
    ...overrides,
  } as AskResultEvent
}

describe('leadSentence', () => {
  it('leads with the largest value, its column and how far ahead it is', () => {
    expect(
      leadSentence(
        result({
          rows: [
            ['Why does Postgres choose a sequential scan?', 412, 388],
            ['Index on (a, b) vs two indexes', 201, 190],
          ],
          row_count: 2,
        })
      )
    ).toBe(
      '"Why does Postgres choose a sequential scan?" leads with 412 votes, more than twice the next post.'
    )
    expect(
      leadSentence(
        result({
          rows: [
            ['A', '300', 1],
            ['B', '200', 1],
          ],
          row_count: 2,
        })
      )
    ).toBe('"A" leads with 300 votes.')
  })

  it('reads the largest value even when the rows are not sorted on it', () => {
    expect(
      leadSentence(
        result({
          rows: [
            ['B', 20, 1],
            ['A', 90, 1],
            ['C', 40, 1],
          ],
          row_count: 3,
        })
      )
    ).toBe('"A" leads with 90 votes, more than twice the next post.')
  })

  it('states the row count without a text column, a number or a single leader', () => {
    expect(leadSentence(result({ rows: [] }))).toBeNull()
    expect(
      leadSentence(result({ columns: ['n'], rows: [[1], [2]], row_count: 2 }))
    ).toBe('The answer has 2 rows.')
    expect(
      leadSentence(
        result({ columns: ['Post'], rows: [['a'], ['b']], row_count: 2 })
      )
    ).toBe('The answer has 2 rows.')
    expect(
      leadSentence(
        result({
          rows: [
            ['A', 5, 1],
            ['B', 5, 1],
          ],
          row_count: 2,
        })
      )
    ).toBe('The answer has 2 rows.')
  })
})

it('aligns the columns that hold numbers', () => {
  expect(
    numericColumns(
      result({
        rows: [
          ['a', 1, '2'],
          ['b', 2, null],
        ],
        row_count: 2,
      })
    )
  ).toEqual([false, true, true])
})

it('places a stream phase on the three steps', () => {
  expect(stepForPhase('schema')).toBe(0)
  expect(stepForPhase('generate')).toBe(1)
  expect(stepForPhase('execute')).toBe(2)
  expect(stepForPhase(undefined)).toBe(0)
})

describe('cellText', () => {
  it('groups quantities and leaves ids, keys and years as written', () => {
    expect(cellText(4790, 'order_count')).toBe('4,790')
    expect(cellText(4790, 'customer_id')).toBe('4790')
    expect(cellText(2026, 'year')).toBe('2026')
    expect(cellText(null, 'id')).toBe('NULL')
  })
})

describe('Earlier', () => {
  const now = Date.parse('2026-10-08T09:56:30')
  const item = (question: string, iso: string) => ({
    hash: question,
    question,
    sql: '',
    tag: '',
    target: 'demo',
    last_used: iso,
  })

  it('groups the questions by day, newest first', () => {
    const groups = groupByDay(
      [
        item('older', '2026-09-16T10:00:00'),
        item('today', '2026-10-08T09:12:00'),
        item('sep18a', '2026-09-18T15:00:00'),
        item('sep18b', '2026-09-18T11:00:00'),
      ],
      now
    )
    expect(groups.map((group) => group.heading)).toEqual([
      'Today',
      'September 18',
      'September 16',
    ])
    expect(groups[1].items.map((entry) => entry.question)).toEqual([
      'sep18a',
      'sep18b',
    ])
  })

  it('shows the time today and the date otherwise', () => {
    expect(whenWord('2026-10-08T09:12:00', now)).toBe('9:12 AM')
    expect(whenWord('2026-09-18T15:00:00', now)).toBe('Sep 18')
  })
})

describe('addedLimit', () => {
  it('reads the LIMIT the backend added, else its default', () => {
    expect(addedLimit('SELECT * FROM posts LIMIT 100')).toBe(100)
    expect(addedLimit('SELECT * FROM (SELECT 1 LIMIT 5) t LIMIT 250')).toBe(250)
    expect(addedLimit('SELECT * FROM posts')).toBe(DEFAULT_ASK_LIMIT)
    expect(DEFAULT_ASK_LIMIT).toBe(100)
  })
})

describe('fillParams', () => {
  const sql =
    'SELECT * FROM orders WHERE customer_id = $1 AND status = $2 LIMIT $10'

  it('fills each placeholder from the sample values, literals as they are', () => {
    expect(
      fillParams(sql, [
        { name: '$1', value: '1876' },
        { name: '2', value: "o'brien" },
        { name: '$10', value: "'5'" },
      ])
    ).toBe(
      "SELECT * FROM orders WHERE customer_id = 1876 AND status = 'o''brien' LIMIT '5'"
    )
  })

  it('writes NULL for a missing value and keeps placeholders without a sample', () => {
    const filled = fillParams(sql, [{ name: '$1', value: 'None' }])
    expect(filled).toBe(
      'SELECT * FROM orders WHERE customer_id = NULL AND status = $2 LIMIT $10'
    )
    expect(hasPlaceholders(filled)).toBe(true)
  })

  it('leaves the SQL alone without samples', () => {
    expect(fillParams(sql, undefined)).toBe(sql)
    expect(fillParams(sql, [])).toBe(sql)
    expect(hasPlaceholders('SELECT $$a$$')).toBe(false)
  })
})
