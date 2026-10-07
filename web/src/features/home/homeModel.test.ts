import { describe, expect, it } from 'vitest'
import type { InboxItem } from '../../lib/monitor/types'
import { healthGlow, inboxStanding } from './homeModel'

const calm = {
  monitored: true,
  failing: false,
  urgent: 0,
  watch: 0,
  openActions: 0,
}

describe('healthGlow', () => {
  it('is green with nothing open', () => {
    expect(healthGlow(calm)).toBe('positive')
  })

  it('is orange with open actions or Watch items', () => {
    expect(healthGlow({ ...calm, openActions: 2 })).toBe('warning')
    expect(healthGlow({ ...calm, watch: 1 })).toBe('warning')
  })

  it('is red with an Urgent item or failing monitoring, whatever else is open', () => {
    expect(healthGlow({ ...calm, urgent: 1, openActions: 3 })).toBe('negative')
    expect(healthGlow({ ...calm, failing: true })).toBe('negative')
  })

  it('stays neutral without monitoring', () => {
    expect(healthGlow({ ...calm, monitored: false, urgent: 1 })).toBe('neutral')
  })
})

function item(fields: Partial<InboxItem>): InboxItem {
  return {
    id: 1,
    target: 'orders',
    type: 'latency_regression',
    subject: 'q',
    state: 'new',
    severity: 0.7,
    title: 'A query got slower',
    body_json: '{}',
    created_at: 0,
    updated_at: 0,
    resolved_at: null,
    resolved_by: null,
    seen_count: 0,
    reopened_count: 0,
    severity_band: 'watch',
    tone: 'warning',
    summary: '',
    query_keys: ['q'],
    subject_label: 'q',
    group_key: 'q',
    muted_by: null,
    trend: [],
    ...fields,
  } as InboxItem
}

describe('inboxStanding', () => {
  it("counts one database's urgent, watch and unread items", () => {
    const items = [
      item({ id: 1, severity: 0.9, severity_band: 'act_now' }),
      item({ id: 2, state: 'seen' }),
      item({ id: 3, target: 'billing' }),
      item({ id: 4, status: 'back_to_normal' }),
    ]
    expect(inboxStanding(items, [], 'orders')).toEqual({
      urgent: 1,
      watch: 1,
      fresh: 1,
    })
  })
})
