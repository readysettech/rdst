import { describe, expect, it } from 'vitest'
import type { AnalyzeJob } from '@/lib/monitor/types'
import {
  alreadyQueuedNote,
  classificationChip,
  findingLabel,
  inlineActivity,
  isWrite,
  jobActivity,
  PHASE_LABELS,
  rowActivity,
  scoreSlot,
  waitingJobs,
} from './rowActivity'

const ok = { ok: true, code: 'ok', reason: null } as const
const write = {
  ok: false,
  code: 'write',
  reason: 'UPDATE changes data',
} as const
const fresh = { last_analysis: null }

const job = (extra: Partial<AnalyzeJob>) =>
  ({ state: 'queued', ...extra }) as AnalyzeJob

const assessment = (status: string, findings: { id: string }[] = []) =>
  ({ status, findings }) as never

describe('rowActivity', () => {
  it('names the place in line, or that it runs now', () => {
    expect(
      jobActivity(job({ queue_state: 'today', queue_position: 2 }))
    ).toEqual({
      kind: 'queued',
      label: 'Queued',
      position: 2,
      soon: true,
    })
    expect(jobActivity(job({ state: 'running' }), 'advise')).toEqual({
      kind: 'running',
      label: 'Analyzing now',
      step: 'Writing recommendations',
      soon: true,
    })
    // A held job isn't soon: a scored row doesn't call it out, and it says why.
    const held = jobActivity(
      job({
        queue_state: 'held',
        reason_text: 'Sign in to turn on AI monitoring',
      })
    )
    expect(held?.soon).toBeUndefined()
    expect(held?.label).toBe('Sign in to turn on AI monitoring')
    expect(jobActivity(null)).toBeNull()
  })

  it('classifies, then leaves the row to Analyze now', () => {
    expect(
      rowActivity({
        query: fresh,
        gate: ok,
        classification: assessment('running'),
      })
    ).toEqual({ kind: 'classifying', label: 'Classifying...' })
    // Before the classifier reaches it, only when the classifier is at work.
    expect(
      rowActivity({ query: fresh, gate: ok, classifying: true })?.kind
    ).toBe('classifying')
    expect(rowActivity({ query: fresh, gate: ok })).toBeNull()
    expect(
      rowActivity({
        query: fresh,
        gate: ok,
        classification: assessment('complete'),
      })
    ).toBeNull()
    // Stuck on sign-in isn't "on its way".
    expect(
      rowActivity({
        query: fresh,
        gate: ok,
        classification: assessment('waiting_auth'),
      })
    ).toBeNull()
  })

  it('puts a job and a click note ahead of classifying, and skips writes', () => {
    const running = job({ state: 'running' })
    expect(
      rowActivity({
        query: fresh,
        gate: ok,
        job: running,
        classification: assessment('pending'),
      })?.label
    ).toBe('Analyzing now')
    expect(
      rowActivity({
        query: fresh,
        gate: ok,
        job: running,
        note: 'Already analyzing it',
      })
    ).toEqual({ kind: 'note', label: 'Already analyzing it' })
    expect(rowActivity({ query: fresh, gate: write, job: running })).toBeNull()
    expect(
      rowActivity({
        query: { last_analysis: { state: 'done' } as never },
        gate: ok,
        job: running,
      })?.label
    ).toBe('Analyzing now')
    expect(
      rowActivity({
        query: { last_analysis: { state: 'done' } as never },
        gate: ok,
      })
    ).toBeNull()
  })

  it('shows a re-analysis on a scored row, and a pending retry softly', () => {
    expect(
      rowActivity({
        query: { last_analysis: { state: 'queued' } as never },
        gate: ok,
      })?.label
    ).toBe('Queued')
    const retrying = {
      state: 'failed',
      error_kind: 'retrying',
      retry_at: Date.now() / 1000 + 600,
    }
    expect(
      rowActivity({
        query: { last_analysis: retrying as never },
        gate: ok,
        job: null,
      })?.label
    ).toMatch(/^Will retry at /)
    expect(
      rowActivity({
        query: { last_analysis: { ...retrying, retry_at: 1 } as never },
        gate: ok,
        job: null,
      })?.label
    ).toBe('Retrying')
  })

  it('says a clicked query is already queued or running', () => {
    expect(alreadyQueuedNote(job({ queue_position: 3 }))).toBe(
      'Already in the queue'
    )
    expect(alreadyQueuedNote(job({ state: 'running' }))).toBe(
      'Already analyzing it'
    )
  })

  it('reads writes and findings', () => {
    expect(isWrite(write)).toBe(true)
    expect(isWrite({ ok: false, code: 'ddl', reason: null })).toBe(true)
    expect(isWrite(ok)).toBe(false)
    expect(
      findingLabel(assessment('complete', [{ id: 'index_coverage' }]))
    ).toBe('Missing index')
    expect(findingLabel(assessment('complete', [{ id: 'none' }]))).toBeNull()
    expect(
      findingLabel(assessment('running', [{ id: 'index_coverage' }]))
    ).toBeNull()
  })
})

it('shows an immediate retry while the job retains its running claim', () => {
  expect(
    rowActivity({
      query: {
        last_analysis: { state: 'running', error_kind: 'retrying' } as never,
      },
      gate: ok,
      job: job({ state: 'running' }),
    })
  ).toEqual({ kind: 'retrying', label: 'Retrying' })
})

describe('the score column while RDST works on a query', () => {
  it('reads queued status and the running step from the row itself', () => {
    const queued = rowActivity({
      query: {
        last_analysis: {
          state: 'queued',
          queue_state: 'today',
          queue_position: 3,
        } as never,
      },
      gate: ok,
    })
    expect(scoreSlot(queued)).toEqual({
      kind: 'queued',
      word: 'Queued',
      detail: null,
      title: 'Queued for deep analysis',
    })
    const running = rowActivity({
      query: { last_analysis: { state: 'running', phase: 'sample' } as never },
      gate: ok,
      job: job({ state: 'running' }),
    })
    expect(scoreSlot(running)?.word).toBe('Analyzing')
    expect(scoreSlot(running)?.title).toBe('Analyzing now: sampling values')
    expect(inlineActivity(running, false)?.label).toBe('Sampling values')
    expect(inlineActivity(running, true)?.label).toBe(
      'Analyzing now · Sampling values'
    )
    expect(inlineActivity(queued, false)).toBeNull()
  })

  it('fills the place from the row when the queue list lacks it', () => {
    expect(
      rowActivity({
        query: {
          last_analysis: {
            state: 'queued',
            queue_state: 'today',
            queue_position: 2,
          } as never,
        },
        gate: ok,
        job: job({}),
      })?.label
    ).toBe('Queued')
  })

  it('says Retrying, softly, and keeps a held job on the placeholder', () => {
    const retry = rowActivity({
      query: {
        last_analysis: { state: 'running', error_kind: 'retrying' } as never,
      },
      gate: ok,
    })
    expect(scoreSlot(retry)?.word).toBe('Retrying')
    expect(inlineActivity(retry, false)).toBeNull()
    const held = jobActivity(job({ queue_state: 'held' }))
    expect(scoreSlot(held)).toBeNull()
    expect(inlineActivity(held, false)).toBe(held)
    expect(PHASE_LABELS.run).toBe('Running EXPLAIN ANALYZE')
  })
})

describe('classificationChip', () => {
  it('names a finding and shows nothing without one', () => {
    expect(
      classificationChip(assessment('complete', [{ id: 'index_coverage' }]))
    ).toEqual({ label: 'Missing index', clear: false })
    // A schema-only classification can't clear a query.
    expect(classificationChip(assessment('complete'))).toBeNull()
    expect(
      classificationChip({
        status: 'complete',
        band: 'Limited',
        findings: [],
      } as never)
    ).toBeNull()
    expect(classificationChip(assessment('running'))).toBeNull()
  })
})

it('shows the dispatch position in the score tile when the job row omits it', () => {
  const first = job({ id: 1, query_key: 'first' })
  const second = job({ id: 2, query_key: 'second' })
  const page = { jobs: [second, first], queue: { next: [first, second] } }
  const activity = jobActivity(waitingJobs(page).get('second'))
  expect(activity?.label).toBe('Queued')
  expect(scoreSlot(activity)?.detail).toBeNull()
})
