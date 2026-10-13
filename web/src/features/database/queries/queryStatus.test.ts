import { describe, expect, it } from 'vitest'
import type { AnalyzeJob, InboxItem, MonitorQuery } from '@/lib/monitor/types'
import type { Analyzability } from './analyzability'
import {
  analysisLine,
  attentionCount,
  changeWords,
  fixReason,
  inboxSignals,
  jobLine,
  queryStatus,
} from './queryStatus'

// 9:42 PM local on a fixed day, so the clock text is stable in any time zone.
const AT = new Date(2026, 8, 28, 21, 42).getTime() / 1000
const NOW = AT + 1800
const OK: Analyzability = { ok: true, code: 'ok', reason: null }

function item(overrides: Partial<InboxItem>): InboxItem {
  return {
    id: 1,
    target: 'demoshop',
    type: 'traffic_spike',
    subject: 'aaa',
    state: 'new',
    severity: 0.7,
    title: 'Query traffic increased',
    body_json: '{}',
    created_at: AT,
    updated_at: AT,
    resolved_at: null,
    resolved_by: null,
    seen_count: 0,
    reopened_count: 0,
    severity_band: 'watch',
    tone: 'warning',
    summary: '',
    query_keys: ['aaa'],
    subject_label: '',
    group_key: '',
    muted_by: null,
    trend: [],
    delivery: 'inbox',
    measurement: {
      metric: 'calls',
      direction: 'up',
      before: 1,
      after: 3,
      change: 2,
      unit: 'calls_per_second',
      text: 'Calls up 3x',
    },
    ...overrides,
  }
}

function query(overrides: Partial<MonitorQuery> = {}): MonitorQuery {
  return {
    key: 'aaa',
    text: 'SELECT 1',
    text_status: 'ok',
    state: [],
    last_analysis: null,
    ...overrides,
  } as unknown as MonitorQuery
}

const analyzed = (fix_ready: boolean, headline = 'Already efficient') =>
  query({
    last_analysis: {
      state: 'done',
      finished_at: AT,
      analysis_ref: 'r',
      mode: 'analyze',
      param_source: null,
      reason: null,
      summary: {
        analysis_ref: 'r',
        analysis_id: '1',
        analyzed_at: AT,
        mode: 'analyze',
        mode_label: null,
        headline,
        rating: 'poor',
        efficiency_score: 40,
        fix_kind: fix_ready ? 'index' : null,
        fix_ready,
      },
    } as MonitorQuery['last_analysis'],
  })

describe('changeWords', () => {
  it('reads as a percent under 2x and a multiple above', () => {
    expect(changeWords(0.34)).toBe('34%')
    expect(changeWords(2)).toBe('3x')
    expect(changeWords(2.2)).toBe('3.2x')
    expect(changeWords(-0.37)).toBe('37%')
    expect(changeWords(22.8)).toBe('24x')
  })
})

describe('inboxSignals', () => {
  it('turns an open item into a reason with numbers and a time', () => {
    const signals = inboxSignals([item({})], 'demoshop', NOW)
    expect(signals.get('aaa')).toMatchObject({
      level: 'watch',
      reason: 'Traffic up 3x since 9:42 PM',
      itemId: 1,
    })
  })

  it('maps Act now to attention and the digest to watch', () => {
    const signals = inboxSignals(
      [
        item({ id: 1, severity_band: 'act_now' }),
        item({
          id: 2,
          subject: 'bbb',
          query_keys: ['bbb'],
          severity_band: 'fyi',
          delivery: 'digest',
          tone: 'neutral',
        }),
      ],
      undefined,
      NOW
    )
    expect(signals.get('aaa')?.level).toBe('attention')
    expect(signals.get('bbb')?.level).toBe('watch')
  })

  it('skips closed, muted, snoozed, recovered, FYI and good-news items', () => {
    const signals = inboxSignals(
      [
        item({ state: 'resolved' }),
        item({ state: 'dismissed' }),
        item({ state: 'snoozed' }),
        item({ muted_by: 3 }),
        item({ severity_band: 'recovered', tone: 'positive' }),
        item({ severity_band: 'fyi', delivery: 'inbox', tone: 'neutral' }),
        item({
          type: 'query_improved',
          severity_band: 'fyi',
          delivery: 'digest',
          tone: 'neutral',
        }),
      ],
      undefined,
      NOW
    )
    expect(signals.size).toBe(0)
  })

  it('keeps the strongest item per query', () => {
    const signals = inboxSignals(
      [
        item({ id: 1, severity_band: 'watch' }),
        item({ id: 2, severity_band: 'act_now', created_at: AT - 600 }),
      ],
      undefined,
      NOW
    )
    expect(signals.get('aaa')?.itemId).toBe(2)
  })

  it('marks only the named members of a group alert, each with its own change', () => {
    const keys = Array.from({ length: 66 }, (_, index) => `k${index}`)
    const group = item({
      type: 'many_queries_changed',
      subject: 'database',
      severity_band: 'act_now',
      query_keys: keys,
      measurement: {
        metric: 'calls',
        direction: 'up',
        before: null,
        after: null,
        change: 0.34,
        unit: 'calls_per_second',
        text: 'Median calls up 34% across 66 queries',
      },
      members: [
        {
          query_key: 'k0',
          display_name: 'a',
          before: 1,
          after: 4,
          change: 3,
          impact_ms: 3,
        },
        {
          query_key: 'k1',
          display_name: 'b',
          before: 1,
          after: 0.8,
          change: -0.23,
          impact_ms: 2,
        },
        {
          query_key: 'k2',
          display_name: 'c',
          before: 1,
          after: 1,
          change: 0.003,
          impact_ms: 1,
        },
      ],
    })
    const signals = inboxSignals([group], undefined, NOW)
    expect([...signals.keys()]).toEqual(['k0'])
    expect(signals.get('k0')?.reason).toBe('Traffic up 4x since 9:42 PM')
  })

  it('names new and stopped queries in plain words', () => {
    const signals = inboxSignals(
      [
        item({
          id: 1,
          type: 'new_heavy_query',
          measurement: {
            metric: 'share',
            direction: 'up',
            before: null,
            after: 0.48,
            change: null,
            unit: 'ratio',
            text: 'New: 48% of database time',
          },
        }),
        item({
          id: 2,
          subject: 'bbb',
          query_keys: ['bbb'],
          type: 'query_stopped_running',
          measurement: {
            metric: 'calls',
            direction: 'down',
            before: 0.1,
            after: 0,
            change: -1,
            unit: 'calls_per_second',
            text: 'Calls down to 0',
          },
        }),
      ],
      undefined,
      NOW
    )
    expect(signals.get('aaa')?.reason).toBe(
      'New since 9:42 PM, 48% of database time'
    )
    expect(signals.get('bbb')?.reason).toBe('Stopped running at 9:42 PM')
  })

  it('leaves other targets out', () => {
    expect(inboxSignals([item({})], 'localdev', NOW).size).toBe(0)
  })
})

describe('queryStatus', () => {
  const signal = {
    level: 'attention' as const,
    reason: 'Traffic up 3x since 9:42 PM',
    itemId: 7,
    createdAt: AT,
  }

  it('takes the first that applies: Inbox, fix, analyzed, not analyzed', () => {
    expect(queryStatus(analyzed(true), { signal, gate: OK })).toMatchObject({
      id: 'attention',
      label: 'Needs attention',
      tone: 'negative',
      reason: 'Traffic up 3x since 9:42 PM',
      itemId: 7,
    })
    expect(
      queryStatus(analyzed(true), {
        signal: { ...signal, level: 'watch' },
        gate: OK,
      })
    ).toMatchObject({ id: 'watch', tone: 'warning' })
    expect(
      queryStatus(
        analyzed(true, 'Needs an index on events(event_type, created_at)'),
        { gate: OK }
      )
    ).toMatchObject({
      id: 'fix_ready',
      label: 'Fix suggested',
      reason: 'Add an index on events(event_type, created_at)',
    })
    expect(queryStatus(analyzed(false), { gate: OK })).toMatchObject({
      id: 'healthy',
      reason: null,
    })
    expect(queryStatus(query(), { gate: OK })).toMatchObject({
      id: 'not_analyzed',
      label: 'Not analyzed yet',
      reason: null,
    })
  })

  it('says why a query has no analysis yet', () => {
    const writes: Analyzability = {
      ok: false,
      code: 'write',
      reason: "UPDATE changes data, so RDST doesn't run it.",
    }
    expect(queryStatus(query(), { gate: writes }).reason).toBe(
      "UPDATE changes data, so RDST doesn't run it."
    )
    const job = {
      state: 'queued',
      queue_state: 'today',
      estimated_start_at: NOW + 360,
    } as AnalyzeJob
    expect(queryStatus(query(), { gate: OK, job, now: NOW }).reason).toBe(
      'Queued, about 6 min'
    )
    // A statement RDST never runs reads "Not analyzed", even with a stale job.
    expect(queryStatus(query(), { gate: writes, job, now: NOW })).toMatchObject(
      {
        id: 'not_analyzed',
        label: 'Not analyzed',
        reason: "UPDATE changes data, so RDST doesn't run it.",
      }
    )
  })
})

describe('fixReason and jobLine', () => {
  it('reads the fix from the analysis', () => {
    expect(fixReason(analyzed(true, 'A rewrite could make it faster'))).toBe(
      'Add an index'
    )
    expect(fixReason(analyzed(true, 'Needs an index on orders(total)'))).toBe(
      'Add an index on orders(total)'
    )
  })

  it('says where a waiting job stands', () => {
    expect(jobLine({ state: 'running' } as AnalyzeJob, NOW)).toBe(
      'Analyzing now'
    )
    expect(
      jobLine({ state: 'queued', queue_state: 'held' } as AnalyzeJob, NOW)
    ).toBe('Waiting, automatic analysis is off')
    expect(jobLine(null, NOW)).toBeNull()
  })
})

it('counts queries with an open Inbox item', () => {
  const signals = inboxSignals([item({})], undefined, NOW)
  expect(attentionCount([query(), query({ key: 'bbb' })], signals)).toBe(1)
})

describe('analysisLine', () => {
  const last = (
    fields: Partial<NonNullable<MonitorQuery['last_analysis']>>
  ): MonitorQuery =>
    query({
      last_analysis: {
        state: 'queued',
        finished_at: null,
        analysis_ref: null,
        mode: '',
        param_source: null,
        reason: null,
        ...fields,
      },
    })

  it('says why an analysis is on hold and offers Analyze now', () => {
    const job = {
      state: 'queued',
      reason: 'new',
      queue_state: 'held',
      reason_text: 'Sign in to turn on AI monitoring',
    } as AnalyzeJob
    expect(analysisLine(last({ queue_state: 'held' }), job, NOW)).toEqual({
      text: 'On hold: Sign in to turn on AI monitoring.',
      tone: 'neutral',
      action: 'analyze_now',
    })
    expect(
      analysisLine(
        last({ queue_state: 'held' }),
        { ...job, reason: 'user' },
        NOW
      )?.action
    ).toBeNull()
  })

  it('says when a queued analysis starts', () => {
    const job = {
      state: 'queued',
      reason: 'new',
      queue_state: 'today',
      estimated_start_at: NOW + 360,
    } as AnalyzeJob
    expect(analysisLine(last({}), job, NOW)?.text).toBe('Queued, about 6 min')
  })

  it('gives the failure reason with Retry, and a retry on its way calmly', () => {
    expect(
      analysisLine(last({ state: 'failed', reason: 'Plan failed' }), null, NOW)
    ).toEqual({
      text: "The last analysis didn't finish. Plan failed.",
      tone: 'negative',
      action: 'retry',
    })
    expect(
      analysisLine(
        last({ state: 'failed', error_kind: 'retrying', retry_at: null }),
        null,
        NOW
      )
    ).toEqual({ text: 'Retrying soon.', tone: 'neutral', action: null })
    expect(analysisLine(last({ state: 'done' }), null, NOW)).toBeNull()
  })

  it('reads a held job by its reason', () => {
    expect(
      jobLine(
        {
          state: 'queued',
          queue_state: 'held',
          reason_text: 'Monitoring is off',
        } as AnalyzeJob,
        NOW
      )
    ).toBe('Monitoring is off')
  })
})
