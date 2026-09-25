import { cleanup, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { resetStubState, setFixtureTiming } from '@/lib/monitor/api'
import { setFixtureOverride } from '@/lib/monitor/contract'
import type { LastAnalysis, MonitorQuery } from '@/lib/monitor/types'
import { renderWithClient } from '@/test-utils'
import { AnalysisTab, analysisModeLabel } from './AnalysisTab'

const done = (mode: string, extra: Partial<LastAnalysis> = {}) =>
  ({
    state: 'done',
    finished_at: Date.now() / 1000 - 120,
    analysis_ref: null,
    mode,
    param_source: null,
    reason: null,
    ...extra,
  }) as LastAnalysis

beforeEach(() => {
  setFixtureTiming(0)
  resetStubState()
  setFixtureOverride('localdev')
})

afterEach(() => {
  cleanup()
  setFixtureOverride(undefined)
})

describe('analysisModeLabel', () => {
  it('prefers the backend label and falls back to the mode', () => {
    expect(analysisModeLabel(done('explain', { mode_label: 'X' }))).toBe('X')
    expect(analysisModeLabel(done('explain'))).toBe('Plan only (EXPLAIN)')
    expect(analysisModeLabel(done('analyze'))).toBe(
      'Ran the query (EXPLAIN ANALYZE)'
    )
    expect(analysisModeLabel({ ...done('explain'), state: 'failed' })).toBe(
      null
    )
    expect(analysisModeLabel(null)).toBeNull()
  })
})

describe('AnalysisTab', () => {
  it('labels a plan-only analysis', async () => {
    const query = {
      key: 'abc',
      text: 'SELECT 1',
      text_status: 'ok',
      last_analysis: done('explain', { mode_label: 'Plan only (EXPLAIN)' }),
    } as MonitorQuery
    renderWithClient(
      <AnalysisTab query={query} target="localdev" onCompare={() => {}} />
    )
    expect(await screen.findByText('Plan only (EXPLAIN)')).toBeTruthy()
    expect(screen.getByText(/no measured timings/)).toBeTruthy()
  })

  it('says why an UPDATE is not analyzed instead of waiting on a job', () => {
    const query = {
      key: 'upd',
      text: 'UPDATE payments SET status = $1 WHERE provider_ref = $2',
      text_status: 'ok',
      last_analysis: { state: 'queued' } as LastAnalysis,
    } as MonitorQuery
    renderWithClient(
      <AnalysisTab query={query} target="localdev" onCompare={() => {}} />
    )
    expect(
      screen.getByText(
        "RDST doesn't analyze UPDATE statements because they change data."
      )
    ).toBeTruthy()
    expect(screen.queryByText('Wait for analysis to start')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Analyze' })).toBeNull()
  })
})
