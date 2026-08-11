import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const fleetStatus = vi.hoisted(() => ({
  check: vi.fn(),
  state: 'idle' as const,
  results: {},
  error: undefined,
  reset: vi.fn(),
}))

vi.mock('./useFleet', () => ({
  useFleetStatus: () => fleetStatus,
}))

import { useTargetConnectivityGate } from './useTargetConnectivityGate'

beforeEach(() => {
  fleetStatus.check.mockReset()
  fleetStatus.reset.mockReset()
})

describe('useTargetConnectivityGate', () => {
  it('allows the request only after the selected database is reachable', async () => {
    fleetStatus.check.mockResolvedValue({
      prod: {
        type: 'connectivity',
        target_name: 'prod',
        status: 'ok',
      },
    })
    const { result } = renderHook(() => useTargetConnectivityGate('prod'))

    let reachable = false
    await act(async () => {
      reachable = await result.current.ensureReachable()
    })

    expect(reachable).toBe(true)
    expect(fleetStatus.check).toHaveBeenCalledWith(undefined, ['prod'])
    expect(result.current.failure).toBeNull()
  })

  it('preserves a categorized failure and stops the request', async () => {
    fleetStatus.check.mockResolvedValue({
      prod: {
        type: 'connectivity',
        target_name: 'prod',
        status: 'failed',
        error: 'connection refused',
        category: 'database_unreachable',
      },
    })
    const { result } = renderHook(() => useTargetConnectivityGate('prod'))

    let reachable = true
    await act(async () => {
      reachable = await result.current.ensureReachable()
    })

    expect(reachable).toBe(false)
    expect(result.current.failure).toEqual({
      target: 'prod',
      message: 'connection refused',
      category: 'database_unreachable',
      code: undefined,
    })
  })
})
