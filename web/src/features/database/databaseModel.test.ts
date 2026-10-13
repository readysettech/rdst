import { describe, expect, it } from 'vitest'
import type { MonitorPulseState } from '@/features/monitor-kit/useMonitorPulse'
import { at, CA_KEYS, CA_NOW, caFixtures } from '@/lib/monitor/fixturesCa'
import type { MonitorQuery } from '@/lib/monitor/types'
import {
  analysesWaitForSignIn,
  analysisUnderWay,
  barState,
  chartMarkers,
  DASH,
  deltaLabel,
  filterRows,
  fixCount,
  healthMetrics,
  markerClock,
  newestJobs,
  pagerLabel,
  readingClock,
  rowActivity,
  rowChip,
  scorePoor,
  sectionMeta,
  sortRows,
  swatchColor,
} from './databaseModel'

const demo = caFixtures('ca-database')
const rows = demo.queries('demo', '1h', 500, 0).queries

function pulse(
  state: MonitorPulseState['state'],
  extra: Partial<MonitorPulseState> = {}
): MonitorPulseState {
  return {
    pulses: [],
    last: null,
    lastCollect: null,
    state,
    nextAt: null,
    now: CA_NOW,
    clock: {
      lastAt: null,
      nextAt: null,
      period: 60,
      fraction: 0,
      overdue: false,
    },
    beat: 0,
    isLoading: false,
    ...extra,
  } as MonitorPulseState
}

describe('barState', () => {
  it('reads Watching while readings arrive', () => {
    const state = barState(
      demo.settings('demo'),
      pulse('live'),
      demo.health('demo', '1h')
    )
    expect(state).toMatchObject({ word: 'Watching', tone: 'live', since: null })
  })

  it("reads Can't reach with the outage start", () => {
    const set = caFixtures('ca-unreachable')
    const state = barState(
      set.settings('database'),
      pulse('connection_lost'),
      set.health('database', '1h')
    )
    expect(state).toMatchObject({
      word: "Can't reach",
      tone: 'negative',
      since: at(9, 41),
    })
  })

  it('reads Not monitored when monitoring is off', () => {
    const set = caFixtures('ca-unmonitored')
    expect(
      barState(set.settings('reports'), pulse('paused'), undefined)
    ).toMatchObject({ word: 'Not monitored', tone: 'off' })
  })

  it('follows the API when it says AI checks are paused', () => {
    const health = demo.health('demo', '1h')
    expect(
      barState(demo.settings('demo'), pulse('live'), health, 'ai_paused')
    ).toMatchObject({ word: 'AI checks paused', tone: 'warning' })
    expect(
      barState(
        demo.settings('demo'),
        pulse('live', { firstReading: true }),
        health,
        'ai_paused'
      )
    ).toMatchObject({ word: 'AI checks paused', tone: 'warning' })
    const set = caFixtures('ca-unmonitored')
    expect(
      barState(set.settings('reports'), pulse('paused'), undefined, 'ai_paused')
    ).toMatchObject({ word: 'Not monitored', tone: 'off' })
  })
})

describe('held analyses', () => {
  const SIGN_IN = 'Sign in to turn on AI monitoring'
  const query = (
    last: Partial<NonNullable<MonitorQuery['last_analysis']>> | null
  ): MonitorQuery => ({
    ...rows[0],
    status: 'analyzing',
    last_analysis: last
      ? ({
          state: 'queued',
          finished_at: null,
          analysis_ref: null,
          mode: '',
          param_source: null,
          reason: null,
          ...last,
        } as MonitorQuery['last_analysis'])
      : null,
  })

  it('says Waiting for sign-in, never Analyzing, while a job waits for a sign-in', () => {
    const held = query({
      queue_state: 'held',
      held_reason: 'sign_in',
      reason: SIGN_IN,
    })
    expect(rowChip(held)?.label).toBe('Waiting for sign-in')
    expect(analysisUnderWay(held)).toBe(false)
    // Queued until it starts, then Analyzing.
    expect(rowChip(query({ queue_state: 'today' }))?.label).toBe('Queued')
    expect(
      rowChip(query({ state: 'running', queue_state: 'running' }))?.label
    ).toBe('Analyzing')
    expect(
      rowChip(query({ queue_state: 'held', held_reason: 'ai_paused' }))?.label
    ).toBe('On hold')
    expect(analysesWaitForSignIn(undefined, [held])).toBe(true)
    expect(analysesWaitForSignIn(undefined, [query(null)])).toBe(false)
  })

  it('reads the first pass held for a sign-in from the checkup', () => {
    const checkup = {
      state: 'first_pass',
      analyzed: 0,
      total: 10,
      due_by: null,
      report_id: null,
      finished_at: null,
    } as const
    expect(
      analysesWaitForSignIn(
        { ...checkup, held: 'sign_in', held_text: SIGN_IN },
        []
      )
    ).toBe(true)
    expect(analysesWaitForSignIn({ ...checkup, held: null }, [])).toBe(false)
  })

  it('says Failed when the last analysis did not finish, but not while it retries', () => {
    expect(
      rowChip(query({ state: 'failed', reason: 'Plan failed' }))?.label
    ).toBe('Failed')
    expect(
      rowChip(query({ state: 'failed', error_kind: 'retrying' }))?.label
    ).not.toBe('Failed')
    expect(
      rowChip({
        ...query({ state: 'failed' }),
        status: 'not_analyzed',
      })?.label
    ).toBe('Failed')
  })

  it('drops Analyzing once monitoring is off, unless a job still runs', () => {
    expect(rowChip(query({ queue_state: 'today' }), false)).toBeNull()
    expect(
      rowChip(
        query({ queue_state: 'held', held_reason: 'sign_in', reason: SIGN_IN }),
        false
      )
    ).toBeNull()
    const running = query({ state: 'running', queue_state: 'running' })
    expect(rowChip(running, false)?.label).toBe('Analyzing')
    expect(analysisUnderWay(running, false)).toBe(true)
  })
})

describe('healthMetrics', () => {
  const health = demo.health('demo', '1h')

  it('gives the hour its four numbers with the cache reason from the report', () => {
    const metrics = healthMetrics(health, {
      day: false,
      cacheReason: 'shared_buffers is the default',
      firstRun: false,
    })
    expect(metrics.map((metric) => metric.label)).toEqual([
      'Queries per second',
      'Connections',
      'Cache hit',
      'Rollbacks and deadlocks',
    ])
    expect(metrics[0].value).toBe('14.2')
    expect(metrics[0].detail).toMatch(/^peak \d+\.\d$/)
    expect(metrics[1]).toMatchObject({
      value: '8',
      unit: 'of 100',
      detail: 'steady',
    })
    expect(metrics[2]).toMatchObject({
      value: '12.5',
      unit: '%',
      tone: 'warning',
      reason: 'shared_buffers is the default',
    })
    expect(metrics[3]).toMatchObject({
      value: '14',
      unit: 'a minute',
      detail: '0 deadlocks',
    })
  })

  it('reads the day as averages, the lowest cache hit and a day of rollbacks', () => {
    const metrics = healthMetrics(demo.health('demo', '24h'), {
      day: true,
      cacheReason: null,
      firstRun: false,
    })
    expect(metrics[0].detail).toMatch(/^average, peak \d+\.\d at \d+:\d\d$/)
    expect(metrics[2].label).toBe('Lowest cache hit')
    expect(metrics[3].unit).toBe('in 24 h')
  })

  it('takes the peak from the period, the same one Home and the report show', () => {
    const day = demo.health('demo', '24h')
    const metrics = healthMetrics(
      {
        ...day,
        period: { ...day.period, qps_peak: 77.3, qps_peak_at: CA_NOW - 600 },
      },
      { day: true, cacheReason: null, firstRun: false }
    )
    expect(metrics[0].detail).toBe(
      `average, peak 77.3 at ${markerClock(CA_NOW - 600, 0)}`
    )
    const none = healthMetrics(
      { ...day, period: { ...day.period, qps_peak: null, qps_peak_at: null } },
      { day: false, cacheReason: null, firstRun: false }
    )
    expect(none[0].detail).toBeUndefined()
  })

  it('says collecting for what has no reading in the first minutes', () => {
    const set = caFixtures('ca-first-run')
    const metrics = healthMetrics(set.health('demo', '1h'), {
      day: false,
      cacheReason: null,
      firstRun: true,
    })
    expect(metrics[0].detail).toMatch(/min so far$/)
    expect(metrics[2]).toMatchObject({ value: DASH, detail: 'collecting' })
    expect(metrics[3]).toMatchObject({ value: DASH, detail: 'collecting' })
  })
})

describe('chartMarkers', () => {
  it('marks the stuck query, the report and the outage inside the window', () => {
    const items = demo.inbox()
    const markers = chartMarkers({
      health: caFixtures('ca-unreachable').health('database', '1h'),
      items,
      reports: demo.reportHistory('demo', 10).reports,
      domain: [CA_NOW - 3600, CA_NOW],
    })
    expect(markers.map((marker) => marker.label)).toEqual([
      "Can't reach 9:41",
      'Stuck query 9:55',
    ])
    const day = chartMarkers({
      health: undefined,
      items: [],
      reports: demo.reportHistory('demo', 10).reports,
      domain: [CA_NOW - 86400, CA_NOW],
    })
    expect(day.some((marker) => marker.label === 'Report 12:00 AM')).toBe(true)
  })
})

describe('query rows', () => {
  it('phrases the 24 h delta', () => {
    expect(deltaLabel(0.12)).toEqual({ text: '+12% vs yesterday', up: true })
    expect(deltaLabel(-0.08)).toEqual({ text: '-8% vs yesterday', up: false })
    expect(deltaLabel(null)).toEqual({ text: 'no day before yet', up: false })
  })

  it('tints only poor plan scores', () => {
    expect(scorePoor(38)).toBe(true)
    expect(scorePoor(42)).toBe(false)
    expect(scorePoor(null)).toBe(false)
  })

  it('orders by share, or by latency with the Latency series', () => {
    expect(sortRows(rows, 'share')[0].key).toBe(
      CA_KEYS.postsUsersByCreationdate
    )
    const byLatency = sortRows(rows, 'latency')
    expect(byLatency[0].mean_ms).toBeGreaterThanOrEqual(byLatency[1].mean_ms)
  })

  it('filters by status, table and text', () => {
    expect(
      filterRows(rows, { status: 'fix', table: null, q: '' })
    ).toHaveLength(fixCount(rows))
    expect(
      filterRows(rows, { status: 'all', table: 'votes', q: '' }).every((row) =>
        row.tables.includes('votes')
      )
    ).toBe(true)
    expect(
      filterRows(rows, { status: 'all', table: null, q: 'badges' })
    ).toHaveLength(1)
  })

  it('captions the section for each state', () => {
    expect(
      sectionMeta(rows, { sort: 'share', firstRun: false, scanAt: null })
    ).toBe('16, 9 with a fix')
    expect(
      sectionMeta(rows, { sort: 'latency', firstRun: false, scanAt: null })
    ).toBe('16, sorted by latency')
    expect(
      sectionMeta(rows.slice(0, 3), {
        sort: 'share',
        firstRun: true,
        scanAt: null,
      })
    ).toBe('3 seen so far')
    expect(
      sectionMeta(rows.slice(0, 3), {
        sort: 'share',
        firstRun: false,
        scanAt: at(0, 0, 0, -2),
      })
    ).toBe('3, from a scan on Oct 6')
  })

  it('pages ten at a time and keeps each query its color', () => {
    expect(pagerLabel(0, 16)).toBe('1 to 10 of 16')
    expect(pagerLabel(1, 16)).toBe('11 to 16 of 16')
    expect(swatchColor(0)).toBe('var(--mk-band-1)')
    expect(swatchColor(null)).toBe('var(--mk-band-other)')
  })

  it('writes clocks the way the page does', () => {
    expect(markerClock(at(9, 55), 3600)).toBe('9:55')
    expect(markerClock(at(0, 0), 86400)).toBe('12:00 AM')
    expect(readingClock(at(9, 56, 30))).toBe('9:56:30')
  })
})

describe('rowActivity', () => {
  const job = (id: number, state: string, queue_state: string | null = null) =>
    ({ id, query_key: 'k', state, queue_state }) as never
  const row = (state: string | null) =>
    ({
      status: state ? 'analyzing' : 'healthy',
      last_analysis: state ? { state } : null,
    }) as unknown as MonitorQuery

  it('reads the newest job for each query', () => {
    expect(
      newestJobs([job(1, 'done'), job(3, 'running'), job(2, 'queued')]).get('k')
    ).toMatchObject({ id: 3 })
  })

  it('lets the polled job decide over the row, and falls back to the row', () => {
    expect(rowActivity(row(null), job(1, 'running'))).toBe('running')
    expect(rowActivity(row(null), job(1, 'queued'))).toBe('queued')
    expect(rowActivity(row(null), job(1, 'queued', 'held'))).toBeNull()
    expect(rowActivity(row('running'), job(1, 'done'))).toBeNull()
    expect(rowActivity(row('queued'), undefined)).toBe('queued')
    expect(rowActivity(row('running'), undefined)).toBe('running')
    expect(rowActivity(row(null), undefined)).toBeNull()
  })
})
