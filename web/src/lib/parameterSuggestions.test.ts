import { describe, expect, it } from 'vitest'
import type { SchemaDetails } from '../types/schema'
import {
  buildParameterSuggestions,
  suggestionSummaryMessage,
} from './parameterSuggestions'
import { detectParameters } from './sqlParameters'

const SCHEMA: SchemaDetails = {
  target: 'prod',
  tables: [
    {
      name: 'orders',
      description: null,
      business_context: null,
      row_estimate: null,
      relationships: [],
      columns: [
        {
          name: 'status',
          data_type: 'text',
          description: null,
          unit: null,
          is_pii: false,
          enum_values: { paid: 'Paid order', pending: 'Pending order' },
        },
        {
          name: 'shop_id',
          data_type: 'bigint',
          description: null,
          unit: null,
          is_pii: false,
          enum_values: null,
        },
        {
          name: 'email',
          data_type: 'text',
          description: null,
          unit: null,
          is_pii: true,
          enum_values: null,
        },
      ],
    },
    {
      name: 'users',
      description: null,
      business_context: null,
      row_estimate: null,
      relationships: [],
      columns: [
        {
          name: 'status',
          data_type: 'text',
          description: null,
          unit: null,
          is_pii: false,
          enum_values: { active: 'Active user', banned: 'Banned user' },
        },
        {
          name: 'plan',
          data_type: 'text',
          description: null,
          unit: null,
          is_pii: false,
          enum_values: { free: 'Free plan', pro: 'Pro plan' },
        },
        {
          name: 'location',
          data_type: 'text',
          description: null,
          unit: null,
          is_pii: false,
          enum_values: null,
        },
        {
          name: 'reputation',
          data_type: 'integer',
          description: null,
          unit: null,
          is_pii: false,
          enum_values: null,
        },
      ],
    },
  ],
  terminology: [],
  extensions: [],
  custom_types: [],
  metrics: [],
}

describe('buildParameterSuggestions', () => {
  it('uses enum and numeric schema evidence without suggesting PII', () => {
    const sql =
      'SELECT * FROM orders WHERE status = :status AND shop_id = :shop AND email = :email'
    const suggestions = buildParameterSuggestions(
      sql,
      detectParameters(sql),
      SCHEMA
    )

    expect(suggestions[':status']).toEqual({
      value: 'paid',
      provenance: 'Schema enum · orders.status',
    })
    expect(suggestions[':shop']).toEqual({
      value: '1',
      provenance: 'Schema type · orders.shop_id',
    })
    expect(suggestions[':email']).toBeUndefined()
  })

  it('uses query shape for LIMIT and OFFSET without schema data', () => {
    const sql = 'SELECT * FROM orders LIMIT $1 OFFSET $2'
    expect(buildParameterSuggestions(sql, detectParameters(sql), null)).toEqual(
      {
        $1: { value: '100', provenance: 'Query shape · LIMIT' },
        $2: { value: '0', provenance: 'Query shape · OFFSET' },
      }
    )
  })

  it('suggests the median for percentile fractions without schema data', () => {
    const sql =
      'SELECT PERCENTILE_DISC($1) WITHIN GROUP (ORDER BY reputation), PERCENTILE_CONT($2) WITHIN GROUP (ORDER BY reputation) FROM users LIMIT $3'
    expect(buildParameterSuggestions(sql, detectParameters(sql), null)).toEqual(
      {
        $1: { value: '0.5', provenance: 'Query shape · percentile' },
        $2: { value: '0.5', provenance: 'Query shape · percentile' },
        $3: { value: '100', provenance: 'Query shape · LIMIT' },
      }
    )
  })

  it('resolves table aliases when the column reference is qualified', () => {
    const sql =
      'SELECT * FROM users u JOIN orders o ON o.shop_id = u.reputation WHERE u.status = $1 AND o.status = $2'
    const suggestions = buildParameterSuggestions(
      sql,
      detectParameters(sql),
      SCHEMA
    )

    expect(suggestions.$1).toEqual({
      value: 'active',
      provenance: 'Schema enum · users.status',
    })
    expect(suggestions.$2).toEqual({
      value: 'paid',
      provenance: 'Schema enum · orders.status',
    })
  })

  it('leaves ambiguous unqualified columns unresolved', () => {
    const sql =
      'SELECT * FROM users u JOIN orders o ON o.shop_id = u.reputation WHERE status = $1'
    expect(
      buildParameterSuggestions(sql, detectParameters(sql), SCHEMA)
    ).toEqual({})
  })

  it('applies column evidence to COALESCE fallbacks and leaves unknowable text empty', () => {
    const sql =
      'SELECT COALESCE(u.plan, $1) AS plan, COALESCE(u.location, $2) AS region FROM users u'
    const suggestions = buildParameterSuggestions(
      sql,
      detectParameters(sql),
      SCHEMA
    )

    expect(suggestions.$1).toEqual({
      value: 'free',
      provenance: 'Schema enum · users.plan',
    })
    expect(suggestions.$2).toBeUndefined()
  })

  it('covers a mixed aggregate query end to end', () => {
    const sql =
      'SELECT COALESCE(u.location, $1) AS region, PERCENTILE_DISC($2) WITHIN GROUP (ORDER BY u.reputation) AS p_rep FROM users u WHERE u.reputation > $3 GROUP BY 1 LIMIT $4'
    const suggestions = buildParameterSuggestions(
      sql,
      detectParameters(sql),
      SCHEMA
    )

    expect(suggestions.$1).toBeUndefined()
    expect(suggestions.$2).toEqual({
      value: '0.5',
      provenance: 'Query shape · percentile',
    })
    expect(suggestions.$3).toEqual({
      value: '1',
      provenance: 'Schema type · users.reputation',
    })
    expect(suggestions.$4).toEqual({
      value: '100',
      provenance: 'Query shape · LIMIT',
    })
  })
})

describe('suggestionSummaryMessage', () => {
  it('reports a complete fill', () => {
    expect(
      suggestionSummaryMessage({
        filled: 2,
        missingBefore: 2,
        schemaAvailable: true,
      })
    ).toBe(
      'Filled 2 of 2 missing parameters. Review suggested values before running.'
    )
  })

  it('scopes partial fills across queries and counts the remainder', () => {
    expect(
      suggestionSummaryMessage({
        filled: 6,
        missingBefore: 9,
        queryCount: 3,
        schemaAvailable: true,
      })
    ).toBe(
      'Filled 6 of 9 missing parameters across 3 queries. 3 need a value you provide. Review suggested values before running.'
    )
  })

  it('explains when nothing safe was found', () => {
    expect(
      suggestionSummaryMessage({
        filled: 0,
        missingBefore: 4,
        schemaAvailable: true,
      })
    ).toBe(
      'No safe suggestions were found. 4 parameters need a value you provide.'
    )
  })

  it('names missing schema evidence as the reason for shape-only fills', () => {
    expect(
      suggestionSummaryMessage({
        filled: 1,
        missingBefore: 4,
        schemaAvailable: false,
      })
    ).toBe(
      'Filled 1 of 4 missing parameters. 3 need a value you provide. Schema evidence is unavailable, so only query-shape suggestions were applied. Review suggested values before running.'
    )
  })

  it('names missing schema evidence when nothing was filled', () => {
    expect(
      suggestionSummaryMessage({
        filled: 0,
        missingBefore: 1,
        schemaAvailable: false,
      })
    ).toBe(
      'No schema evidence is available for this database. 1 parameter needs a value you provide.'
    )
  })
})
