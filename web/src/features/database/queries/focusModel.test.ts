import { describe, expect, it } from 'vitest'
import type { MonitorQuery } from '@/lib/monitor/types'
import { exclusionSentence, exclusions, type FocusContext } from './focusModel'
import type { RowFacts } from './querySelectors'

const query = {
  key: 'k1',
  text: 'SELECT * FROM orders WHERE id = $1',
  display_name: 'SELECT on orders by id',
  tables: ['orders'],
} as MonitorQuery

const healthy = {
  status: { id: 'healthy' },
  classification: null,
} as unknown as RowFacts

const ctx = (extra: Partial<FocusContext> = {}): FocusContext => ({
  search: '',
  filter: 'all',
  table: null,
  keys: null,
  writesHidden: true,
  isWrite: false,
  factsOf: () => healthy,
  ...extra,
})

describe('exclusions', () => {
  it('names nothing when the query is in the list', () => {
    expect(
      exclusions(query, ctx({ search: 'orders', table: 'orders' }))
    ).toEqual([])
  })

  it('names only what hides it', () => {
    expect(
      exclusions(
        query,
        ctx({ search: 'customers', filter: 'attention', table: 'orders' })
      )
    ).toEqual(['search', 'filter'])
    expect(exclusions(query, ctx({ isWrite: true }))).toEqual(['writes'])
    expect(exclusions(query, ctx({ keys: ['other'] }))).toEqual(['picked'])
    expect(exclusions(query, ctx({ table: 'customers' }))).toEqual(['table'])
  })

  it('says it in one sentence', () => {
    expect(
      exclusionSentence('SELECT on orders', ['search', 'filter'], {
        filter: 'healthy',
        table: null,
      })
    ).toBe(
      "SELECT on orders is filtered out by your search and the 'Healthy' filter."
    )
    expect(
      exclusionSentence('Q', ['writes', 'table', 'search'], {
        filter: 'all',
        table: 'orders',
      })
    ).toBe(
      'Q is filtered out by the writes toggle, the orders table filter and your search.'
    )
  })
})
