import { describe, expect, it } from 'vitest'
import {
  deriveQueryName,
  filterQueriesBySearch,
  queryDisplayName,
} from './queryIdentity'

const queries = [
  {
    hash: 'abcd1111',
    tag: 'Recent orders',
    sql: 'SELECT id, created_at FROM orders WHERE customer_id = $1',
    original_sql: null,
    question: null,
  },
  {
    hash: 'abcd2222',
    tag: '',
    sql: 'SELECT COUNT(*) FROM public.users',
    original_sql: null,
    question: 'How many users are in the database?',
  },
  {
    hash: 'efgh3333',
    tag: '',
    sql: 'SELECT displayname FROM users',
    original_sql: 'SELECT displayname\nFROM users',
    question: null,
  },
]

describe('query identity', () => {
  it('prefers a saved name and otherwise derives a readable SQL name', () => {
    expect(queryDisplayName(queries[0])).toBe('Recent orders')
    expect(queryDisplayName(queries[1])).toBe('COUNT on users')
    expect(deriveQueryName('UPDATE public.orders SET status = $1')).toBe(
      'Update · orders'
    )
  })

  it('searches names, natural-language questions, and normalized SQL', () => {
    expect(filterQueriesBySearch(queries, 'recent orders')).toEqual([
      queries[0],
    ])
    expect(filterQueriesBySearch(queries, 'how many users')).toEqual([
      queries[1],
    ])
    expect(filterQueriesBySearch(queries, 'displayname from users')).toEqual([
      queries[2],
    ])
  })

  it('matches exact hashes and only unambiguous hash prefixes', () => {
    expect(filterQueriesBySearch(queries, 'abcd1111')).toEqual([queries[0]])
    expect(filterQueriesBySearch(queries, 'efgh')).toEqual([queries[2]])
    expect(filterQueriesBySearch(queries, 'abcd')).toEqual([])
    expect(filterQueriesBySearch(queries, 'abc')).toEqual([])
  })
})
