import { describe, expect, it } from 'vitest'
import { inboxItems } from '@/lib/monitor/fixtures'
import type { InboxItem } from '@/lib/monitor/types'
import { actionTiles } from './actionTileModel'
import { recoveryPhrase } from './inboxModel'
import { itemLine, recurringText, smallerText } from './itemHeadline'

const NOW = new Date(2026, 8, 29, 23, 40).getTime() / 1000
const END = new Date(2026, 8, 29, 23, 25).getTime() / 1000

const base = (type: string, extra: Partial<InboxItem> = {}): InboxItem => ({
  ...inboxItems(NOW)[0],
  type,
  ...extra,
})

describe('recoveryPhrase', () => {
  it('names the reading that went back to normal', () => {
    expect(recoveryPhrase(base('latency_regression'), END, NOW)).toBe(
      'Latency back to normal at 11:25 PM'
    )
    expect(recoveryPhrase(base('traffic_spike'), END, NOW)).toBe(
      'Calls back to normal at 11:25 PM'
    )
    expect(recoveryPhrase(base('cache_hit_ratio_drop'), END, NOW)).toBe(
      'Cache hit ratio back to normal at 11:25 PM'
    )
    expect(recoveryPhrase(base('query_stopped_running'), END, NOW)).toBe(
      'Running again at 11:25 PM'
    )
  })
})

describe('itemLine', () => {
  it('folds an item to who, when and how it ended', () => {
    const item = base('latency_regression', {
      incident: {
        start: END - 1800,
        end: END,
        ongoing: false,
        measured: true,
        metric: 'latency',
        change: 2,
        queries: [],
      },
    })
    const line = itemLine(item, new Map(), NOW)
    expect(line.when).toBe('from 10:55 PM')
    expect(line.state).toBe('Latency back to normal at 11:25 PM')
    expect(line.ongoing).toBe(false)
  })

  it('says when an open change is still happening', () => {
    const item = base('traffic_spike', {
      incident: {
        start: END,
        end: null,
        ongoing: true,
        measured: true,
        metric: 'calls',
        change: 1,
        queries: [],
      },
    })
    const line = itemLine(item, new Map(), NOW)
    expect(line.when).toBe('since 11:25 PM')
    expect(line.state).toBe('Still happening')
  })
})

describe('recurringText and smallerText', () => {
  it('explains a scheduled burst that alerted anyway', () => {
    expect(
      recurringText({
        kind: 'period',
        period_s: 45 * 60,
        repeats: 4,
        last_onset: END,
        verdict: 'bigger',
      })
    ).toBe(
      "This happens about every 45 minutes, and this time it's bigger than usual."
    )
    expect(
      recurringText({
        kind: 'daily',
        period_s: 86_400,
        repeats: 3,
        last_onset: END,
        verdict: 'longer',
      })
    ).toBe(
      "This happens every day around 11:25 PM, and this time it's lasting longer than usual."
    )
  })

  it('counts the smaller queries that moved, and says nothing for none', () => {
    expect(smallerText(60)).toBe(
      '60 smaller queries also moved at the same time.'
    )
    expect(smallerText(0)).toBeNull()
  })
})

describe('action tiles', () => {
  it('give each kind of step its own kind', () => {
    const item = base('query_stopped_running', {
      state: 'new',
      link: {
        kind: 'query',
        target: 'localdev',
        query_key: 'abc',
        tab: 'performance',
        label: 'Open query',
      },
      incident: {
        start: END,
        end: null,
        ongoing: true,
        measured: true,
        metric: 'calls',
        change: -1,
        queries: [],
      },
    })
    const kinds = actionTiles(item, NOW).map((tile) => tile.kind)
    expect(kinds).toEqual(['chart', 'frame', 'resolve'])
    expect(new Set(actionTiles(item, NOW).map((tile) => tile.icon)).size).toBe(
      3
    )
  })
})

describe('itemLine for a repeating burst', () => {
  it('reads by its latest occurrence', () => {
    const item = base('many_queries_changed', {
      state: 'seen',
      occurrences: [
        {
          start: END,
          end: null,
          size: 3,
          query_count: 3,
          quiet: true,
          verdict: 'usual',
        },
        {
          start: END - 3000,
          end: END - 2400,
          size: 3,
          query_count: 3,
          quiet: false,
          verdict: null,
        },
      ],
    })
    const line = itemLine(item, new Map(), NOW)
    expect(line.when).toBe('latest since 11:25 PM')
    expect(line.state).toBe('Still happening')
  })
})

describe('itemLine follows the backend status', () => {
  const occurrences = [
    { start: END - 600, end: null, size: 3, query_count: 4, quiet: false, verdict: null },
    { start: END - 7200, end: END - 6600, size: 3, query_count: 2, quiet: false, verdict: null },
  ]

  it('reads a repeat back to normal when its latest occurrence measured so', () => {
    const item = base('many_queries_changed', {
      state: 'seen',
      occurrences,
      status: 'back_to_normal',
      status_at: END,
    })
    const line = itemLine(item, new Map(), NOW)
    expect(line.ongoing).toBe(false)
    expect(line.state).toBe('Every query back to normal at 11:25 PM')
  })

  it('says still happening only while the backend does', () => {
    const item = base('many_queries_changed', {
      state: 'seen',
      occurrences,
      status: 'ongoing',
    })
    expect(itemLine(item, new Map(), NOW).state).toBe('Still happening')
  })
})
