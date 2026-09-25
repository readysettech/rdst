import { describe, expect, it } from 'vitest'
import { inboxItems } from '@/lib/monitor/fixtures'
import type { InboxItem, IncidentQuery } from '@/lib/monitor/types'
import { actionTiles } from './actionTileModel'
import {
  CLUSTER_GAP,
  clusterFrame,
  clusterLabel,
  clusterOnsets,
  clusterRoute,
  groupBreakdown,
  jumpFigures,
  jumpSize,
  type MemberJump,
  memberJumps,
  shownFrame,
} from './onsetClusterModel'

// Local times, so the checks hold in any time zone.
const at = (hour: number, minute: number) =>
  new Date(2026, 8, 29, hour, minute).getTime() / 1000
const NOW = at(18, 0)
const MIN = 60

const jump = (key: string, start: number | null, change = 1): MemberJump => ({
  key,
  name: `Query ${key}`,
  start,
  end: null,
  metric: 'calls',
  kind: 'jumped',
  before: 1,
  after: 1 + change,
  change,
  onset: null,
})

const window = (key: string, start: number | null): IncidentQuery => ({
  query_key: key,
  start,
  end: start == null ? null : start + 11 * MIN,
  change: 0.5,
  spark: [],
})

const group = (patch: Partial<InboxItem> = {}): InboxItem => ({
  ...inboxItems(NOW, 'localdev')[0],
  id: 57,
  target: 'demoshop',
  type: 'many_queries_changed',
  state: 'seen',
  title: '4 queries changed at the same time',
  query_keys: ['aaa', 'bbb', 'ccc', 'ddd'],
  measurement: {
    metric: 'calls',
    direction: 'up',
    before: null,
    after: null,
    change: 0.24,
    unit: 'calls_per_second',
    text: '',
  },
  members: [
    {
      query_key: 'ddd',
      display_name: 'SELECT on events',
      before: 0.08,
      after: 0.06,
      change: -0.25,
      impact_ms: 10,
    },
  ],
  body_json: JSON.stringify({
    recovery_queries: {
      aaa: {
        kind: 'traffic_spike',
        baseline_qps: 0.001,
        at_filing: { qps: 0.077, mean_ms: 4 },
      },
      ccc: {
        kind: 'query_stopped_running',
        baseline_qps: 0.085,
        at_filing: { qps: 0, mean_ms: 0 },
      },
    },
    recovered_queries: {
      bbb: {
        kind: 'latency_regression',
        baseline_mean_ms: 9.5,
        baseline_qps: 0.09,
        before: { qps: 0.05, mean_ms: 13.9 },
        after: { qps: 0.07, mean_ms: 10.6 },
      },
    },
  }),
  incident: {
    start: at(15, 4),
    end: null,
    ongoing: true,
    measured: true,
    metric: 'calls',
    change: 0.24,
    queries: [
      window('aaa', at(15, 4)),
      window('bbb', at(15, 5)),
      window('ccc', at(16, 15)),
      window('ddd', null),
    ],
  },
  ...patch,
})

describe('clustering members by onset', () => {
  it('starts a new cluster after a gap of more than 10 minutes', () => {
    const { clusters, untimed } = clusterOnsets([
      jump('a', at(15, 4)),
      jump('b', at(15, 4)),
      jump('c', at(15, 12)),
      jump('d', at(16, 15)),
      jump('e', at(16, 19)),
      jump('f', null),
    ])
    // Newest first; each cluster keeps its first and last onset.
    expect(
      clusters.map((cluster) => cluster.members.map((member) => member.key))
    ).toEqual([
      ['d', 'e'],
      ['a', 'b', 'c'],
    ])
    expect(clusters[1].start).toBe(at(15, 4))
    expect(clusters[1].last).toBe(at(15, 12))
    expect(untimed.map((member) => member.key)).toEqual(['f'])
  })

  it('chains onsets that each stay within the gap of the previous one', () => {
    const starts = [0, 1, 2, 3].map((step) => at(12, 0) + step * CLUSTER_GAP)
    const { clusters } = clusterOnsets(
      starts.map((start, index) => jump(String(index), start))
    )
    expect(clusters).toHaveLength(1)
    const apart = clusterOnsets([
      jump('a', at(12, 0)),
      jump('b', at(12, 0) + CLUSTER_GAP + 60),
    ])
    expect(apart.clusters).toHaveLength(2)
  })

  it('leads each cluster with its largest moves', () => {
    const { clusters } = clusterOnsets([
      jump('small', at(15, 4), 0.2),
      jump('big', at(15, 4), 4),
      jump('mid', at(15, 5), -0.6),
    ])
    expect(clusters[0].members.map((member) => member.key)).toEqual([
      'big',
      'mid',
      'small',
    ])
  })
})

describe('cluster wording', () => {
  it('says how many jumped and when', () => {
    const { clusters } = clusterOnsets([
      jump('a', at(15, 4)),
      jump('b', at(15, 4)),
      jump('c', at(16, 15)),
      jump('d', at(16, 19)),
    ])
    expect(clusterLabel(clusters[0], NOW)).toBe(
      '2 queries jumped from 4:15 to 4:19 PM'
    )
    expect(clusterLabel(clusters[1], NOW)).toBe('2 queries jumped at 3:04 PM')
  })

  it("names a query that jumped alone, and mixed moves as 'changed'", () => {
    const { clusters } = clusterOnsets([
      jump('a', at(11, 50)),
      { ...jump('b', at(11, 58)), kind: 'slowed', metric: 'latency' },
      jump('c', at(15, 4) - 86_400),
    ])
    expect(clusterLabel(clusters[0], NOW)).toBe(
      '2 queries changed from 11:50 to 11:58 AM'
    )
    const noon = clusterOnsets([jump('x', at(11, 55)), jump('y', at(12, 3))])
    expect(clusterLabel(noon.clusters[0], NOW)).toBe(
      '2 queries jumped from 11:55 AM to 12:03 PM'
    )
    expect(clusterLabel(clusters[1], NOW)).toBe(
      'Query c jumped yesterday at 3:04 PM'
    )
  })
})

describe('time frame links', () => {
  it('pads the window and keeps it at least 5 minutes long', () => {
    const [from, to] = clusterFrame({ start: at(15, 4), last: at(15, 4) }, NOW)
    expect(from).toBe(at(15, 1))
    expect(to - from).toBeGreaterThanOrEqual(5 * MIN)
    expect(to).toBe(at(15, 9))
    const wide = clusterFrame({ start: at(16, 15), last: at(16, 19) }, NOW)
    expect(wide).toEqual([at(16, 12), at(16, 24)])
  })

  it('never reaches past now', () => {
    const [from, to] = clusterFrame({ start: NOW - 60, last: NOW - 60 }, NOW)
    expect(to).toBe(NOW)
    expect(to - from).toBeGreaterThanOrEqual(5 * MIN)
  })

  it('opens the Database page on the window, carrying the item', () => {
    const route = clusterRoute(
      group(),
      { start: at(15, 4), last: at(15, 5) },
      NOW
    )
    expect(route.to).toBe('/database')
    expect(route.search).toEqual({
      target: 'demoshop',
      range: '24h',
      zoom: `${at(15, 1)}-${at(15, 10)}`,
      from: 'inbox:57',
    })
    expect(route.search).not.toHaveProperty('query')
  })
})

describe('member jumps', () => {
  it("uses each member's own finding, else the top members, for its numbers", () => {
    const jumps = new Map(
      memberJumps(group(), new Map(), NOW).map((entry) => [entry.key, entry])
    )
    const traffic = jumps.get('aaa')
    expect(traffic).toMatchObject({ metric: 'calls', kind: 'jumped' })
    expect(jumpFigures(traffic as MemberJump)).toEqual({
      label: 'Calls',
      before: '3.6 an hour',
      after: '4.6 a minute',
    })
    const slower = jumps.get('bbb')
    expect(slower).toMatchObject({ metric: 'latency', kind: 'slowed' })
    expect(jumpFigures(slower as MemberJump)?.after).toBe('14 ms')
    expect(jumpSize(slower as MemberJump)).toBe('+46%')
    const stopped = jumps.get('ccc')
    expect(stopped?.kind).toBe('stopped')
    expect(jumpFigures(stopped as MemberJump)?.after).toBe('none')
    expect(jumpSize(stopped as MemberJump)).toBe('stopped')
    const top = jumps.get('ddd')
    expect(top).toMatchObject({ name: 'SELECT on events', start: null })
    expect(jumpSize(top as MemberJump)).toBe('-25%')
  })

  it('sizes every move the same way', () => {
    expect(jumpSize({ kind: 'jumped', change: 68 })).toBe('69x')
    expect(jumpSize({ kind: 'jumped', change: 0.95 })).toBe('2x')
    expect(jumpSize({ kind: 'jumped', change: 0.24 })).toBe('+24%')
    expect(jumpSize({ kind: 'dropped', change: -0.4 })).toBe('-40%')
    expect(jumpSize({ kind: null, change: null })).toBeNull()
  })

  it('says what the members did', () => {
    const jumps = memberJumps(group(), new Map(), NOW)
    expect(groupBreakdown(jumps)).toBe(
      '1 ran more often, 1 got slower, 1 stopped running, 1 ran less often.'
    )
    const { clusters, untimed } = clusterOnsets(jumps)
    expect(clusters.map((cluster) => cluster.members.length)).toEqual([1, 2])
    expect(untimed.map((member) => member.key)).toEqual(['ddd'])
  })
})

describe('action tiles', () => {
  const stopped = (): InboxItem => ({
    ...inboxItems(NOW, 'localdev')[0],
    id: 7,
    target: 'localdev',
    type: 'query_stopped_running',
    state: 'new',
    query_keys: ['9e5f2299915b'],
    link: {
      kind: 'query',
      target: 'localdev',
      query_key: '9e5f2299915b',
      tab: 'performance',
      label: 'Open query',
    },
    advice: {
      summary: "RDST can't see your application.",
      steps: [
        'If a release or a job change removed this query, resolve this item.',
      ],
    },
    incident: {
      start: at(15, 12),
      end: null,
      ongoing: true,
      measured: true,
      metric: 'calls',
      change: -1,
      queries: [],
    },
  })

  it('offers concrete steps that each do something', () => {
    const tiles = actionTiles(stopped(), NOW)
    expect(tiles.map((tile) => tile.title)).toEqual([
      'See when it stopped',
      'See what else ran then',
      'Removed on purpose?',
    ])
    expect(tiles.map((tile) => tile.action.kind)).toEqual([
      'open',
      'open',
      'resolve',
    ])
    const frame = tiles[1].action
    expect(frame.kind === 'open' && frame.route.search.zoom).toBeTruthy()
    for (const tile of tiles) expect(tile.detail).not.toMatch(/[\u2013\u2014]/)
  })

  it('turns SQL advice into copy tiles and leaves closed items alone', () => {
    const stuck = {
      ...stopped(),
      type: 'stuck_query',
      advice: {
        summary: 'A query has been running for a long time.',
        steps: ['Cancel it: SELECT pg_cancel_backend(<pid>);'],
      },
    }
    expect(actionTiles(stuck, NOW)).toEqual([
      expect.objectContaining({
        title: 'Cancel it',
        action: { kind: 'copy', text: 'SELECT pg_cancel_backend(<pid>);' },
      }),
    ])
    expect(actionTiles({ ...stuck, state: 'resolved' }, NOW)).toEqual([])
  })
})

describe('shownFrame', () => {
  const jumps = [jump('a', at(9, 0)), jump('b', at(16, 40))]
  const occurrence = (start: number, end: number | null) => ({
    start,
    end,
    size: 2,
    query_count: 2,
    quiet: false,
    verdict: null,
  })

  it('spans the members onsets for a burst that happened once', () => {
    expect(shownFrame(jumps, [])).toEqual({ start: at(9, 0), last: at(16, 40) })
    expect(shownFrame(jumps, [occurrence(at(9, 0), at(9, 30))])).toEqual({
      start: at(9, 0),
      last: at(16, 40),
    })
  })

  it('shows only the latest occurrence of a repeating burst', () => {
    const entries = [
      occurrence(at(16, 40), at(16, 55)),
      occurrence(at(9, 0), at(9, 20)),
    ]
    const expected = { start: at(16, 40), last: at(16, 55) }
    expect(shownFrame(jumps, entries)).toEqual(expected)
    expect(shownFrame(jumps, [...entries].reverse())).toEqual(expected)
  })

  it('ends a still-going occurrence at its start', () => {
    const entries = [
      occurrence(at(16, 40), null),
      occurrence(at(9, 0), at(9, 20)),
    ]
    expect(shownFrame(jumps, entries)).toEqual({
      start: at(16, 40),
      last: at(16, 40),
    })
  })
})
