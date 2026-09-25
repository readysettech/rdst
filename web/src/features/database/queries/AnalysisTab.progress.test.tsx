import { cleanup, fireEvent, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import type { AnalyzeJob, MonitorQuery } from '@/lib/monitor/types'
import { renderWithClient } from '@/test-utils'

const state = vi.hoisted(() => ({
  own: [] as AnalyzeJob[],
  all: [] as AnalyzeJob[],
  submitted: {} as AnalyzeJob,
}))
vi.mock('@/lib/monitor/hooks', () => ({
  useQueryAnalyzeJobs: () => ({ data: { jobs: state.own } }),
  useAnalyzeJobs: () => ({ data: { jobs: state.all } }),
  useMonitorSettings: () => ({ data: { execute: false } }),
  useEnqueueAnalysis: () => ({
    mutate: (_key: string, options: { onSuccess: (job: AnalyzeJob) => void }) =>
      options.onSuccess(state.submitted),
  }),
}))
vi.mock('@/features/queries/results/useStoredAnalysis', () => ({
  useStoredAnalysis: () => ({ isLoading: false, hasBody: true, results: {} }),
}))
vi.mock('@/features/queries/results/AnalysisResults', () => ({
  AnalysisResults: () => <div>Saved result</div>,
}))

import { AnalysisTab } from './AnalysisTab'

afterEach(cleanup)

it.each([
  'own',
  'all',
] as const)('RV3-01 advances a submitted job from queued through running to done via %s', (source) => {
  const query = {
    key: 'abc',
    text: 'SELECT 1',
    text_status: 'ok',
    last_analysis: null,
  } as unknown as MonitorQuery
  state.submitted = {
    id: 7,
    query_key: 'abc',
    state: 'queued',
    enqueued_at: 100,
    started_at: null,
  } as AnalyzeJob
  state.own = []
  state.all = []
  const view = renderWithClient(
    <AnalysisTab query={query} target="db" onCompare={() => {}} />
  )
  fireEvent.click(screen.getAllByRole('button', { name: 'Analyze' })[0])
  expect(screen.getByText('Analysis queued')).toBeTruthy()
  state[source] = [{ ...state.submitted, state: 'running', started_at: 101 }]
  view.rerender(<AnalysisTab query={query} target="db" onCompare={() => {}} />)
  expect(screen.getByText('Analyzing now')).toBeTruthy()
  expect(
    screen.getByText('Reading the plan without running the query')
  ).toBeTruthy()
  state[source] = [{ ...state.submitted, state: 'done' }]
  view.rerender(
    <AnalysisTab
      query={{
        ...query,
        last_analysis: {
          state: 'done',
          mode: 'explain',
          param_source: null,
          reason: null,
          analysis_ref: 'abc/analysis/a1',
          finished_at: 102,
        },
      }}
      target="db"
      onCompare={() => {}}
    />
  )
  expect(screen.getByText('Saved result')).toBeTruthy()
  expect(screen.getByRole('button', { name: 'Analyze again' })).toBeTruthy()
  expect(screen.queryByText('Analysis queued')).toBeNull()
})
