import { describe, expect, it } from 'vitest'
import type { DatabaseReport } from '@/lib/monitor/types'
import { reportAiOutput } from './reportAiOutput'

const body = {
  summary: 'Load is steady. {{q:aaaaaaaaaaaa}} leads.',
  fix_first: [
    {
      query_key: 'aaaaaaaaaaaa',
      title: 'Add an index on orders',
      why: '{{q:aaaaaaaaaaaa}} scans orders.',
      action: 'Add it',
    },
  ],
  one_fix_several_queries: [
    { table: 'orders', columns: ['c'], query_keys: [], action: 'One index' },
  ],
  health: [
    {
      check: 'cache',
      label: 'Cache hit',
      value: '11%',
      status: 'warn',
      note: 'Raise shared_buffers',
    },
    { check: 'locks', label: 'Locks', value: '0', status: 'ok', note: '' },
  ],
  changed_since_yesterday: [],
  provisional_query_keys: [],
  narrative_error: null,
} as unknown as DatabaseReport['report_json']

const refs = {
  aaaaaaaaaaaa: { display_name: 'SELECT on orders', text_snippet: '' },
}

describe('reportAiOutput', () => {
  it("sends the model's wording with query references named", () => {
    expect(reportAiOutput({ report_json: body, refs })).toBe(
      'Summary:\nLoad is steady. SELECT on orders leads.\n\n' +
        'Fix first:\nAdd an index on orders: SELECT on orders scans orders. Add it\n\n' +
        'One fix for several queries:\nOne index'
    )
  })

  it('has nothing to report without a written narrative', () => {
    expect(
      reportAiOutput({ report_json: body, refs, narrative_missing: true })
    ).toBe('')
    expect(
      reportAiOutput({
        report_json: { ...body, narrative_error: 'AI_OFF' },
        refs,
      } as never)
    ).toBe('')
    expect(reportAiOutput(null)).toBe('')
  })
})
