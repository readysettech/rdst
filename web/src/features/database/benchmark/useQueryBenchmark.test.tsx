import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import type { MonitorQuery } from '@/lib/monitor/types'
import { useQueryBenchmark } from './useQueryBenchmark'

const mocks = vi.hoisted(() => ({
  controller: vi.fn(),
  selected: vi.fn(),
  setRunChoice: vi.fn(),
}))
vi.mock('@/features/caching/load-test/useLoadTestController', () => ({
  useLoadTestController: mocks.controller,
}))
vi.mock('@/features/caching/sandbox', () => ({
  fetchSandboxDiagnostics: async () => ({
    docker_installed: false,
    docker_running: false,
  }),
}))
vi.mock('@/lib/backgroundRuns', () => ({
  useBackgroundRuns: () => [],
  cancelBackgroundRun: vi.fn(),
}))
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

it('preselects the opened query and keeps both presets on the shared runner', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({ ok: true, json: async () => ({ runs: [] }) })
  )
  const controller = {
    queries: [{ hash: 'abc', tag: 'Orders', sql: 'SELECT 1' }],
    registryLoading: false,
    listError: null,
    setSelectedQueries: mocks.selected,
    selectedQueryById: new Map(),
    runChoice: 'source',
    setRunChoice: mocks.setRunChoice,
    setTestProfile: vi.fn(),
    setDurationSeconds: vi.fn(),
    setIntervalMs: vi.fn(),
    setCapacityClients: vi.fn(),
    setComparative: vi.fn(),
    destinationLock: { isLocked: false },
    connectivity: { isChecking: false },
    runnableCount: 1,
    missingParameterCount: 0,
    canStart: true,
    handleConfirmRun: vi.fn().mockResolvedValue(undefined),
  }
  mocks.controller.mockReturnValue(controller)
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
  const query = {
    key: 'abc',
    text: 'SELECT 1',
    text_status: 'ok',
    display_name: 'Orders',
  } as MonitorQuery
  const { result } = renderHook(() => useQueryBenchmark(query, 'shop'), {
    wrapper,
  })
  await waitFor(() => expect(mocks.selected).toHaveBeenCalledWith(['Orders']))
  expect(controller.setDurationSeconds).toHaveBeenLastCalledWith(30)
  act(() => result.current.setPresetId('standard'))
  expect(controller.setDurationSeconds).toHaveBeenLastCalledWith(120)
  expect(controller.setTestProfile).toHaveBeenLastCalledWith('capacity')
  await act(async () => result.current.start())
  expect(controller.handleConfirmRun).toHaveBeenCalledOnce()
})
