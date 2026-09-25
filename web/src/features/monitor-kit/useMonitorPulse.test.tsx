import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import { resetStubState, setFixtureTiming } from '@/lib/monitor/api'
import { setFixtureOverride } from '@/lib/monitor/contract'
import { useMonitorQueries, useQuerySeries } from '@/lib/monitor/hooks'
import { monitorKeys } from '@/lib/monitor/keys'
import type { MonitorPulse } from '@/lib/monitor/types'
import { createTestQueryClient, queryClientWrapper } from '@/test-utils'
import { useMonitorPulse } from './useMonitorPulse'

const stream = vi.hoisted(() => ({
  emit: undefined as ((pulse: MonitorPulse) => void) | undefined,
  seriesReads: 0,
  queryReads: 0,
}))

vi.mock('@/lib/monitor/api', async (importOriginal) => {
  const api = await importOriginal<typeof import('@/lib/monitor/api')>()
  return {
    ...api,
    subscribePulse: (
      _target: string,
      onPulse: (pulse: MonitorPulse) => void
    ) => {
      stream.emit = onPulse
      return () => {
        stream.emit = undefined
      }
    },
    getQueries: (...args: Parameters<typeof api.getQueries>) => {
      stream.queryReads += 1
      return api.getQueries(...args)
    },
    getQuerySeries: (...args: Parameters<typeof api.getQuerySeries>) => {
      stream.seriesReads += 1
      return api.getQuerySeries(...args)
    },
  }
})

beforeEach(() => {
  setFixtureTiming(0)
  resetStubState()
  setFixtureOverride('localdev')
  stream.queryReads = 0
  stream.seriesReads = 0
  stream.emit = undefined
})

it('refetches an open query series on a collector pulse', async () => {
  const client = createTestQueryClient()
  const key = monitorKeys.series('localdev', 'open-query', '24h')
  const { result, unmount } = renderHook(
    () => {
      useMonitorPulse('localdev')
      return useQuerySeries('localdev', 'open-query', '24h')
    },
    { wrapper: queryClientWrapper(client) }
  )

  await waitFor(() => expect(result.current.isSuccess).toBe(true))
  expect(
    client.getQueryCache().find({ queryKey: key })?.getObserversCount()
  ).toBe(1)
  const before = stream.seriesReads

  act(() => {
    stream.emit?.({
      ts: 1_000,
      kind: 'collect',
      ok: true,
      detail: '',
      next_collect_at: 1_060,
      state: 'live',
    })
  })

  await waitFor(() => expect(stream.seriesReads).toBeGreaterThan(before))
  unmount()
  client.clear()
  setFixtureOverride(undefined)
})

it('refreshes the query list on successive readings without a click', async () => {
  const client = createTestQueryClient()
  const { result, unmount } = renderHook(
    () => {
      useMonitorPulse('localdev')
      return useMonitorQueries('localdev', '24h')
    },
    { wrapper: queryClientWrapper(client) }
  )
  await waitFor(() => expect(result.current.isSuccess).toBe(true))
  for (const ts of [2000, 2010, 2020]) {
    const before = stream.queryReads
    act(() =>
      stream.emit?.({
        ts,
        kind: 'collect',
        ok: true,
        detail: '',
        next_collect_at: ts + 10,
        state: 'live',
      })
    )
    await waitFor(() => expect(stream.queryReads).toBeGreaterThan(before))
  }
  const beforePoll = stream.queryReads
  act(() =>
    client.setQueryData(monitorKeys.pulse('localdev'), {
      pulses: [
        {
          ts: 2030,
          kind: 'collect',
          ok: true,
          detail: '',
          next_collect_at: 2040,
          state: 'live',
        },
      ],
    })
  )
  await waitFor(() => expect(stream.queryReads).toBeGreaterThan(beforePoll))
  unmount()
  client.clear()
  setFixtureOverride(undefined)
})
