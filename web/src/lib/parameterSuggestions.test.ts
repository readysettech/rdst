import { describe, expect, it } from 'vitest'
import type { SchemaDetails } from '../types/schema'
import { buildParameterSuggestions } from './parameterSuggestions'
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
})
