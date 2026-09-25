import { describe, expect, it } from 'vitest'
import type { AnalyzeJob } from '@/lib/monitor/types'
import { aboutMinutes, canAnalyzeNow, queueLine } from './analysisQueue'

const NOW = 1_800_000_000

const job = (extra: Partial<AnalyzeJob> = {}) =>
  ({
    id: 1,
    target: 'db',
    query_key: 'k',
    reason: 'new',
    state: 'queued',
    priority: 1,
    param_source: null,
    mode: null,
    enqueued_at: NOW - 4 * 3600,
    started_at: null,
    finished_at: null,
    error: null,
    analysis_ref: null,
    reason_text: null,
    ...extra,
  }) as AnalyzeJob

describe('queueLine', () => {
  it("shows queued status and estimates within today's budget", () => {
    expect(
      queueLine(
        job({
          queue_state: 'today',
          queue_position: 1,
          estimated_start_at: NOW + 110,
        }),
        NOW
      )
    ).toBe('Queued, about 2 min')
    expect(
      queueLine(
        job({
          queue_state: 'today',
          queue_position: 3,
          estimated_start_at: NOW + 360,
        }),
        NOW
      )
    ).toBe('Queued, about 6 min')
    expect(
      queueLine(job({ queue_state: 'today', queue_position: 12 }), NOW)
    ).toBe('Queued')
  })

  it('shows a held job its reason', () => {
    expect(
      queueLine(
        job({
          queue_state: 'held',
          reason_text: 'Sign in to turn on AI monitoring',
        }),
        NOW
      )
    ).toBe('Sign in to turn on AI monitoring')
  })

  it('falls back to the count ahead for an older backend', () => {
    expect(queueLine(job(), NOW, null, 42)).toBe(
      '42 analyses ahead of this one'
    )
    expect(queueLine(job(), NOW, null, 0)).toBe('Next in line')
    expect(queueLine(job({ state: 'running' }), NOW)).toBe('Analyzing now')
  })

  it('rounds estimates to at least a minute', () => {
    expect(aboutMinutes(NOW - 100, NOW)).toBe('about 1 min')
    expect(aboutMinutes(NOW + 3 * 3600, NOW)).toBe('about 3 h')
  })
})

describe('canAnalyzeNow', () => {
  it('offers Analyze now for a held automatic job, whatever was used today', () => {
    const held = job({ queue_state: 'held' })
    expect(canAnalyzeNow(held)).toBe(true)
    expect(canAnalyzeNow(job({ queue_state: 'today' }))).toBe(false)
    expect(canAnalyzeNow({ ...held, reason: 'user' })).toBe(false)
  })
})
