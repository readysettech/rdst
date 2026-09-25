import { describe, expect, it } from 'vitest'
import type { MonitorQuery } from '@/lib/monitor/types'
import { fixReady, jevRead, matchesClassification } from './jev'

const base = {
  key: 'c58ed20d30d1',
  text: 'SELECT 1',
  text_status: 'ok',
  state: [],
  change: { latency: null, qps: null, significant: false, verdict: null },
} as unknown as MonitorQuery

const analyzed = (fix_ready: boolean) =>
  ({
    ...base,
    last_analysis: {
      state: 'done',
      finished_at: 1,
      analysis_ref: 'r',
      mode: 'analyze',
      param_source: null,
      reason: null,
      summary: { fix_ready, headline: 'Already efficient' },
    },
  }) as unknown as MonitorQuery

describe('fix ready', () => {
  it('follows the analysis summary, not a client rule', () => {
    expect(fixReady(analyzed(true))).toBe(true)
    expect(fixReady(analyzed(false))).toBe(false)
  })
})

describe('jevRead', () => {
  it("reads the monitor row's complete assessment", () => {
    const read = jevRead({
      ...base,
      assessment: {
        status: 'complete',
        band: 'Low',
        priority_score: 29,
        confidence: 0.39,
        findings: [],
        model: 'jev',
        rubric_version: 'v2',
        assessed_at: '2026-09-26T04:36:19Z',
        error_code: null,
      },
    })
    expect(read.assessment).toMatchObject({
      status: 'complete',
      band: 'Low',
      priority_score: 29,
    })
    expect(read.waitingReason).toBeNull()
  })

  it('says what an incomplete assessment waits for', () => {
    const read = jevRead({
      ...base,
      assessment: {
        status: 'waiting_connection',
        band: null,
        priority_score: null,
        confidence: null,
        findings: [],
        model: null,
        rubric_version: null,
        assessed_at: null,
        error_code: null,
        waiting_reason: 'Waiting for a database connection.',
      },
    })
    expect(read.waitingReason).toBe('Waiting for a database connection.')
  })

  it('falls back to the Query Library row', () => {
    expect(
      jevRead(base, { status: 'complete', band: 'High' }).assessment?.band
    ).toBe('High')
  })
})

describe('classification filters', () => {
  it('matches findings, no finding and unassessed queries', () => {
    const flagged = {
      status: 'complete',
      band: 'High',
      priority_score: 80,
      findings: [{ id: 'index_coverage', label: 'Missing index' }],
    }
    expect(matchesClassification(flagged, 'index_coverage')).toBe(true)
    expect(matchesClassification(flagged, 'join_growth')).toBe(false)
    expect(matchesClassification(flagged, 'none')).toBe(false)
    expect(
      matchesClassification({ status: 'complete', findings: [] }, 'none')
    ).toBe(true)
    expect(matchesClassification({ status: 'pending' }, 'unassessed')).toBe(
      true
    )
    expect(matchesClassification(null, 'unassessed')).toBe(true)
    expect(matchesClassification(null, 'none')).toBe(false)
  })
})
