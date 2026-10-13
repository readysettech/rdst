import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useDefaultTarget, useTarget } from './useTarget'

const status = vi.hoisted(() => ({
  data: undefined as
    | { targets: { name: string }[]; default_target: string | null }
    | undefined,
}))
vi.mock('../lib/useSystemStatus', () => ({
  useSystemStatus: () => ({
    data: status.data,
    isLoading: !status.data,
    refetch: vi.fn(),
    dataUpdatedAt: 1,
  }),
}))

describe('useTarget', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  afterEach(() => {
    cleanup()
    localStorage.clear()
  })

  it('restores a target from localStorage without starting Readyset work', () => {
    localStorage.setItem('rdst_selected_target', 'tpcds-sf100')

    const { result } = renderHook(() => useTarget())

    expect(result.current.target).toBe('tpcds-sf100')
  })

  it('persists and clears the selected target', () => {
    const { result } = renderHook(() => useTarget())

    act(() => result.current.setTarget('imdb'))
    expect(result.current.target).toBe('imdb')
    expect(localStorage.getItem('rdst_selected_target')).toBe('imdb')

    act(() => result.current.setTarget(null))
    expect(result.current.target).toBeNull()
    expect(localStorage.getItem('rdst_selected_target')).toBeNull()
  })
})

describe('useDefaultTarget', () => {
  afterEach(() => {
    cleanup()
    localStorage.clear()
    status.data = undefined
  })

  it('selects the default database when nothing is selected', () => {
    status.data = {
      targets: [{ name: 'analytics' }, { name: 'demo' }],
      default_target: 'demo',
    }
    const { result } = renderHook(() => ({
      resolved: useDefaultTarget(),
      selected: useTarget().target,
    }))
    expect(result.current.resolved).toBe('demo')
    expect(result.current.selected).toBe('demo')
    expect(localStorage.getItem('rdst_selected_target')).toBe('demo')
  })

  it('keeps a selection that names a configured database', () => {
    localStorage.setItem('rdst_selected_target', 'analytics')
    status.data = {
      targets: [{ name: 'analytics' }, { name: 'demo' }],
      default_target: 'demo',
    }
    const { result } = renderHook(() => useDefaultTarget())
    expect(result.current).toBe('analytics')
    expect(localStorage.getItem('rdst_selected_target')).toBe('analytics')
  })
})
