import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { createTestQueryClient, queryClientWrapper } from '@/test-utils'
import { resetStubState, setFixtureTiming } from './api'
import { setFixtureOverride } from './contract'
import { useUpdateMonitorSettings } from './hooks'

afterEach(() => setFixtureOverride(undefined))

it('refreshes Home, which the sidebar reads, after a settings change', async () => {
  setFixtureTiming(0)
  resetStubState()
  setFixtureOverride('ca-database')
  const client = createTestQueryClient()
  const invalidate = vi.spyOn(client, 'invalidateQueries')
  const { result } = renderHook(() => useUpdateMonitorSettings('demo'), {
    wrapper: queryClientWrapper(client),
  })
  act(() => result.current.mutate({ enabled: false }))
  await waitFor(() =>
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['monitor', 'home'] })
  )
})
