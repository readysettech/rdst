import { describe, expect, it } from 'vitest'
import { inboxItems } from '@/lib/monitor/fixtures'
import type { InboxItem, Incident } from '@/lib/monitor/types'
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
