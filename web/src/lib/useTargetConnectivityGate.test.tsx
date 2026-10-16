import { act, render, renderHook } from '@testing-library/react'
import { useEffect } from 'react'
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

  it('reports nothing for a check a newer one replaced', async () => {
    const finishes: ((value: unknown) => void)[] = []
    fleetStatus.check.mockImplementation(
      () => new Promise((resolve) => finishes.push(resolve))
    )
    const { result } = renderHook(() => useTargetConnectivityGate('prod'))

    let first: Promise<boolean> = Promise.resolve(true)
    let second: Promise<boolean> = Promise.resolve(false)
    act(() => {
      first = result.current.ensureReachable()
      second = result.current.ensureReachable()
    })
    await act(async () => {
      // The replaced check is aborted and comes back empty.
      finishes[0]({})
      finishes[1]({
        prod: { type: 'connectivity', target_name: 'prod', status: 'ok' },
      })
    })

    expect(await first).toBe(false)
    expect(await second).toBe(true)
    expect(result.current.failure).toBeNull()
  })

  it('keeps a check started the moment a database is chosen', async () => {
    fleetStatus.check.mockImplementation(async (_group, [target]) => ({
      [target]: { type: 'connectivity', target_name: target, status: 'ok' },
    }))
    const outcomes: boolean[] = []
    // The request goes from a child's effect, which runs before the gate sees the switch.
    function Sender({ ensure }: { ensure: () => Promise<boolean> }) {
      useEffect(() => {
        void ensure().then((ok) => outcomes.push(ok))
      }, [ensure])
      return null
    }
    function Page({ target }: { target: string }) {
      const gate = useTargetConnectivityGate(target)
      return target === 'prod' ? <Sender ensure={gate.ensureReachable} /> : null
    }
    const view = render(<Page target="stage" />)
    fleetStatus.reset.mockClear()
    await act(async () => view.rerender(<Page target="prod" />))

    expect(fleetStatus.reset).not.toHaveBeenCalled()
    expect(outcomes).toEqual([true])
  })
})
