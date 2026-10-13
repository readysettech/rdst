import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it } from 'vitest'
import { resetStubState, setFixtureTiming } from '@/lib/monitor/api'
import { setFixtureOverride } from '@/lib/monitor/contract'
import { queryClientWrapper } from '@/test-utils'
import { resetReportRuns, useReportRun } from './useReportRun'

beforeEach(() => {
  setFixtureTiming(0)
  setFixtureOverride('localdev')
  resetStubState()
  resetReportRuns()
})
afterEach(() => {
  setFixtureOverride(undefined)
  resetReportRuns()
})

it('runs Analyze database from queued to done with skipped and plan-only rows', async () => {
  const { result } = renderHook(() => useReportRun('localdev'), {
    wrapper: queryClientWrapper(),
  })
  await act(() => result.current.start())
  await waitFor(() => expect(result.current.status).toBe('done'))
  const states = result.current.view?.queries.map((row) => row.state) ?? []
  expect(states).toHaveLength(15)
  expect(states).toContain('skipped')
  expect(states).toContain('provisional')
  expect(states).toContain('failed')
  expect(result.current.active).toBe(false)
})

it('shares one run between consumers and cancels it', async () => {
  setFixtureTiming(0, 20)
  const wrapper = queryClientWrapper()
  const a = renderHook(() => useReportRun('localdev'), { wrapper })
  const b = renderHook(() => useReportRun('localdev'), { wrapper })
  await act(() => a.result.current.start())
  await waitFor(() => expect(b.result.current.status).toBe('running'))
  expect(b.result.current.reportId).toBe(a.result.current.reportId)
  await act(() => b.result.current.cancel())
  await waitFor(() => expect(a.result.current.status).toBe('cancelled'))
})
