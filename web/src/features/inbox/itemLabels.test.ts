import { describe, expect, it } from 'vitest'
import { inboxItems } from '@/lib/monitor/fixtures'
import type { InboxItem } from '@/lib/monitor/types'
import { itemPlace } from './inboxModel'
import { itemLine, itemReason } from './itemHeadline'

const NOW = new Date(2026, 8, 29, 11, 40).getTime() / 1000
const STOP = new Date(2026, 8, 29, 10, 3).getTime() / 1000
const RESUME = new Date(2026, 8, 29, 11, 25).getTime() / 1000
const item = (extra: Partial<InboxItem> = {}): InboxItem => ({
  ...inboxItems(NOW)[0],
  type: 'query_stopped_running',
  state: 'new',
  severity_band: 'fyi',
  status: 'ongoing',
  status_at: STOP,
  created_at: STOP,
  incident: null,
  occurrences: [],
  ...extra,
})

describe('Inbox reason labels', () => {
  it.each([
    'watch',
    'fyi',
  ] as const)('drops the section name from %s rows', (band) => {
    const row = item({
      severity_band: band,
      band_reason: 'FYI: a query stopped running',
    })
    expect(itemPlace(row)).toBe('watch')
    expect(itemReason(row)).toBe('A query stopped running')
  })

  it('prefixes only recovered rows', () => {
    expect(
      itemReason(
        item({ severity_band: 'act_now', band_reason: 'Watch: latency up' })
      )
    ).toBe('Latency up')
    expect(
      itemReason(
        item({
          severity_band: 'recovered',
          band_reason: 'Back to normal: ran again',
        })
      )
    ).toBe('Back to normal: ran again')
  })
})

describe('Stopped query state', () => {
  it('uses the local clock for the last stop without an incident window', () => {
    const line = itemLine(item(), new Map(), NOW)
    expect(line.state).toBe("Hasn't run since 10:03 AM")
    expect(line.when).toBeNull()
    expect(line.ongoing).toBe(true)
  })

  it('uses the latest occurrence after a query stops again', () => {
    const row = item({
      occurrences: [
        {
          start: STOP,
          end: null,
          size: null,
          query_count: 1,
          quiet: false,
          verdict: null,
        },
        {
          start: STOP - 3600,
          end: STOP - 3000,
          size: null,
          query_count: 1,
          quiet: false,
          verdict: null,
        },
      ],
    })
    expect(itemLine(row, new Map(), NOW).state).toBe(
      "Hasn't run since 10:03 AM"
    )
  })

  it('says running again at the measured recovery time', () => {
    const row = item({
      state: 'resolved',
      severity_band: 'recovered',
      status: 'back_to_normal',
      status_at: RESUME,
      resolved_at: RESUME,
    })
    const line = itemLine(row, new Map(), NOW)
    expect(line.state).toBe('Running again at 11:25 AM')
    expect(line.ongoing).toBe(false)
  })

  it('keeps dismissed stops quiet', () => {
    const row = item({
      state: 'dismissed',
      status: 'closed',
      status_at: RESUME,
    })
    expect(itemLine(row, new Map(), NOW).state).toBeNull()
  })
})

it('places recovered status outside Act now even with a stale severity band', () => {
  const row = item({
    severity_band: 'act_now',
    status: 'back_to_normal',
    band_reason: 'Act now: latency up',
  })
  expect(itemPlace(row)).toBe('resolved')
  expect(itemReason(row)).toBe('Back to normal: latency up')
})

it('keeps closed rows from saying still happening with stale ongoing status', () => {
  const line = itemLine(
    item({ state: 'resolved', status: 'ongoing' }),
    new Map(),
    NOW
  )
  expect(line.ongoing).toBe(false)
  expect(line.state ?? '').not.toMatch(/Still happening|Hasn't run/)
})
