import { expect, it } from 'vitest'
import type {
  DaySnapshot,
  ReportSummary,
  SnapshotNumber,
} from '@/lib/monitor/types'
import {
  activeSection,
  briefItems,
  briefTarget,
  chipColors,
  comparisonLine,
  csvFileName,
  databaseHref,
  dayLine,
  dayList,
  dayTitle,
  eventState,
  fixLine,
  metricBands,
  neighbors,
  numberView,
  periodLine,
  queriesCsv,
  reportSections,
  sortQueries,
  spikeSize,
  spikeWhen,
  vitalPoints,
} from './snapshotModel'

const DAY = 86400

function summary(patch: Partial<ReportSummary>): ReportSummary {
  return {
    id: 1,
    ts: 1_790_661_660,
    trigger: 'nightly',
    duration_s: 60,
    headline: '',
    counts: { fix_first: 0, one_fix: 0, health_warn: 0, health_ok: 0 },
    provisional_count: 0,
    ...patch,
  }
}

function number(patch: Partial<SnapshotNumber>): SnapshotNumber {
  return {
    metric: 'mean_ms',
    label: 'Average latency',
    unit: 'ms',
    value: 10,
    prior: 10,
    change: 0,
    better: 'down',
    ...patch,
  }
}

it('titles a day by its weekday and date, with the year only when it differs', () => {
  const now = new Date(2026, 8, 29, 10).getTime() / 1000
  expect(dayTitle('2026-09-28', now)).toBe('Monday, September 28')
  expect(dayTitle('2025-09-28', now)).toBe('Sunday, September 28, 2025')
})

it('keeps one entry per day: real over sample, a full day over today so far', () => {
  const days = dayList([
    summary({ id: 11, day: '2026-09-28', ts: 100, period_partial: true }),
    summary({ id: 10, day: '2026-09-28', ts: 90 }),
    summary({ id: 9, day: '2026-09-27', ts: 80, sample: true }),
    summary({ id: 8, day: '2026-09-27', ts: 70 }),
    summary({ id: 7, day: '2026-09-26', ts: 60, sample: true }),
  ])
  expect(days.map((entry) => [entry.day, entry.id, entry.sample])).toEqual([
    ['2026-09-28', 10, false],
    ['2026-09-27', 8, false],
    ['2026-09-26', 7, true],
  ])
  expect(neighbors(days, 8)).toEqual({ older: days[2], newer: days[0] })
  expect(neighbors(days, 10).newer).toBeNull()
})

it('says what a day held in a few words', () => {
  expect(dayLine(summary({ events: 4, open_events: 1, look_at: 3 }))).toBe(
    '4 events, 1 still open · 3 queries to look at'
  )
  expect(dayLine(summary({ events: 0, look_at: 0 }))).toBe('Nothing unusual')
})

it('colors a delta by whether the change is good for that number', () => {
  const slower = numberView(number({ value: 20, prior: 8 }))
  expect(slower).toMatchObject({ delta: '2.5x', direction: 'up', tone: 'bad' })
  const faster = numberView(number({ value: 6, prior: 8 }))
  expect(faster).toMatchObject({
    delta: '-25%',
    direction: 'down',
    tone: 'good',
  })
  expect(numberView(number({ value: 10.2, prior: 10 })).delta).toBe('same')
  // Traffic is neither good nor bad.
  const traffic = numberView(
    number({ metric: 'qps', unit: 'qps', value: 30, prior: 20, better: null })
  )
  expect(traffic).toMatchObject({ tone: 'neutral', value: '30', unit: '/s' })
  // A ratio moves in points.
  const hit = numberView(
    number({ unit: 'ratio', value: 0.9, prior: 0.99, better: 'up' })
  )
  expect(hit).toMatchObject({ delta: '-9.0 pts', tone: 'bad', prior: '99%' })
  expect(numberView(number({ prior: null })).prior).toBeNull()
})

it('links a query to the Database page zoomed to its day', () => {
  const now = 1_790_700_000
  const start = now - 30 * 3600
  const href = databaseHref({
    target: 'demoshop',
    queryKey: 'abc123abc123',
    period: { start, end: start + DAY },
    now,
  })
  const search = new URL(href, 'http://x').searchParams
  expect(search.get('target')).toBe('demoshop')
  expect(search.get('query')).toBe('abc123abc123')
  // 24h doesn't reach back to the day's start; 7d does.
  expect(search.get('range')).toBe('7d')
  expect(search.get('zoom')).toBe(`${start}-${start + DAY}`)
  // Today so far ends now.
  const today = databaseHref({
    target: 'demoshop',
    period: { start: now - 3600, end: now + 20 * 3600 },
    now,
  })
  expect(new URL(today, 'http://x').searchParams.get('zoom')).toBe(
    `${now - 3600}-${now}`
  )
})

it('gives charted queries their band color and leaves the rest plain', () => {
  const colors = chipColors({
    chart: {
      bands: [
        { key: 'a', kind: 'query', color_index: 0 },
        { key: 'b', kind: 'query', color_index: 2 },
        { key: 'other', kind: 'other', color_index: null },
      ],
    },
  } as unknown as DaySnapshot)
  expect(colors.get('a')).toBe('var(--mk-band-1)')
  expect(colors.get('b')).toBe('var(--mk-band-3)')
  expect(colors.has('other')).toBe(false)
})

it('words an event state and sorts the day list', () => {
  expect(eventState({ kind: 'created', state: 'open' }).label).toBe(
    'Still open'
  )
  expect(eventState({ kind: 'index', state: 'done' }).label).toBe('Applied')
  const rows = [
    {
      display_name: 'b',
      share: 0.1,
      calls: 50,
      mean_ms: 1,
      latency_change: null,
    },
    { display_name: 'a', share: 0.5, calls: 5, mean_ms: 9, latency_change: 2 },
  ]
  expect(sortQueries(rows, 'share')[0].display_name).toBe('a')
  expect(sortQueries(rows, 'calls')[0].display_name).toBe('b')
  expect(sortQueries(rows, 'name')[0].display_name).toBe('a')
})

it('keeps index definitions out of the fix line and dates a later fix', () => {
  expect(fixLine("Add an index on events(event_type, ((props->>'id')))")).toBe(
    'add an index on events'
  )
  const end = new Date(2026, 8, 29).getTime() / 1000
  expect(
    eventState(
      { kind: 'created', state: 'resolved', resolved_at: end + 3600 },
      end
    ).label
  ).toMatch(/^Back to normal Tue at 1:00/)
})

it('words an ended event as observed, never as resolved', () => {
  const end = new Date(2026, 8, 29).getTime() / 1000
  const back = eventState(
    { kind: 'created', state: 'resolved', resolved_at: end - 3 * 3600 },
    end
  )
  expect(back.label).toMatch(/^Back to normal at \d{1,2}:\d{2} [AP]M$/)
  expect(back.tone).toBe('positive')
  // A line that is itself the return to normal needs no second label.
  expect(eventState({ kind: 'resolved', state: 'open' }).label).toBe('')
})

it('switches the chart between queries/s, latency and share of time', () => {
  const chart = {
    res: 300,
    start: 0,
    end: 600,
    xs: [0, 300],
    bands: [
      {
        key: 'a',
        kind: 'query',
        display_name: 'A',
        color_index: 0,
        share: 0.7,
        calls: 1,
        qps: 1,
        mean_ms: 1,
      },
      {
        key: 'other',
        kind: 'other',
        display_name: 'Everything else',
        color_index: null,
        share: 0.3,
        calls: 1,
        qps: 1,
        mean_ms: 1,
      },
    ],
    qps: [
      [1, 2],
      [3, null],
    ],
    ms: [
      [30, 10],
      [10, null],
    ],
    mean_ms: [
      [2, 4],
      [1, null],
    ],
    overall_mean_ms: [1.5, 4],
    markers: [],
  } as DaySnapshot['chart'] & object
  expect(metricBands(chart, 'calls').map((band) => band.values)).toEqual([
    [1, 2],
    [3, null],
  ])
  const time = metricBands(chart, 'time')
  expect(time[0].values).toEqual([75, 100])
  expect(time[1].values).toEqual([25, null])
  const latency = metricBands(chart, 'latency')
  // Latency leaves out "everything else" and adds every query as a dashed line.
  expect(latency.map((band) => band.id)).toEqual(['a', 'overall'])
  expect(latency[1].reference).toBe(true)
  expect(
    vitalPoints(
      {
        res: 300,
        xs: [0, 300],
        qps: [],
        mean_ms: [],
        active_conns: [],
        cache_hit_ratio: [0.9, null],
        xact_rollback: [],
        deadlocks: [],
      },
      'cache_hit_ratio'
    )
  ).toEqual([
    { x: 0, y: 90 },
    { x: 300, y: null },
  ])
  expect(briefTarget('spike')).toBe('snapshot-chart')
  expect(briefTarget('todo')).toBe('snapshot-actions')
  expect(briefTarget(null)).toBeNull()
})

it('sizes a spike against the usual level', () => {
  const spike = {
    id: 'S1',
    metric: 'latency' as const,
    start: 0,
    end: 1500,
    peak_ts: 300,
    peak: 27.9,
    usual: 5.15,
    ratio: 5.42,
    queries: [],
    events: [],
    summary: '',
  }
  expect(spikeSize(spike)).toMatchObject({
    peak: '27.9 ms',
    usual: '5.15 ms',
    ratio: '5.4x',
  })
  expect(spikeWhen(spike)).toMatch(/ to /)
  expect(spikeWhen({ ...spike, end: 300 })).toMatch(/^At /)
})

it('exports every query of the day as CSV, quoting what needs it', () => {
  const row = {
    query_key: 'abc',
    display_name: 'SELECT on "orders", by id',
    calls: 10,
    qps: 0.5,
    total_ms: 20,
    mean_ms: 2,
    share: 0.25,
    prior_calls: 5,
    prior_mean_ms: 1,
    latency_change: 1,
    status: 'fix_ready' as const,
    status_reason: null,
    score: 55,
    grade: 'C' as const,
    fix: {
      kind: 'index',
      title: 'Add an index',
      sql: 'CREATE INDEX i ON orders (id);',
    },
    analysis_ref: null,
    analysis_mode: null,
    analyzed_at: null,
    headline: null,
    provisional: false,
    new: false,
    text: 'SELECT id\nFROM orders',
    findings: ['Seq scan', 'No index'],
  }
  const csv = queriesCsv([row])
  const [header, line] = csv.split('\r\n')
  expect(header.split(',')).toContain('fix_sql')
  expect(header.split(',').at(-1)).toBe('sql')
  expect(
    line.startsWith('"SELECT on ""orders"", by id",abc,10,0.5,20,2,0.25')
  ).toBe(true)
  expect(line).toContain('Fix suggested')
  expect(line).toContain('Seq scan | No index')
  expect(csv).toContain('"SELECT id\nFROM orders"')
  expect(csvFileName('demo shop', '2026-09-28')).toBe(
    'demo-shop-queries-2026-09-28.csv'
  )
})

it('titles a whole day by its date alone; a day so far says as of when', () => {
  const noon = new Date(2026, 8, 30, 14, 15).getTime() / 1000
  const start = new Date(2026, 8, 30).getTime() / 1000
  const whole = {
    start: start - DAY,
    end: start,
    partial: false,
    day: '2026-09-29',
  }
  expect(periodLine(whole, start + 3 * 3600 + 540, noon)).toBeNull()
  const today = { start, end: noon, partial: true, day: '2026-09-30' }
  expect(periodLine(today, noon, noon)).toBe('Today so far, as of 2:15 PM')
  expect(periodLine({ ...today, day: '2026-09-29' }, noon, noon + DAY)).toBe(
    'So far, as of 2:15 PM'
  )
  expect(
    periodLine({ ...whole, rolling: true }, start, noon)?.startsWith(
      'The 24 hours up to'
    )
  ).toBe(true)
})

it('compares with the previous day only when it was recorded', () => {
  const monday = new Date(2026, 8, 28).getTime() / 1000
  const tuesday = monday + DAY
  const period = {
    start: tuesday,
    end: tuesday + DAY,
    partial: false,
    day: '2026-09-29',
  }
  expect(
    comparisonLine({
      period: { ...period, prior_start: monday, prior_end: tuesday },
      numbers: [number({ prior: 8 })],
    })
  ).toBe('Against Monday')
  expect(
    comparisonLine({
      period: {
        ...period,
        prior_start: monday,
        prior_observed_from: monday + 16 * 3600 + 45 * 60,
      },
      numbers: [number({ prior: 8 })],
    })
  ).toBe('Against Monday, recorded from 4:45 PM')
  // The first recorded day, and an older report that named a day it had no numbers for.
  expect(
    comparisonLine({
      period: { ...period, prior_start: null },
      numbers: [number({})],
    })
  ).toBeNull()
  expect(
    comparisonLine({
      period: { ...period, prior_start: monday },
      numbers: [number({ prior: null })],
    })
  ).toBeNull()
})

it('keeps the day in brief to its top three priorities, each with its line', () => {
  const items = Array.from({ length: 5 }, (_, index) => ({
    topic: 'health',
    text: `Finding ${index}.`,
    why: `What finding ${index} means.`,
  }))
  const brief = briefItems({ brief: [], brief_items: items })
  expect(brief.map((item) => item.text)).toEqual([
    'Finding 0.',
    'Finding 1.',
    'Finding 2.',
  ])
  expect(brief[0].why).toBe('What finding 0 means.')
  expect(briefItems({ brief: ['One.', 'Two.'], brief_items: [] })).toEqual([
    { topic: null, text: 'One.' },
    { topic: null, text: 'Two.' },
  ])
})

it('lists the sections in page order and marks the one being read', () => {
  const base = {
    brief: ['One.'],
    brief_items: [],
    chart: null,
    spikes: [],
    events: [],
    look_at: [],
    queries: [],
    actions: [],
  } as unknown as DaySnapshot
  expect(reportSections(base).map((section) => section.label)).toEqual([
    'Health',
    'Numbers',
    'Brief',
    'What to change',
    'What happened',
    'Queries',
  ])
  // A past day leaves out what to change now.
  expect(
    reportSections(base, false).some(
      (section) => section.id === 'snapshot-actions'
    )
  ).toBe(false)
  const full = {
    ...base,
    chart: {} as DaySnapshot['chart'],
    spikes: [{}, {}],
    queries: [{}, {}, {}],
  } as unknown as DaySnapshot
  const sections = reportSections(full)
  expect(sections.map((section) => section.id)).toEqual([
    'snapshot-health',
    'snapshot-numbers',
    'snapshot-brief',
    'snapshot-actions',
    'snapshot-chart',
    'snapshot-spikes',
    'snapshot-events',
    'snapshot-look-at',
    'snapshot-all',
  ])
  expect(
    sections.find((section) => section.id === 'snapshot-spikes')?.count
  ).toBe(2)
  const tops = [
    { id: 'a', top: 40 },
    { id: 'b', top: 300 },
    { id: 'c', top: 900 },
  ]
  expect(activeSection(tops, 20, false)).toBe('a')
  expect(activeSection(tops, 320, false)).toBe('b')
  expect(activeSection(tops, 320, true)).toBe('c')
  expect(activeSection([], 320, false)).toBeNull()
})
