import { describe, expect, it } from 'vitest'
import type {
  AnalyzeQueueEntry,
  AnalyzeQueueSummary,
  MonitorPulse,
} from '@/lib/monitor/types'
import {
  firstRun,
  firstRunDomain,
  isFirstRun,
  showFirstRun,
} from './firstReadingsModel'

const NOW = 1_790_000_000
const ON = NOW - 90

function history(firstTs: number | null, lastTs: number | null = firstTs) {
  return {
    first_ts: firstTs,
    last_ts: lastTs,
    span_seconds: firstTs != null && lastTs != null ? lastTs - firstTs : 0,
    resolutions: {
      '60': { first_ts: firstTs },
      '300': { first_ts: null },
      '3600': { first_ts: null },
      '86400': { first_ts: null },
    },
  }
}

function collect(
  ts: number,
  detail = 'Read 4 queries',
  ok = true
): MonitorPulse {
  return {
    ts,
    kind: 'collect',
    ok,
    detail,
    next_collect_at: ts + 60,
    state: 'live',
  }
}

const settings = {
  enabled: true,
  attention_every_min: 5 as const,
  nightly_report: true,
  nightly_hour: 0,
  deep_analysis: true,
  enabled_at: ON,
}

function job(
  id: number,
  state: AnalyzeQueueEntry['state'],
  extra: Partial<AnalyzeQueueEntry> = {}
): AnalyzeQueueEntry {
  return {
    id,
    target: 'db',
    query_key: `q${id}`,
    reason: 'new',
    state,
    priority: 1,
    param_source: null,
    mode: null,
    enqueued_at: ON + 12,
    started_at: null,
    finished_at: null,
    error: null,
    analysis_ref: null,
    reason_text: null,
    display_name: `Query ${id}`,
    ...extra,
  }
}

function queue(parts: Partial<AnalyzeQueueSummary> = {}): AnalyzeQueueSummary {
  return {
    cap: 10,
    used: 0,
    left: 10,
    reserved: 2,
    automatic_left: 8,
    reset_at: NOW + 3600,
    average_run_s: 120,
    automatic_enabled: true,
    running: null,
    next: [],
    recent: [],
    waiting_today: 0,
    held: 0,
    first_hour: {
      started_at: ON,
      ends_at: ON + 3600,
      top: 10,
      analyzed: 3,
      max: 20,
      next_check_at: ON + 120,
      first_reading_at: ON + 2,
      first_queries_at: ON + 2,
      readings: 9,
    },
    ...parts,
  }
}

const row = (key: string, status: string | null, analyzable = true) => ({
  key,
  analyzable,
  assessment: status
    ? ({
        status,
        band: null,
        priority_score: null,
        confidence: null,
        findings: [],
        model: null,
        rubric_version: null,
        assessed_at: status === 'complete' ? '2026-09-21T12:00:00Z' : null,
        error_code: null,
        waiting_reason: null,
      } as never)
    : null,
})

describe('isFirstRun and showFirstRun', () => {
  it('holds from turning monitoring on until five minutes of history', () => {
    expect(isFirstRun(true, history(null))).toBe(true)
    expect(isFirstRun(true, history(NOW - 120, NOW))).toBe(true)
    expect(isFirstRun(true, history(NOW - 300, NOW))).toBe(false)
    expect(isFirstRun(false, history(null))).toBe(false)
  })

  it('shows the steps for the first hour after the turn-on', () => {
    expect(showFirstRun(settings, history(ON + 30, NOW), NOW)).toBe(true)
    expect(
      showFirstRun({ ...settings, enabled_at: NOW - 3600 }, history(1), NOW)
    ).toBe(false)
    // Without a turn-on time on record, until the chart has five minutes.
    const legacy = { ...settings, enabled_at: null }
    expect(showFirstRun(legacy, history(NOW - 60, NOW), NOW)).toBe(true)
    expect(showFirstRun(legacy, history(NOW - 600, NOW), NOW)).toBe(false)
    expect(showFirstRun({ ...settings, enabled: false }, null, NOW)).toBe(false)
  })

  it('uses the served first-hour rule even when chart history is missing or recent', () => {
    const again = {
      ...settings,
      enabled_at: NOW - 120,
      first_hour_started_at: null,
    }
    for (const span of [
      undefined,
      null,
      history(null),
      history(NOW - 110, NOW),
    ]) {
      expect(showFirstRun(again, span, NOW)).toBe(false)
    }
    const first = { ...settings, first_hour_started_at: ON }
    expect(showFirstRun(first, undefined, NOW)).toBe(true)
    expect(showFirstRun(first, history(null), ON + 3600)).toBe(false)
    expect(showFirstRun({ ...first, enabled: false }, null, NOW)).toBe(false)
  })

  it('skips the steps when monitoring is turned back on for a database with history', () => {
    const again = { ...settings, enabled_at: NOW - 120 }
    expect(showFirstRun(again, history(NOW - 86400 * 3, NOW), NOW)).toBe(false)
    expect(showFirstRun(again, history(NOW - 110, NOW), NOW)).toBe(true)
  })
})

describe('firstRunDomain', () => {
  it('spans the first reading and the next minutes, then steps aside', () => {
    expect(firstRunDomain(history(NOW - 60, NOW), NOW)).toEqual([
      NOW - 60,
      NOW + 240,
    ])
    expect(firstRunDomain(history(NOW - 280, NOW), NOW)).toEqual([
      NOW - 280,
      NOW + 60,
    ])
    expect(firstRunDomain(history(NOW - 300, NOW), NOW)).toBeNull()
    expect(firstRunDomain(history(null), NOW)).toBeNull()
  })
})

describe('firstRun', () => {
  const byId = (model: ReturnType<typeof firstRun>) =>
    Object.fromEntries(model.steps.map((step) => [step.id, step]))

  it('waits for the first reading with nothing invented', () => {
    const model = firstRun({
      now: NOW,
      settings,
      history: history(null),
      pulses: [],
      nextReadingAt: NOW + 8,
      queries: null,
      queue: null,
    })
    expect(model.readings).toBe(0)
    expect(model.nextAt).toBe(NOW + 8)
    expect(model.waitingForQueries).toBe(false)
    expect(model.complete).toBe(false)
    expect(model.steps.map((step) => [step.id, step.state])).toEqual([
      ['queries', 'active'],
      ['deep', 'pending'],
      ['report', 'pending'],
    ])
    expect(model.steps[0].meta).toBe('Reading the database')
  })

  it('shows real deep analysis progress and what comes next', () => {
    const model = firstRun({
      now: NOW,
      settings,
      history: history(ON - 50, NOW),
      pulses: [collect(NOW - 4), collect(NOW - 14), collect(ON - 3600)],
      nextReadingAt: NOW + 50,
      queries: [
        row('a', 'complete'),
        row('b', 'running'),
        row('w', null, false),
      ],
      queue: queue({
        running: job(2, 'running', { started_at: NOW - 30 }),
        next: [job(3, 'queued', { queue_position: 1 })],
        recent: [
          job(1, 'done', { finished_at: NOW - 31 }),
          job(9, 'done', { enqueued_at: ON - 600, finished_at: ON - 300 }),
        ],
      }),
    })
    expect(model.firstAt).toBe(ON + 2)
    expect(model.nextAt).toBe(NOW + 6)
    expect(byId(model).queries).toMatchObject({
      label: 'Found your queries',
      state: 'done',
      meta: null,
    })
    // Only analyses since the turn-on count: one done of three.
    expect(byId(model).deep).toMatchObject({
      label: 'Analyzing the most expensive queries',
      state: 'active',
      meta: '1 analyzed',
    })
    expect(byId(model).deep.progress).toBeCloseTo(1 / 3)
    expect(model.complete).toBe(false)
  })

  it('never shows a bare zero with a check, and completes once the top 10 are done', () => {
    const widened = (recent: AnalyzeQueueEntry[]) =>
      firstRun({
        now: NOW,
        settings,
        history: history(ON, NOW),
        pulses: [collect(NOW - 5)],
        queries: [row('a', 'complete')],
        queue: queue({
          recent,
          first_hour: { ...queue().first_hour!, top: 10, next_check_at: null },
        }),
      })
    const none = widened([job(1, 'skipped', { finished_at: NOW - 40 })])
    expect(byId(none).deep.state).not.toBe('done')
    expect(none.complete).toBe(false)
    const two = widened([
      job(1, 'done', { finished_at: NOW - 40 }),
      job(2, 'done', { finished_at: NOW - 20 }),
    ])
    expect(byId(two).deep).toMatchObject({
      state: 'done',
      meta: '2 analyzed',
    })
    expect(two.complete).toBe(true)
  })

  it('says when the database has not run a query yet', () => {
    const model = firstRun({
      now: NOW,
      settings,
      history: history(ON, NOW),
      pulses: [collect(NOW - 5, 'Read 0 queries')],
      queries: [],
      queue: queue({ first_hour: null }),
    })
    expect(model.waitingForQueries).toBe(true)
    expect(model.steps[0]).toMatchObject({
      state: 'active',
      meta: 'Waiting for the database to run some',
    })
  })

  it("doesn't call a list fetched before the first reading empty", () => {
    const model = firstRun({
      now: NOW,
      settings,
      history: history(ON, NOW),
      pulses: [collect(NOW - 5, 'Read 23 queries')],
      queries: [],
      queue: null,
    })
    expect(model.waitingForQueries).toBe(false)
  })

  it('marks steps that need sign-in, AI, or are turned off', () => {
    const base = {
      now: NOW,
      history: history(ON - 50, ON + 400),
      pulses: [collect(NOW - 5)],
      queries: [row('a', 'waiting_auth')],
    }
    const login = firstRun({
      ...base,
      settings: { ...settings, nightly_report: false },
      queue: queue({
        recent: [
          job(1, 'failed', {
            finished_at: NOW - 40,
            error: 'LOGIN_REQUIRED: Sign in to run Deep Analyze',
          }),
        ],
      }),
    })
    expect(byId(login).deep).toMatchObject({
      state: 'blocked',
      meta: '0 analyzed, 1 failed',
      sub: 'Needs sign-in',
    })
    // With the nightly report off, no first report is promised.
    expect(byId(login).report).toMatchObject({
      state: 'off',
      meta: 'Turned off',
    })
    const local = firstRun({
      ...base,
      settings: { ...settings, ai_monitoring: 'local' },
      queue: queue(),
    })
    expect(byId(local).deep).toMatchObject({ state: 'blocked' })
    const off = firstRun({
      ...base,
      settings: { ...settings, deep_analysis: false },
      queue: queue(),
    })
    expect(byId(off).deep.state).toBe('off')
    expect(off.complete).toBe(true)
  })

  it('reports a failed reading', () => {
    const model = firstRun({
      now: NOW,
      settings,
      history: history(null),
      pulses: [collect(NOW - 5, 'pg_stat_statements is not readable', false)],
      queries: null,
      queue: null,
    })
    expect(model.failure).toBe('pg_stat_statements is not readable')
    expect(model.steps[0].state).toBe('blocked')
  })
})

it.each([
  1, 2, 0,
])('keeps a mixed first run honest with %s completed analyses', (completed) => {
  const recent = Array.from({ length: 12 }, (_, index) =>
    job(index, index < completed ? 'done' : 'failed', { finished_at: NOW - 5 })
  )
  const model = firstRun({
    now: NOW,
    settings,
    history: history(ON, NOW),
    pulses: [collect(NOW - 5)],
    queries: [row('a', 'complete')],
    queue: queue({ recent }),
  })
  expect(model.complete).toBe(false)
  expect(model.steps.find((step) => step.id === 'deep')).toMatchObject({
    state: 'blocked',
    meta: `${completed} analyzed, ${12 - completed} failed`,
  })
})

it('uses full first-hour totals when the recent list omits a completed analysis', () => {
  const recent = Array.from({ length: 10 }, (_, index) => job(index, 'failed'))
  const model = firstRun({
    now: NOW,
    settings,
    history: history(ON, NOW),
    pulses: [collect(NOW - 5)],
    queries: [row('a', 'complete')],
    queue: queue({
      recent,
      first_hour: {
        ...queue().first_hour!,
        analyzed: 1,
        scheduled: 12,
        failed: 11,
      },
    }),
  })
  expect(model.complete).toBe(false)
  expect(model.steps.find((step) => step.id === 'deep')?.meta).toBe(
    '1 analyzed, 11 failed'
  )
})
