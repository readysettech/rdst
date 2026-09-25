import { describe, expect, it } from 'vitest'
import { inboxItems } from '@/lib/monitor/fixtures'
import type { InboxItem, Incident, IncidentQuery } from '@/lib/monitor/types'
import { bannerText } from './bannerText'
import { itemRoute } from './inboxLinks'
import {
  changeSize,
  fromParam,
  incidentWindow,
  incidentZoom,
  lineOnset,
  parseFrom,
} from './incidentModel'

// Local times, so the checks hold in any time zone.
const at = (hour: number, minute: number) =>
  new Date(2026, 8, 28, hour, minute).getTime() / 1000
const NOW = at(23, 0)
const HOUR = 3600

const incident = (patch: Partial<Incident> = {}): Incident => ({
  start: at(21, 42),
  end: at(22, 15),
  ongoing: false,
  measured: true,
  metric: 'calls',
  change: 2.1,
  queries: [],
  ...patch,
})

const item = (patch: Partial<InboxItem> = {}): InboxItem => ({
  ...inboxItems(NOW, 'localdev')[0],
  id: 42,
  target: 'localdev',
  type: 'traffic_spike',
  state: 'seen',
  query_keys: ['aaaaaaaaaaaa'],
  link: {
    kind: 'query',
    target: 'localdev',
    query_key: 'aaaaaaaaaaaa',
    tab: 'performance',
    label: 'Open query',
  },
  incident: incident(),
  measurement: null,
  body_json: '{}',
  members: undefined,
  ...patch,
})

const group = () =>
  item({
    type: 'many_queries_changed',
    title: 'Traffic jumped on 3 queries',
    query_keys: ['aaaaaaaaaaaa', 'bbbbbbbbbbbb', 'cccccccccccc'],
    link: { kind: 'database', target: 'localdev', label: 'Open database' },
    incident: incident({
      start: at(21, 30),
      end: null,
      ongoing: true,
      queries: [
        {
          query_key: 'bbbbbbbbbbbb',
          start: at(21, 50),
          end: at(22, 20),
          change: 2.1,
          spark: [
            { ts: at(21, 0), value: 1 },
            { ts: at(21, 55), value: 3 },
            { ts: at(22, 50), value: 1 },
          ],
        },
        {
          query_key: 'cccccccccccc',
          start: null,
          end: null,
          change: null,
          spark: [],
        },
      ],
    }),
  })

describe('incident window', () => {
  it("uses a group member's own onset, else the item's", () => {
    expect(incidentWindow(group(), 'bbbbbbbbbbbb')).toMatchObject({
      start: at(21, 50),
      end: at(22, 20),
      ongoing: false,
      change: 2.1,
    })
    expect(incidentWindow(group(), 'cccccccccccc')).toMatchObject({
      start: at(21, 30),
      end: null,
      ongoing: true,
    })
    expect(incidentWindow(item({ incident: null }))).toBeNull()
  })

  it('zooms an hour either side, never past now, in the smallest range that holds it', () => {
    expect(incidentZoom({ start: at(21, 42), end: at(22, 15) }, NOW)).toEqual({
      range: '24h',
      zoom: [at(20, 42), NOW],
    })
    expect(incidentZoom({ start: at(18, 0), end: at(18, 30) }, NOW)).toEqual({
      range: '24h',
      zoom: [at(17, 0), at(19, 30)],
    })
    // Still happening: up to now.
    expect(incidentZoom({ start: at(22, 50), end: null }, NOW).zoom).toEqual([
      at(21, 50),
      NOW,
    ])
    expect(
      incidentZoom(
        { start: NOW - 3 * 86_400, end: NOW - 3 * 86_400 + HOUR },
        NOW
      ).range
    ).toBe('7d')
  })

  it('reads and writes the from param', () => {
    expect(fromParam({ id: 42 })).toBe('inbox:42')
    expect(parseFrom('inbox:42')).toBe(42)
    expect(parseFrom('inbox:x')).toBeNull()
    expect(parseFrom(undefined)).toBeNull()
  })
})

describe('links carry the item', () => {
  it('opens the query zoomed to its window', () => {
    const route = itemRoute(item(), undefined)
    expect(route.to).toBe('/database')
    expect(route.search).toMatchObject({
      target: 'localdev',
      query: 'aaaaaaaaaaaa',
      from: 'inbox:42',
    })
    const [start, end] = route.search.zoom.split('-').map(Number)
    expect(start).toBe(at(20, 42))
    expect(end).toBeGreaterThan(at(22, 15))
  })

  it("opens a group member on that member's own window", () => {
    const route = itemRoute(group(), 'bbbbbbbbbbbb')
    expect(route.search.query).toBe('bbbbbbbbbbbb')
    expect(route.search.zoom.split('-').map(Number)[0]).toBe(at(20, 50))
  })

  it('leaves reports and settings as they are', () => {
    const report = itemRoute(
      item({ link: { kind: 'report', target: 'localdev', label: 'Open' } })
    )
    expect(report.search.from).toBeUndefined()
    const settings = itemRoute(
      item({ link: { kind: 'settings', target: 'localdev', label: 'Open' } })
    )
    expect(settings.search).toEqual({ target: 'localdev', panel: 'settings' })
  })
})

describe('banner', () => {
  it('says when it started and that it is over', () => {
    expect(bannerText(item(), 'aaaaaaaaaaaa', NOW)).toEqual({
      lead: 'Calls up 3.1x at 9:42 PM.',
      state: 'Calls measured back to normal at 10:15 PM.',
      note: null,
      tone: 'over',
    })
  })

  it('says what the numbers did when the item has them', () => {
    const text = bannerText(
      item({
        measurement: {
          metric: 'calls',
          direction: 'up',
          before: 1,
          after: 3,
          change: 2,
          unit: 'calls_per_second',
          text: '',
        },
      }),
      null,
      NOW
    )
    expect(text?.lead).toBe(
      'Calls went from 1 a second to 3 a second at 9:42 PM.'
    )
  })

  it('never says a change of 0%', () => {
    const text = bannerText(
      item({
        type: 'plan_change',
        incident: incident({ change: -0.004, metric: 'latency' }),
        measurement: {
          metric: 'latency',
          direction: 'down',
          before: 0.303,
          after: 0.3015,
          change: -0.005,
          unit: 'ms',
          text: 'Latency down 0%',
        },
      }),
      null,
      NOW
    )
    expect(text?.lead).toBe('Its plan may have changed at 9:42 PM.')
    expect(text?.lead).not.toMatch(/0%/)
  })

  it('says when it is still happening', () => {
    const text = bannerText(
      item({ incident: incident({ end: null, ongoing: true }) }),
      null,
      NOW
    )
    expect(text?.state).toBe('Still happening.')
    expect(text?.tone).toBe('ongoing')
  })

  it("says 'by' when only the filing time is known", () => {
    const text = bannerText(
      item({
        type: 'latency_regression',
        incident: incident({
          measured: false,
          metric: null,
          change: 0.4,
          end: null,
          ongoing: false,
        }),
        measurement: {
          metric: 'latency',
          direction: 'up',
          before: 10,
          after: 14,
          change: 0.4,
          unit: 'ms',
          text: '',
        },
      }),
      null,
      NOW
    )
    expect(text?.lead).toBe('Each call went from 10 ms to 14 ms by 9:42 PM.')
    expect(text?.state).toBeNull()
    expect(text?.tone).toBe('unknown')
  })

  it('says how often a stopped query ran', () => {
    const stopped = (before: number | null) =>
      bannerText(
        item({
          type: 'query_stopped_running',
          incident: incident({ end: null, ongoing: true, change: -1 }),
          measurement: before
            ? {
                metric: 'calls',
                direction: 'down',
                before,
                after: 0,
                change: -1,
                unit: 'calls_per_second',
                text: '',
              }
            : null,
        }),
        null,
        NOW
      )
    expect(stopped(null)?.lead).toBe('Stopped running at 9:42 PM.')
    expect(stopped(0.085)?.lead).toBe(
      'It ran about 5.1 a minute, then stopped at 9:42 PM.'
    )
    expect(stopped(null)?.state).toBe("Hasn't run since 9:42 PM.")
  })

  it('says what happened to a group member, and who moved with it', () => {
    const member = bannerText(group(), 'bbbbbbbbbbbb', NOW)
    expect(member?.lead).toBe("This query's calls rose 3.1x at 9:50 PM.")
    expect(member?.note).toBeNull()
    expect(member?.state).toBe('Calls measured back to normal at 10:20 PM.')
    const base = group()
    const withMates = item({
      ...base,
      incident: incident({
        ...base.incident,
        queries: [
          ...(base.incident?.queries ?? []),
          {
            ...(base.incident?.queries[0] as IncidentQuery),
            query_key: 'aaaaaaaaaaaa',
            start: at(21, 53),
          },
        ],
      }),
    })
    const text = bannerText(withMates, 'bbbbbbbbbbbb', NOW)
    expect(text?.note).toBe('1 other query jumped from 9:50 to 9:53 PM too.')
    for (const words of [text?.lead, text?.note])
      expect(words).not.toMatch(/One of|changed together/)
  })

  it('names the whole group, or the time frame a link opened', () => {
    expect(bannerText(group(), null, NOW)?.lead).toBe(
      '3 queries changed, the first at 9:30 PM.'
    )
    const frame = bannerText(group(), null, NOW, [at(21, 45), at(21, 58)])
    expect(frame?.lead).toBe('Query bbbbbbbb jumped at 9:50 PM.')
    expect(frame?.state).toBe(
      'Every query measured back to normal by 10:20 PM.'
    )
  })
})

describe('banner state follows the item status', () => {
  // Item 148 on localdev: a repeating burst whose latest occurrence measured back to
  // normal while its members' own windows never closed.
  const burst = (patch: Partial<InboxItem> = {}) =>
    item({
      type: 'many_queries_changed',
      title: '2 queries got slower at the same time',
      state: 'new',
      query_keys: ['aaaaaaaaaaaa', 'bbbbbbbbbbbb'],
      link: { kind: 'database', target: 'localdev', label: 'Open database' },
      measurement: {
        metric: 'latency',
        direction: 'up',
        before: null,
        after: null,
        change: 3.36,
        unit: 'ms',
        text: 'Median latency up 4.4x across 2 queries',
      },
      incident: incident({
        start: at(21, 50),
        end: at(22, 40),
        ongoing: false,
        metric: 'latency',
        change: 3.36,
        queries: ['aaaaaaaaaaaa', 'bbbbbbbbbbbb'].map((key) => ({
          query_key: key,
          start: at(21, 50),
          end: null,
          change: 4.4,
          spark: [],
        })),
      }),
      severity_band: 'recovered',
      tone: 'positive',
      status: 'back_to_normal',
      status_at: at(22, 40),
      band_reason: 'Back to normal: latency up 4.4x on 2 queries at once',
      ...patch,
    })

  it('reads back to normal in the time frame, for each query and for the group', () => {
    const frame = bannerText(burst(), null, NOW, [at(21, 45), at(21, 58)])
    expect(frame?.state).toBe(
      'Every query measured back to normal by 10:40 PM.'
    )
    expect(frame?.tone).toBe('over')
    const member = bannerText(burst(), 'aaaaaaaaaaaa', NOW)
    expect(member?.state).toBe('Latency measured back to normal at 10:40 PM.')
    expect(bannerText(burst(), null, NOW)?.tone).toBe('over')
  })

  it('says still happening only when the item does', () => {
    const ongoing = burst({
      status: 'ongoing',
      status_at: at(21, 50),
      severity_band: 'act_now',
      incident: { ...(burst().incident as Incident), ongoing: true, end: null },
    })
    const frame = bannerText(ongoing, null, NOW, [at(21, 45), at(21, 58)])
    expect(frame?.state).toBe('Still happening.')
    expect(bannerText(ongoing, 'aaaaaaaaaaaa', NOW)?.state).toBe(
      'Still happening.'
    )
  })

  it("follows a single query's status over its open window", () => {
    const single = item({
      state: 'new',
      incident: incident({
        end: null,
        ongoing: false,
        queries: [
          {
            query_key: 'aaaaaaaaaaaa',
            start: at(21, 42),
            end: null,
            change: 2.1,
            spark: [],
          },
        ],
      }),
      status: 'back_to_normal',
      status_at: at(22, 15),
    })
    const text = bannerText(single, 'aaaaaaaaaaaa', NOW)
    expect(text?.state).toBe('Calls measured back to normal at 10:15 PM.')
    expect(text?.tone).toBe('over')
  })
})

describe('banner times', () => {
  it('reads naturally for yesterday and older days', () => {
    const yesterday = at(21, 42) - 86_400
    const text = bannerText(
      item({ incident: incident({ start: yesterday, end: yesterday + 1800 }) }),
      null,
      NOW
    )
    expect(text?.lead).toBe('Calls up 3.1x yesterday at 9:42 PM.')
    expect(text?.state).toBe(
      'Calls measured back to normal yesterday at 10:12 PM.'
    )
    const older = bannerText(
      item({ incident: incident({ start: at(21, 42) - 3 * 86_400 }) }),
      null,
      NOW
    )
    expect(older?.lead).toBe('Calls up 3.1x on Sep 25 at 9:42 PM.')
  })
})

describe('query line onsets', () => {
  it('says when each member jumped and by how much, with its sparkline', () => {
    const onset = lineOnset(group(), 'bbbbbbbbbbbb', NOW)
    expect(onset?.text).toBe('jumped 9:50 PM, 3.1x')
    expect(onset?.when).toBe('jumped 9:50 PM')
    expect(onset?.points).toHaveLength(3)
    expect(onset?.domain).toEqual([at(21, 0), at(22, 50)])
    expect(lineOnset(group(), 'cccccccccccc', NOW)).toBeNull()
  })

  it("uses a single item's window and trend only when measured", () => {
    const single = item({
      trend: [
        { ts: at(21, 0), value: 1 },
        { ts: at(22, 0), value: 3 },
      ],
    })
    expect(lineOnset(single, 'aaaaaaaaaaaa', NOW)?.text).toBe(
      'jumped 9:42 PM, 3.1x'
    )
    expect(
      lineOnset(
        item({ incident: incident({ measured: false }) }),
        'aaaaaaaaaaaa',
        NOW
      )
    ).toBeNull()
  })

  it('sizes changes as times or percent', () => {
    expect(changeSize(2.1)).toBe('3.1x')
    expect(changeSize(0.4)).toBe('40%')
    expect(changeSize(-0.6)).toBe('2.5x')
    expect(changeSize(-0.2)).toBe('20%')
  })
})

it.each([
  'resolved',
  'dismissed',
] as const)('ends %s windows even with stale incident data', (state) => {
  const alert = item({
    state,
    incident: incident({
      ongoing: true,
      end: null,
      queries: [
        {
          query_key: 'a',
          start: NOW - 60,
          end: null,
          change: 2.1,
          spark: [],
        },
      ],
    }),
  })
  expect(incidentWindow(alert)?.ongoing).toBe(false)
  expect(incidentWindow(alert, 'a')?.ongoing).toBe(false)
})
