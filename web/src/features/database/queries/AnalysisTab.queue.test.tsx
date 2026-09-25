import { cleanup, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { resetStubState, setFixtureTiming } from '@/lib/monitor/api'
import { setFixtureOverride } from '@/lib/monitor/contract'
import type {
  AnalyzeJob,
  AnalyzeJobPage,
  MonitorQuery,
} from '@/lib/monitor/types'
import { renderWithClient } from '@/test-utils'

const pages = vi.hoisted(() => ({
  own: { jobs: [] } as AnalyzeJobPage,
  all: { jobs: [] } as AnalyzeJobPage,
}))

vi.mock('@/lib/monitor/hooks', async (original) => ({
  ...(await original<typeof import('@/lib/monitor/hooks')>()),
  useQueryAnalyzeJobs: () => ({ data: pages.own }),
  useAnalyzeJobs: () => ({ data: pages.all }),
}))

const { AnalysisTab } = await import('./AnalysisTab')

const NOW = Date.now() / 1000

const job = (extra: Partial<AnalyzeJob> = {}) =>
  ({
    id: 7,
    target: 'localdev',
    query_key: 'abc',
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

const query = {
  key: 'abc',
  text: 'SELECT 1',
  text_status: 'ok',
  last_analysis: null,
} as unknown as MonitorQuery

const queue = {
  cap: 27,
  used: 22,
  left: 5,
  reserved: 5,
  automatic_left: 0,
  reset_at: NOW + 3600,
  average_run_s: 120,
  automatic_enabled: true,
  running: null,
  next: [],
  recent: [],
  waiting_today: 0,
  held: 0,
}

beforeEach(() => {
  setFixtureTiming(0)
  resetStubState()
  setFixtureOverride('localdev')
})

afterEach(() => {
  cleanup()
  setFixtureOverride(undefined)
})

describe('AnalysisTab queue wording', () => {
  it('says why a held job waits and offers Analyze now', async () => {
    const waiting = job({
      queue_state: 'held',
      reason_text: 'Sign in to turn on AI monitoring',
    })
    pages.own = { jobs: [waiting] }
    pages.all = { jobs: [waiting], queue }
    renderWithClient(
      <AnalysisTab query={query} target="localdev" onCompare={() => {}} />
    )
    expect(
      await screen.findByText('Sign in to turn on AI monitoring')
    ).toBeTruthy()
    expect(screen.getByText('Analysis on hold')).toBeTruthy()
    expect(screen.queryByText(/after midnight/)).toBeNull()
    expect(
      screen.getByRole('link', { name: 'Settings' }).getAttribute('href')
    ).toBe('/database?target=localdev&panel=settings#monitor-cap')
    expect(screen.getByRole('button', { name: /Analyze now/ })).toBeTruthy()
    expect(screen.queryByText(/so far/)).toBeNull()
    expect(screen.queryByText('4:00:00')).toBeNull()
  })

  it('shows queued status and an estimate', async () => {
    const waiting = job({
      queue_state: 'today',
      queue_position: 3,
      estimated_start_at: NOW + 360,
    })
    pages.own = { jobs: [waiting] }
    pages.all = { jobs: [waiting], queue }
    renderWithClient(
      <AnalysisTab query={query} target="localdev" onCompare={() => {}} />
    )
    expect(await screen.findByText('Queued, about 6 min')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Analyze now/ })).toBeNull()
  })

  it('falls back to the count ahead without queue facts', async () => {
    const mine = job()
    pages.own = { jobs: [mine] }
    pages.all = {
      jobs: [
        mine,
        job({ id: 3, query_key: 'other', enqueued_at: NOW - 5 * 3600 }),
      ],
    }
    renderWithClient(
      <AnalysisTab query={query} target="localdev" onCompare={() => {}} />
    )
    expect(await screen.findByText('1 analysis ahead of this one')).toBeTruthy()
    expect(screen.queryByText(/Waiting/)).toBeNull()
  })
})
