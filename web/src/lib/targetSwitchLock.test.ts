import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import {
  __resetTargetSwitchLockForTests,
  useTargetSwitchLock,
  useTargetSwitchLockState,
} from './targetSwitchLock'

describe('targetSwitchLock', () => {
  it('toggles lock state for a single lock acquire and release', async () => {
    __resetTargetSwitchLockForTests()

    const { result, rerender } = renderHook(
      ({ active }: { active: boolean }) => {
        useTargetSwitchLock('ask', active)
        return useTargetSwitchLockState()
      },
      { initialProps: { active: true } }
    )

    await waitFor(() => {
      expect(result.current.isLocked).toBe(true)
      expect(result.current.message).toBe(
        'Target switching is disabled while Ask is in progress.'
      )
    })

    rerender({ active: false })

    await waitFor(() => {
      expect(result.current.isLocked).toBe(false)
      expect(result.current.message).toBe('')
    })
  })

  it('stays locked until all concurrent locks are released', async () => {
    __resetTargetSwitchLockForTests()

    const { result, rerender } = renderHook(
      ({ askActive, topActive }: { askActive: boolean; topActive: boolean }) => {
        useTargetSwitchLock('ask', askActive)
        useTargetSwitchLock('top', topActive)
        return useTargetSwitchLockState()
      },
      { initialProps: { askActive: true, topActive: true } }
    )

    await waitFor(() => {
      expect(result.current.isLocked).toBe(true)
      expect(result.current.message).toBe(
        'Target switching is disabled while multiple operations are in progress.'
      )
    })

    rerender({ askActive: false, topActive: true })

    await waitFor(() => {
      expect(result.current.isLocked).toBe(true)
      expect(result.current.message).toBe(
        'Target switching is disabled while Top Queries is in progress.'
      )
    })

    rerender({ askActive: false, topActive: false })

    await waitFor(() => {
      expect(result.current.isLocked).toBe(false)
      expect(result.current.message).toBe('')
    })
  })

  it('maintains reference counts for repeated locks of the same reason', async () => {
    __resetTargetSwitchLockForTests()

    const { result, rerender } = renderHook(
      ({ lockA, lockB }: { lockA: boolean; lockB: boolean }) => {
        useTargetSwitchLock('schema', lockA)
        useTargetSwitchLock('schema', lockB)
        return useTargetSwitchLockState()
      },
      { initialProps: { lockA: true, lockB: true } }
    )

    await waitFor(() => {
      expect(result.current.isLocked).toBe(true)
      expect(result.current.message).toBe(
        'Target switching is disabled while Schema operations are in progress.'
      )
    })

    rerender({ lockA: false, lockB: true })

    await waitFor(() => {
      expect(result.current.isLocked).toBe(true)
      expect(result.current.message).toBe(
        'Target switching is disabled while Schema operations are in progress.'
      )
    })

    rerender({ lockA: false, lockB: false })

    await waitFor(() => {
      expect(result.current.isLocked).toBe(false)
      expect(result.current.message).toBe('')
    })
  })

  it('clears all lock state for tests', () => {
    const { result } = renderHook(() => {
      useTargetSwitchLock('benchmark', true)
      return useTargetSwitchLockState()
    })

    expect(result.current.isLocked).toBe(true)

    act(() => {
      __resetTargetSwitchLockForTests()
    })

    expect(result.current.isLocked).toBe(false)
    expect(result.current.message).toBe('')
  })
})
